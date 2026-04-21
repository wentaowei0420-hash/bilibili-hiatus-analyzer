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
from .analyzer import BilibiliHiatusAnalyzer
from .bilibili_api import BilibiliApi
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config
from .feishu_uploader import FeishuUploader
from .http_client import BilibiliHttpClient
from .logging_utils import create_progress, create_summary_panel, get_console, setup_logging


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
            if candidate.isdigit():
                targets.append(candidate)

    return list(dict.fromkeys(targets))


def write_uid_fetch_outputs(config, video_rows, summary_rows):
    output_dir = config.output_csv.parent
    videos_path = output_dir / "bilibili_uid_all_videos.csv"
    summary_path = output_dir / "bilibili_uid_fetch_summary.csv"

    video_fieldnames = [
        "uploader_name",
        "uploader_id",
        "video_title",
        "bvid",
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
        "bvid": "BVID",
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
        "video_count": "视频数量",
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
            "视频总数": row.get("video_count", ""),
            "抓取状态": row.get("status", ""),
            "最近抓取时间": row.get("fetched_at", ""),
        }
        for row in summary_rows
    ]
    upsert_creator_rows(config.export_store_db, "bilibili", creator_rows, source_mode="uid")

    grouped_rows = {}
    for row in video_rows or []:
        uploader_id = str((row or {}).get("uploader_id") or "").strip()
        if not uploader_id:
            continue
        grouped_rows.setdefault(uploader_id, []).append(row)
    for uploader_id, rows in grouped_rows.items():
        replace_video_rows_for_uploader(
            config.export_store_db,
            "bilibili",
            uploader_id,
            rows,
            "bvid",
        )

    return videos_path, summary_path


def write_uid_analysis_output(config, analysis_rows):
    output_dir = config.output_csv.parent
    analysis_path = output_dir / "bilibili_uid_video_duration_analysis.csv"
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


def run_analysis(trigger_upload=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "bilibili_app")

    client = BilibiliHttpClient(config)
    api = BilibiliApi(config, client)
    cache_store = CacheStore(config)
    analyzer = BilibiliHiatusAnalyzer(config, api, cache_store)
    results = analyzer.analyze_hiatus()

    if trigger_upload and results is not None:
        get_console().print(
            create_summary_panel(
                "Bilibili Main Sheet Sync",
                ["Analysis finished. Main data sheet will be synced now."],
                border_style="cyan",
            )
        )
        run_feishu_upload()

    return results


def run_feishu_upload(prune_missing=True):
    config = load_feishu_config()
    setup_logging(config.log_dir, "feishu_upload")
    uploader = FeishuUploader(config)
    uploader.run(prune_missing=prune_missing)


def run_uid_analysis_upload(csv_path, target_uids=None):
    config = load_feishu_config()
    setup_logging(config.log_dir, "bilibili_uid_analysis_upload")
    _show_uid_analysis_status_panel(config, csv_path, target_uids=target_uids)
    uploader = FeishuUploader(config)
    uploader.run_single_table(
        config.export_uid_analysis_table,
        csv_fallback_path=csv_path,
        sheet_title=config.analysis_sheet_title,
        sheet_index=config.analysis_sheet_index,
        upload_state_json=config.analysis_upload_state_json,
        panel_title="Bilibili UID Analysis Synced",
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
            "Bilibili UID Analysis Ready",
            lines,
            border_style="cyan",
        )
    )


def run_fetch_uid_videos(list_path):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "bilibili_uid_fetch")

    targets = load_uid_targets(list_path)
    if not targets:
        get_console().print(create_summary_panel("Bilibili UID Fetch", ["No valid UID found in list."], border_style="yellow"))
        return []

    client = BilibiliHttpClient(config)
    api = BilibiliApi(config, client)
    cache_store = CacheStore(config)
    analyzer = BilibiliHiatusAnalyzer(config, api, cache_store)
    all_video_rows = []
    summary_rows = []
    analysis_rows = []

    with create_progress(transient=False) as progress:
        task_id = progress.add_task("Fetch Bilibili UID videos", total=len(targets))
        for uid in targets:
            progress.update(task_id, description=f"Fetch Bilibili UID videos | current UID: {uid}")
            fetched_at = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            try:
                videos = api.get_all_videos_for_up(uid, f"UID_{uid}")
                all_video_rows.extend(videos)
                following = {
                    "mid": uid,
                    "uname": videos[0].get("uploader_name", f"UID_{uid}") if videos else f"UID_{uid}",
                    "follower_count": "",
                }
                analysis_rows.append(analyzer.build_video_duration_summary(following, videos))
                summary_rows.append(
                    {
                        "target_uid": uid,
                        "uploader_name": following["uname"],
                        "uploader_homepage": f"https://space.bilibili.com/{uid}",
                        "video_count": len(videos),
                        "status": "success" if videos else "no_video",
                        "last_publish_date": videos[0].get("publish_date", "") if videos else "",
                        "fetched_at": fetched_at,
                        "message": "",
                    }
                )
            except Exception as exc:
                analysis_rows.append(
                    analyzer.build_video_duration_summary(
                        {"mid": uid, "uname": f"UID_{uid}", "follower_count": ""},
                        [],
                    )
                )
                summary_rows.append(
                    {
                        "target_uid": uid,
                        "uploader_name": f"UID_{uid}",
                        "uploader_homepage": f"https://space.bilibili.com/{uid}",
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

    videos_path, summary_path = write_uid_fetch_outputs(config, all_video_rows, summary_rows)
    analysis_path = write_uid_analysis_output(config, analysis_rows)
    get_console().print(
        create_summary_panel(
            "Bilibili UID Fetch Finished",
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


def main():
    try:
        run_analysis(trigger_upload=True)
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Execution was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Bilibili Error", [str(exc)], border_style="red"))
        traceback.print_exc()


def upload_main():
    try:
        run_feishu_upload()
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Upload was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Upload Error", [str(exc)], border_style="red"))
        traceback.print_exc()
