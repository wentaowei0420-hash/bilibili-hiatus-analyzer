import json
import os
import sqlite3
import traceback
from datetime import datetime
from pathlib import Path

from common.export_store import (
    read_latest_snapshot_to_dataframe,
    read_table_to_dataframe,
    upsert_rows_to_table,
)
from common.file_io import atomic_write_csv, atomic_write_text
from common.platform_store import (
    replace_video_rows_for_uploader,
    upsert_cache_entries,
    upsert_creator_rows,
    upsert_video_state_rows,
)
from common.runtime_control import OperationCancelled, check_stop
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
from .exporters import save_cache_inventory_to_csv, save_video_duration_analysis_to_csv
from .playwright_browser_client import PlaywrightDouyinBrowserClient
from .rating.store import rating_store_db_path, source_store_db_path
from .utils import parse_view_count


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


def merge_douyin_profile(profile_index, user):
    if not isinstance(user, dict):
        return
    uid = str(user.get("sec_uid") or user.get("uploader_id") or "").strip()
    if not uid:
        return

    existing = profile_index.get(uid, {})
    merged = dict(existing)
    for key, value in user.items():
        if value not in (None, ""):
            merged[key] = value
    profile_index[uid] = merged


def load_douyin_uid_profile_index(cache_store):
    profile_index = {}

    for user in cache_store.load_followings_cache():
        merge_douyin_profile(profile_index, user)

    progress = cache_store.load_progress()
    for uid, entry in (progress or {}).items():
        if not isinstance(entry, dict):
            continue
        user = entry.get("user", {}) if isinstance(entry.get("user"), dict) else {}
        summary = entry.get("summary", {}) if isinstance(entry.get("summary"), dict) else {}
        merge_douyin_profile(profile_index, {**summary, **user, "sec_uid": user.get("sec_uid") or uid})

    return profile_index


def _parse_bool_env(name, default=True):
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def get_douyin_uid_fetch_order():
    labels = {
        "follower_count": "\u7c89\u4e1d\u6570",
        "published_video_count": "\u89c6\u9891\u603b\u6570",
        "total_favorited": "\u83b7\u8d5e\u603b\u6570",
        "average_like_count": "\u5e73\u5747\u70b9\u8d5e\u6570",
    }
    field = str(os.getenv("DOUYIN_FETCH_ORDER_BY", "follower_count") or "follower_count").strip()
    if field not in labels:
        field = "follower_count"
    descending = _parse_bool_env("DOUYIN_FETCH_ORDER_DESC", True)
    return field, descending, labels[field]


def _resolve_douyin_profile_sort_value(profile, field):
    profile = profile if isinstance(profile, dict) else {}
    if field == "published_video_count":
        return parse_view_count(profile.get("aweme_count") or profile.get("published_video_count") or profile.get("total_videos"))
    if field == "total_favorited":
        return parse_view_count(profile.get("total_favorited"))
    if field == "average_like_count":
        total_favorited = parse_view_count(profile.get("total_favorited"))
        video_count = parse_view_count(profile.get("aweme_count") or profile.get("published_video_count") or profile.get("total_videos"))
        return int(total_favorited / video_count) if total_favorited > 0 and video_count > 0 else 0
    return parse_view_count(profile.get("follower_count"))


def sort_uid_targets_by_follower_count(targets, profile_index, order_field="follower_count", descending=True):
    def sort_key(uid):
        value = _resolve_douyin_profile_sort_value((profile_index.get(str(uid), {}) or {}), order_field)
        primary = -value if descending else value
        return (primary, str(uid))

    return sorted(targets, key=sort_key)


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
        "total_favorited",
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
        "total_favorited": "获赞总数",
        "published_video_count": "发布视频数量",
        "video_count": "本次抓取视频数",
        "status": "抓取状态",
        "last_publish_date": "最新发布日期",
        "fetched_at": "抓取时间",
        "message": "说明",
    }

    atomic_write_csv(videos_path, video_fieldnames, video_rows, header_row=video_headers)
    atomic_write_csv(summary_path, summary_fieldnames, summary_rows, header_row=summary_headers)

    creator_rows = [
        {
            "UP主UID": row.get("target_uid", ""),
            "UP主姓名": row.get("uploader_name", ""),
            "UP主主页链接": row.get("uploader_homepage", ""),
            "粉丝数": row.get("follower_count", ""),
            "获赞总数": row.get("total_favorited", ""),
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
        "total_favorited",
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
        "total_favorited": "获赞总数",
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
    atomic_write_csv(analysis_path, fieldnames, analysis_rows, header_row=chinese_headers)
    upsert_rows_to_table(
        config.export_store_db,
        config.export_uid_analysis_table,
        fieldnames,
        chinese_headers,
        analysis_rows,
        key_field="uploader_id",
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
        atomic_write_text(path, "\n".join(updated_lines) + ("\n" if updated_lines else ""), encoding="utf-8")


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


def run_analysis(
    trigger_upload=True,
    fetch_mode_override=None,
    max_followings=None,
    recent_video_limit_override=None,
    reporter=None,
    export_outputs=True,
):
    config = load_analyzer_config(
        fetch_mode_override=fetch_mode_override,
        recent_video_limit_override=recent_video_limit_override,
    )
    setup_logging(config.log_dir, "douyin_app")
    export_outputs = bool(export_outputs or trigger_upload)

    fetch_mode = (config.fetch_mode or "counts").strip().lower()
    enable_partial_upload = trigger_upload and export_outputs and fetch_mode != "counts"

    browser_client = create_douyin_browser_client(config)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client,
        cache_store,
        upload_callback=run_partial_feishu_upload if enable_partial_upload else None,
        max_followings=max_followings,
        reporter=reporter,
        export_outputs=export_outputs,
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


def run_fetch_uid_videos(list_path, max_targets=None):
    config = load_analyzer_config(fetch_mode_override="full")
    setup_logging(config.log_dir, "douyin_uid_fetch")

    targets = load_uid_targets(list_path)
    if not targets:
        get_console().print(create_summary_panel("Douyin UID Fetch", ["No valid UID found in list."], border_style="yellow"))
        return []
    total_targets = len(targets)
    cache_store = CacheStore(config)
    profile_index = load_douyin_uid_profile_index(cache_store)
    order_field, order_desc, order_label = get_douyin_uid_fetch_order()
    targets = sort_uid_targets_by_follower_count(targets, profile_index, order_field, order_desc)
    if max_targets is not None:
        targets = targets[:max(0, int(max_targets))]
    if not targets:
        get_console().print(create_summary_panel("Douyin UID Fetch", ["Selected UID count is 0."], border_style="yellow"))
        return []

    get_console().print(
        create_summary_panel(
            "Douyin UID Order",
            [
                f"\u5df2\u6309{order_label}{'\u4ece\u9ad8\u5230\u4f4e' if order_desc else '\u4ece\u4f4e\u5230\u9ad8'}\u6392\u5e8f\u540e\u5f00\u59cb\u6293\u53d6\u3002",
                "\u6392\u5e8f\u6765\u6e90: \u6296\u97f3\u5173\u6ce8\u5217\u8868\u7f13\u5b58/\u5386\u53f2\u6293\u53d6\u7f13\u5b58\uff0c\u7f3a\u5931\u6307\u6807\u6309 0 \u5904\u7406\u3002",
                f"Selected UID count: {len(targets)} / {total_targets}",
            ],
            border_style="cyan",
        )
    )
    browser_client = create_douyin_browser_client(config)
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
                try:
                    check_stop()
                except OperationCancelled:
                    write_uid_fetch_outputs(config, all_video_rows, summary_rows)
                    write_uid_analysis_output(config, analysis_rows)
                    raise
                progress.update(task_id, description=f"Fetch Douyin UID videos | current UID: {uid}")
                fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
                cached_user = profile_index.get(str(uid), {}) or {}
                user = {
                    "sec_uid": uid,
                    "nickname": cached_user.get("nickname") or cached_user.get("uploader_name") or f"UID_{uid}",
                    "homepage": cached_user.get("homepage") or cached_user.get("uploader_homepage") or f"https://www.douyin.com/user/{uid}",
                    "remark_name": cached_user.get("remark_name", ""),
                    "follower_count": cached_user.get("follower_count", ""),
                    "aweme_count": cached_user.get("aweme_count") or cached_user.get("total_videos") or "",
                    "total_favorited": cached_user.get("total_favorited", ""),
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
                            "total_favorited": user.get("total_favorited", ""),
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
                            "total_favorited": user.get("total_favorited", ""),
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
                f"UID count: {len(targets)} / {total_targets}",
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


def run_cache_liked_videos_as_s(limit=None):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_liked_video_cache")
    browser_client = create_douyin_browser_client(config)
    try:
        browser_client.ensure_login()
        videos = browser_client.get_liked_videos_from_homepage(limit=limit)
    finally:
        browser_client.close()

    output_path = config.output_csv.parent / "douyin_liked_videos_cached.csv"
    fieldnames = [
        "uploader_name",
        "uploader_id",
        "video_id",
        "aweme_id",
        "video_title",
        "video_url",
        "publish_date",
        "publish_timestamp",
        "duration_seconds",
        "like_count",
        "video_manual_grade",
    ]
    rows = []
    for video in videos or []:
        row = dict(video)
        video_id = str(row.get("aweme_id") or row.get("video_id") or "").strip()
        if not video_id:
            continue
        row["video_id"] = video_id
        row["aweme_id"] = video_id
        row["video_manual_grade"] = "S"
        row["source_mode"] = "liked"
        rows.append(row)

    atomic_write_csv(output_path, fieldnames, rows)
    upsert_video_state_rows(
        source_store_db_path(config),
        "douyin",
        rows,
        video_id_column="aweme_id",
        uploader_id_column="uploader_id",
        uploader_name_column="uploader_name",
        source_mode="liked",
    )
    upsert_cache_entries(
        config.export_store_db,
        "douyin",
        {row["aweme_id"]: row for row in rows if row.get("aweme_id")},
        cache_type="liked_videos",
        source_mode="liked",
        uploader_id_getter=lambda _key, payload: (payload or {}).get("uploader_id", ""),
        cached_at_getter=lambda _payload: datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    )
    _upsert_manual_video_grades(
        rating_store_db_path(config),
        [row.get("aweme_id") for row in rows],
        grade="S",
        note="homepage liked video cache",
    )

    from .rating.video_scoring import run_douyin_video_scoring

    video_score_path = run_douyin_video_scoring(config)
    try:
        from .rating.creator_scoring import run_douyin_creator_scoring

        run_douyin_creator_scoring(config)
    except Exception as exc:
        get_console().print(
            create_summary_panel(
                "Douyin Creator Score Refresh Skipped",
                [str(exc)],
                border_style="yellow",
            )
        )

    get_console().print(
        create_summary_panel(
            "Douyin Liked Videos Cached",
            [
                f"Liked videos: {len(rows)}",
                "Manual video grade: S",
                f"Cache CSV: {output_path}",
                f"Video score CSV: {video_score_path}",
            ],
            border_style="green",
        )
    )
    return {
        "video_count": len(rows),
        "output_path": str(output_path),
        "video_score_path": str(video_score_path),
    }


def _upsert_manual_video_grades(db_path, video_ids, grade="S", note=""):
    video_ids = sorted({str(item or "").strip() for item in (video_ids or []) if str(item or "").strip()})
    if not video_ids:
        return 0
    updated_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS douyin_video_manual_rating (
                video_id TEXT PRIMARY KEY,
                manual_grade TEXT NOT NULL,
                note TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO douyin_video_manual_rating (video_id, manual_grade, note, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(video_id) DO UPDATE SET
                manual_grade=excluded.manual_grade,
                note=excluded.note,
                updated_at=excluded.updated_at
            """,
            [(video_id, grade, note, updated_at) for video_id in video_ids],
        )
        conn.commit()
    return len(video_ids)


def run_score_videos_from_cache(*, refresh_inventory=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_video_scoring")
    if refresh_inventory:
        refresh_cache_inventory_current(config)
    from .rating.video_scoring import run_douyin_video_scoring

    return run_douyin_video_scoring(config)


def run_score_creators_from_cache(*, refresh_inventory=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_creator_scoring")
    if refresh_inventory:
        refresh_cache_inventory_current(config)
    if _sqlite_table_count(rating_store_db_path(config), "video_score_current") <= 0:
        from .rating.video_scoring import run_douyin_video_scoring

        run_douyin_video_scoring(config)
    from .rating.creator_scoring import run_douyin_creator_scoring

    return run_douyin_creator_scoring(config)


def refresh_cache_inventory_current(config=None):
    config = config or load_analyzer_config()
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(config, browser_client=None, cache_store=cache_store)
    cache_rows = analyzer.build_cache_inventory_rows(
        cache_store.load_followings_cache_payload(),
        cache_store.load_progress(),
    )
    save_cache_inventory_to_csv(config, cache_rows)
    return len(cache_rows)


def run_export_compact_tables_from_cache(high_like_threshold=10000):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_compact_export")
    if _sqlite_table_count(rating_store_db_path(config), "video_score_current") <= 0:
        from .rating.video_scoring import run_douyin_video_scoring

        run_douyin_video_scoring(config)
    if _sqlite_table_count(rating_store_db_path(config), "creator_score_current") <= 0:
        from .rating.creator_scoring import run_douyin_creator_scoring

        run_douyin_creator_scoring(config)
    from .compact_exports import run_douyin_compact_exports

    return run_douyin_compact_exports(config, high_like_threshold=high_like_threshold)


def run_prune_export_snapshots(vacuum=False):
    config = load_analyzer_config()
    from common.export_store import prune_disabled_snapshot_history

    deleted = prune_disabled_snapshot_history(config.export_store_db)
    if vacuum:
        import sqlite3

        with sqlite3.connect(config.export_store_db) as conn:
            conn.execute("VACUUM")
    return deleted


def _sqlite_table_count(db_path, table_name):
    import sqlite3
    from pathlib import Path

    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0] or 0)


def _load_liked_video_state_rows(db_path):
    db_path = Path(db_path) if db_path else None
    if not db_path or not db_path.exists():
        return []

    try:
        with sqlite3.connect(db_path, timeout=10) as conn:
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='douyin_video_state'"
            ).fetchone()
            if not exists:
                return []

            rows = conn.execute(
                """
                SELECT video_id, uploader_id, uploader_name, publish_timestamp, like_count,
                       duration_seconds, source_mode, payload_json
                FROM douyin_video_state
                WHERE video_id IS NOT NULL AND TRIM(video_id) != ''
                """
            ).fetchall()
    except Exception as exc:
        get_console().print(
            create_summary_panel(
                "Liked Video Cache Read Failed",
                [f"Database: {db_path}", f"Error: {exc}"],
                border_style="yellow",
            )
        )
        return []

    videos = []
    for raw in rows:
        video_id, uploader_id, uploader_name, publish_timestamp, like_count, duration_seconds, source_mode, payload_json = raw
        try:
            payload = json.loads(payload_json or "{}")
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        if str(source_mode or "").strip().lower() != "liked" and metadata.get("liked_cache") is not True:
            continue
        row = dict(payload)
        row.setdefault("aweme_id", str(video_id or "").strip())
        row.setdefault("video_id", str(video_id or "").strip())
        row.setdefault("uploader_id", str(uploader_id or "").strip())
        row.setdefault("uploader_name", str(uploader_name or "").strip())
        row.setdefault("publish_timestamp", publish_timestamp)
        row.setdefault("like_count", like_count)
        row.setdefault("duration_seconds", duration_seconds)
        row.setdefault("video_manual_grade", "S")
        videos.append(row)
    return videos


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
