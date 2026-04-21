import csv
import traceback
from datetime import datetime
from pathlib import Path

from common.export_store import (
    read_latest_snapshot_to_dataframe,
    read_table_to_dataframe,
    write_rows_to_table,
)
from common.platform_store import (
    replace_video_rows_for_uploader,
    upsert_creator_rows,
)
from bilibili_analyzer.feishu_uploader import FeishuUploader
from bilibili_analyzer.logging_utils import (
    create_progress,
    create_summary_panel,
    get_console,
    setup_logging,
)

from .analyzer import DouyinHiatusAnalyzer
from .browser_client import DouyinBrowserClient
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config
from .exporters import save_video_duration_analysis_to_csv
from .playwright_browser_client import PlaywrightDouyinBrowserClient


def create_douyin_browser_client(config):
    backend = str(getattr(config, "browser_backend", "drission") or "drission").strip().lower()
    if backend == "playwright":
        return PlaywrightDouyinBrowserClient(config)
    return DouyinBrowserClient(config)


def load_unfollow_targets(list_path):
    path = Path(list_path)
    if not path.exists():
        get_console().print(create_summary_panel("Unfollow List Missing", [str(path)], border_style="red"))
        return []

    targets = []
    with path.open("r", encoding="utf-8") as unfollow_file:
        for line in unfollow_file:
            text = line.strip()
            if text and not text.startswith("#"):
                targets.append(text)
    return targets


def load_uid_targets(list_path):
    path = Path(list_path)
    if not path.exists():
        get_console().print(create_summary_panel("UID List Missing", [str(path)], border_style="red"))
        return []

    targets = []
    with path.open("r", encoding="utf-8") as uid_file:
        for line in uid_file:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            candidate = text.split(",", 1)[0].split()[0].strip()
            if candidate:
                targets.append(candidate)
    return list(dict.fromkeys(targets))


def write_uid_fetch_outputs(config, video_rows, summary_rows):
    output_dir = config.output_csv.parent
    videos_path = output_dir / "douyin_uid_all_videos.csv"
    summary_path = output_dir / "douyin_uid_fetch_summary.csv"

    video_fieldnames = [
        "uploader_name",
        "uploader_id",
        "video_title",
        "aweme_id",
        "publish_date",
        "publish_timestamp",
        "duration_text",
        "duration_seconds",
        "duration_category",
        "like_count",
        "view_count",
        "video_url",
    ]
    video_headers = {
        "uploader_name": "UP主姓名",
        "uploader_id": "UP主UID",
        "video_title": "视频标题",
        "aweme_id": "视频ID",
        "publish_date": "发布日期",
        "publish_timestamp": "发布时间戳",
        "duration_text": "视频时长",
        "duration_seconds": "视频时长(秒)",
        "duration_category": "时长分类",
        "like_count": "点赞数",
        "view_count": "播放量",
        "video_url": "视频链接",
    }

    summary_fieldnames = [
        "target_uid",
        "uploader_name",
        "uploader_homepage",
        "follower_count",
        "published_video_count",
        "video_count",
        "status",
        "last_publish_date",
        "fetched_at",
        "message",
    ]
    summary_headers = {
        "target_uid": "目标UID",
        "uploader_name": "UP主姓名",
        "uploader_homepage": "UP主主页链接",
        "follower_count": "粉丝数",
        "published_video_count": "发布视频数量",
        "video_count": "本次抓取视频数",
        "status": "抓取状态",
        "last_publish_date": "最新发布日期",
        "fetched_at": "抓取时间",
        "message": "说明",
    }

    videos_path.parent.mkdir(parents=True, exist_ok=True)
    with videos_path.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=video_fieldnames, extrasaction="ignore")
        writer.writerow(video_headers)
        writer.writerows(video_rows)

    with summary_path.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=summary_fieldnames, extrasaction="ignore")
        writer.writerow(summary_headers)
        writer.writerows(summary_rows)

    creator_rows = [
        {
            "UP主UID": row.get("target_uid", ""),
            "UP主姓名": row.get("uploader_name", ""),
            "UP主主页链接": row.get("uploader_homepage", ""),
            "粉丝数": row.get("follower_count", ""),
            "发布视频数量": row.get("published_video_count", ""),
            "视频总数": row.get("video_count", ""),
            "抓取状态": row.get("status", ""),
            "最近抓取时间": row.get("fetched_at", ""),
        }
        for row in summary_rows
    ]
    upsert_creator_rows(config.export_store_db, "douyin", creator_rows, source_mode="uid")

    grouped_rows = {}
    for row in video_rows or []:
        uploader_id = str((row or {}).get("uploader_id") or "").strip()
        if not uploader_id:
            continue
        grouped_rows.setdefault(uploader_id, []).append(row)
    for uploader_id, rows in grouped_rows.items():
        replace_video_rows_for_uploader(
            config.export_store_db,
            "douyin",
            uploader_id,
            rows,
            "aweme_id",
        )

    return videos_path, summary_path


def write_uid_analysis_output(config, analysis_rows):
    output_dir = config.output_csv.parent
    analysis_path = output_dir / "douyin_uid_video_duration_analysis.csv"
    fieldnames = [
        "uploader_name",
        "uploader_id",
        "follower_count",
        "total_videos",
        "total_duration_seconds",
        "average_duration_seconds",
        "average_duration_text",
        "average_like_count",
        "average_update_interval_days",
        "short_video_count",
        "short_video_ratio",
        "medium_video_count",
        "medium_video_ratio",
        "medium_long_video_count",
        "medium_long_video_ratio",
        "long_video_count",
        "long_video_ratio",
    ]
    chinese_headers = {
        "uploader_name": "UP主姓名",
        "uploader_id": "UP主UID",
        "follower_count": "粉丝数",
        "total_videos": "视频总数",
        "total_duration_seconds": "总时长(秒)",
        "average_duration_seconds": "平均时长(秒)",
        "average_duration_text": "平均时长",
        "average_like_count": "平均点赞数",
        "average_update_interval_days": "平均几天一更",
        "short_video_count": "短视频数量(0~30s)",
        "short_video_ratio": "短视频占比",
        "medium_video_count": "中视频数量(30~60s)",
        "medium_video_ratio": "中视频占比",
        "medium_long_video_count": "中长视频数量(60~240s)",
        "medium_long_video_ratio": "中长视频占比",
        "long_video_count": "长视频数量(240s+)",
        "long_video_ratio": "长视频占比",
    }

    analysis_path.parent.mkdir(parents=True, exist_ok=True)
    with analysis_path.open("w", newline="", encoding="utf-8-sig") as csvfile:
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
        writer.writerow(chinese_headers)
        writer.writerows(analysis_rows)
    write_rows_to_table(
        config.export_store_db,
        config.export_uid_analysis_table,
        fieldnames,
        chinese_headers,
        analysis_rows,
    )
    return analysis_path


def remove_unfollow_target(list_path, homepage):
    path = Path(list_path)
    if not path.exists():
        return

    normalized_homepage = DouyinBrowserClient.normalize_homepage_url(homepage)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    updated_lines = []
    removed = False
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            updated_lines.append(line)
            continue
        normalized_line = DouyinBrowserClient.normalize_homepage_url(raw)
        if not removed and normalized_line == normalized_homepage:
            removed = True
            continue
        updated_lines.append(line)

    if removed:
        path.write_text("\n".join(updated_lines) + ("\n" if updated_lines else ""), encoding="utf-8")


def remove_unfollowed_local_state(config, homepage):
    cache_store = CacheStore(config)
    removed_uids = cache_store.remove_unfollowed_user(homepage=homepage)
    if removed_uids:
        get_console().print(
            create_summary_panel(
                "Douyin Local State Cleaned",
                [
                    f"Removed UID count: {len(removed_uids)}",
                    f"UIDs: {', '.join(removed_uids[:5])}" + (" ..." if len(removed_uids) > 5 else ""),
                ],
                border_style="green",
            )
        )
    return removed_uids


def run_partial_feishu_upload(processed_count):
    run_feishu_upload(prune_missing=False)


def run_cached_feishu_preupload(fetch_mode_override=None):
    config = load_analyzer_config(fetch_mode_override=fetch_mode_override)
    cache_store = CacheStore(config)
    if cache_store.is_followings_cache_expired():
        get_console().print(
            create_summary_panel(
                "Douyin Cached Preupload Skipped",
                [
                    "Followings cache is expired.",
                    "Cached preupload was skipped to avoid pushing stale unfollowed creators.",
                ],
                border_style="yellow",
            )
        )
        return False
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client=None,
        cache_store=cache_store,
        upload_callback=None,
    )
    if not analyzer.export_cached_snapshot():
        return False

    get_console().print(
        create_summary_panel(
            "Douyin Cached Snapshot",
            ["Cached main-sheet snapshot will be uploaded before the live crawl starts."],
            border_style="cyan",
        )
    )
    run_feishu_upload(prune_missing=False)
    return True


def run_analysis(trigger_upload=True, fetch_mode_override=None):
    config = load_analyzer_config(fetch_mode_override=fetch_mode_override)
    setup_logging(config.log_dir, "douyin_app")

    fetch_mode = (config.fetch_mode or "monitor").strip().lower()
    enable_partial_upload = trigger_upload and fetch_mode != "counts"

    browser_client = create_douyin_browser_client(config)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client,
        cache_store,
        upload_callback=run_partial_feishu_upload if enable_partial_upload else None,
    )

    if enable_partial_upload:
        try:
            run_cached_feishu_preupload(fetch_mode_override=fetch_mode_override)
        except Exception as exc:
            get_console().print(
                create_summary_panel(
                    "Preupload Skipped",
                    [f"Cached preupload failed but the crawl will continue: {exc}"],
                    border_style="yellow",
                )
            )
    elif trigger_upload and fetch_mode == "counts":
        get_console().print(
            create_summary_panel(
                "Douyin Counts Mode",
                ["Counts mode will upload only once after the full crawl finishes."],
                border_style="cyan",
            )
        )

    try:
        results = analyzer.analyze_hiatus()
    finally:
        browser_client.close()

    if trigger_upload and results is not None:
        get_console().print(
            create_summary_panel(
                "Douyin Main Sheet Sync",
                ["Analysis finished. Main data sheet will be synced now."],
                border_style="cyan",
            )
        )
        run_feishu_upload(prune_missing=True)

    return results


def run_feishu_upload(prune_missing=True):
    config = load_feishu_config()
    setup_logging(config.log_dir, "douyin_feishu_upload")

    analyzer_config = load_analyzer_config()
    cache_store = CacheStore(analyzer_config)
    if cache_store.is_followings_cache_expired():
        get_console().print(
            create_summary_panel(
                "Douyin Upload Blocked",
                [
                    "Followings cache is expired.",
                    "Upload was blocked to avoid syncing stale followed creators to Feishu.",
                    "Please run a fresh Douyin crawl before uploading the main sheet again.",
                ],
                border_style="red",
            )
        )
        return False

    analyzer = DouyinHiatusAnalyzer(
        analyzer_config,
        browser_client=None,
        cache_store=cache_store,
        upload_callback=None,
    )
    analyzer.export_cached_snapshot()

    uploader = FeishuUploader(config)
    uploader.run(prune_missing=prune_missing)
    return True


def run_uid_analysis_upload(csv_path, target_uids=None):
    config = load_feishu_config()
    setup_logging(config.log_dir, "douyin_uid_analysis_upload")
    _show_uid_analysis_status_panel(config, csv_path, target_uids=target_uids)
    uploader = FeishuUploader(config)
    uploader.run_single_table(
        config.export_uid_analysis_table,
        csv_fallback_path=csv_path,
        sheet_title=config.analysis_sheet_title,
        sheet_index=config.analysis_sheet_index,
        upload_state_json=config.analysis_upload_state_json,
        panel_title="Douyin UID Analysis Synced",
    )


def _load_uid_analysis_dataframe(config):
    dataframe = read_latest_snapshot_to_dataframe(config.export_store_db, config.export_uid_analysis_table)
    source = "sqlite snapshot"
    if dataframe is None:
        dataframe = read_table_to_dataframe(config.export_store_db, config.export_uid_analysis_table)
        source = "sqlite current"
    return dataframe, source


def _show_uid_analysis_status_panel(config, csv_path, target_uids=None):
    dataframe, source = _load_uid_analysis_dataframe(config)

    lines = [
        f"Target sheet: {config.analysis_sheet_title}",
        f"SQLite table: {config.export_uid_analysis_table}",
        f"Source: {source if dataframe is not None else 'csv fallback pending'}",
    ]

    if dataframe is not None:
        lines.append(f"Prepared rows: {len(dataframe.index)}")
        lines.append(f"Prepared columns: {len(dataframe.columns)}")
    else:
        lines.append("Prepared rows: 0")
        lines.append("Prepared columns: 0")

    if target_uids is not None:
        target_uid_set = {str(uid).strip() for uid in target_uids if str(uid).strip()}
        matched_count = 0
        if dataframe is not None and "UP主UID" in dataframe.columns:
            matched_count = dataframe["UP主UID"].astype(str).str.strip().isin(target_uid_set).sum()
        lines.append(f"Target UID count: {len(target_uid_set)}")
        lines.append(f"Matched in SQLite: {matched_count}")

    csv_path = Path(csv_path)
    lines.append(f"Fallback CSV: {csv_path.name}")
    lines.append(f"CSV exists: {'yes' if csv_path.exists() else 'no'}")

    get_console().print(
        create_summary_panel(
            "Douyin UID Analysis Ready",
            lines,
            border_style="cyan",
        )
    )


def run_unfollow(list_path):
    config = load_analyzer_config(fetch_mode_override="counts")
    setup_logging(config.log_dir, "douyin_unfollow")

    targets = load_unfollow_targets(list_path)
    if not targets:
        get_console().print(create_summary_panel("Douyin Unfollow", ["No valid homepage found in list."], border_style="yellow"))
        return []

    browser_client = create_douyin_browser_client(config)
    try:
        browser_client.ensure_login()
        results = browser_client.unfollow_users_by_homepages(
            targets,
            on_unfollowed=lambda homepage: (
                remove_unfollow_target(list_path, homepage),
                remove_unfollowed_local_state(config, homepage),
            ),
        )
    finally:
        browser_client.close()

    unfollowed = sum(1 for item in results if item.get("status") == "unfollowed")
    skipped = sum(1 for item in results if item.get("status") == "skipped")
    failed = sum(1 for item in results if item.get("status") not in {"unfollowed", "skipped"})

    get_console().print(
        create_summary_panel(
            "Douyin Unfollow Finished",
            [
                f"Targets: {len(results)}",
                f"Unfollowed: {unfollowed}",
                f"Already not followed: {skipped}",
                f"Failed: {failed}",
            ],
            border_style="green",
        )
    )
    return results


def run_fetch_uid_videos(list_path):
    config = load_analyzer_config(fetch_mode_override="full")
    setup_logging(config.log_dir, "douyin_uid_fetch")

    targets = load_uid_targets(list_path)
    if not targets:
        get_console().print(create_summary_panel("Douyin UID Fetch", ["No valid UID found in list."], border_style="yellow"))
        return []

    browser_client = create_douyin_browser_client(config)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client,
        cache_store,
        upload_callback=None,
    )
    all_video_rows = []
    summary_rows = []
    analysis_rows = []

    try:
        browser_client.ensure_login()
        with create_progress(transient=False) as progress:
            task_id = progress.add_task("Fetch Douyin UID videos", total=len(targets))
            for uid in targets:
                progress.update(task_id, description=f"Fetch Douyin UID videos | current UID: {uid}")
                fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                user = {
                    "sec_uid": uid,
                    "nickname": f"UID_{uid}",
                    "homepage": f"https://www.douyin.com/user/{uid}",
                    "remark_name": "",
                    "follower_count": "",
                    "aweme_count": "",
                }
                try:
                    videos = browser_client.get_all_videos_for_user(user)
                    all_video_rows.extend(videos)
                    analysis_rows.append(analyzer.build_video_duration_summary(user, videos))
                    summary_rows.append(
                        {
                            "target_uid": uid,
                            "uploader_name": user["nickname"],
                            "uploader_homepage": user["homepage"],
                            "follower_count": user.get("follower_count", ""),
                            "published_video_count": user.get("aweme_count", ""),
                            "video_count": len(videos),
                            "status": "success" if videos else "no_video",
                            "last_publish_date": videos[0].get("publish_date", "") if videos else "",
                            "fetched_at": fetched_at,
                            "message": "",
                        }
                    )
                except Exception as exc:
                    analysis_rows.append(analyzer.build_empty_summary(user))
                    summary_rows.append(
                        {
                            "target_uid": uid,
                            "uploader_name": user["nickname"],
                            "uploader_homepage": user["homepage"],
                            "follower_count": user.get("follower_count", ""),
                            "published_video_count": user.get("aweme_count", ""),
                            "video_count": 0,
                            "status": "failed",
                            "last_publish_date": "",
                            "fetched_at": fetched_at,
                            "message": str(exc),
                        }
                    )

                write_uid_fetch_outputs(config, all_video_rows, summary_rows)
                write_uid_analysis_output(config, analysis_rows)
                progress.advance(task_id)
    finally:
        browser_client.close()

    videos_path, summary_path = write_uid_fetch_outputs(config, all_video_rows, summary_rows)
    analysis_path = write_uid_analysis_output(config, analysis_rows)
    get_console().print(
        create_summary_panel(
            "Douyin UID Fetch Finished",
            [
                f"UID count: {len(targets)}",
                f"Video rows: {len(all_video_rows)}",
                f"Video detail CSV: {videos_path.name}",
                f"Fetch summary CSV: {summary_path.name}",
                f"Analysis CSV: {analysis_path.name}",
                f"Target sheet: {load_feishu_config().analysis_sheet_title}",
            ],
            border_style="green",
        )
    )
    run_uid_analysis_upload(analysis_path, target_uids=targets)
    return summary_rows


def main(fetch_mode_override=None):
    try:
        run_analysis(trigger_upload=True, fetch_mode_override=fetch_mode_override)
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Execution was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Douyin Error", [str(exc)], border_style="red"))
        traceback.print_exc()


def upload_main():
    try:
        run_feishu_upload()
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Upload was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Upload Error", [str(exc)], border_style="red"))
        traceback.print_exc()
