import csv
from datetime import datetime

from bilibili_analyzer.logging_utils import smart_print as print

from .utils import format_ratio


def save_to_csv(config, results):
    fieldnames = [
        "uploader_name",
        "following_remark",
        "uploader_id",
        "uploader_homepage",
        "following_group_names",
        "follower_count",
        "published_video_count",
        "average_like_count",
        "average_update_interval_days",
        "latest_video_title",
        "upload_date",
        "days_since_update",
        "days_since_last_video",
        "view_count",
        "video_url",
        "data_source",
    ]
    chinese_headers = {
        "following_remark": "备注",
        "uploader_name": "UP主姓名",
        "uploader_id": "UP主UID",
        "uploader_homepage": "UP主主页链接",
        "following_group_names": "关注分组名称",
        "follower_count": "粉丝数",
        "published_video_count": "发布视频数量",
        "average_like_count": "平均点赞数",
        "average_update_interval_days": "平均几天一更",
        "latest_video_title": "最新视频标题",
        "upload_date": "最后活跃/发布日期",
        "days_since_update": "未更新天数",
        "days_since_last_video": "距离最后一个视频发布(天)",
        "view_count": "最新视频播放量",
        "video_url": "视频链接",
        "data_source": "数据来源",
    }
    _write_csv(config.output_csv, fieldnames, chinese_headers, results, "保存抖音排行CSV失败")


def save_all_videos_to_csv(config, video_rows):
    fieldnames = [
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
    chinese_headers = {
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
    _write_csv(
        config.all_videos_csv,
        fieldnames,
        chinese_headers,
        video_rows,
        "保存抖音视频明细CSV失败",
    )


def save_video_duration_analysis_to_csv(config, summary_rows):
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
    _write_csv(
        config.video_duration_analysis_csv,
        fieldnames,
        chinese_headers,
        summary_rows,
        "保存抖音视频时长分析CSV失败",
    )


def save_video_duration_report(config, summary_rows, total_video_count):
    try:
        config.video_duration_report_md.parent.mkdir(parents=True, exist_ok=True)
        total_up_count = len(summary_rows)
        short_total = sum(row["short_video_count"] for row in summary_rows)
        medium_total = sum(row["medium_video_count"] for row in summary_rows)
        medium_long_total = sum(row["medium_long_video_count"] for row in summary_rows)
        long_total = sum(row["long_video_count"] for row in summary_rows)

        report_lines = [
            "# 抖音关注博主视频时长分析报告",
            "",
            f"- 生成时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}",
            f"- 分析博主数量: {total_up_count}",
            f"- 分析视频总数: {total_video_count}",
            "",
            "## 全局视频类型占比",
            "",
            f"- 短视频(0~30s): {short_total} ({format_ratio(short_total, total_video_count)})",
            f"- 中视频(30~60s): {medium_total} ({format_ratio(medium_total, total_video_count)})",
            f"- 中长视频(60~240s): {medium_long_total} ({format_ratio(medium_long_total, total_video_count)})",
            f"- 长视频(240s+): {long_total} ({format_ratio(long_total, total_video_count)})",
            "",
            "## 长视频占比 Top 20",
            "",
            "| 排名 | UP主 | 粉丝数 | 视频总数 | 长视频数量 | 长视频占比 | 平均时长 | 平均点赞数 | 平均几天一更 |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]

        sorted_rows = sorted(
            summary_rows,
            key=lambda row: (
                float(str(row["long_video_ratio"]).rstrip("%") or "0"),
                row["total_videos"],
            ),
            reverse=True,
        )

        for index, row in enumerate(sorted_rows[:20], 1):
            average_update_interval_days = row.get("average_update_interval_days")
            report_lines.append(
                f"| {index} | {row['uploader_name']} | {row.get('follower_count') or '暂无数据'} | {row['total_videos']} | "
                f"{row['long_video_count']} | {row['long_video_ratio']} | {row['average_duration_text']} | "
                f"{row.get('average_like_count', 0)} | "
                f"{average_update_interval_days if average_update_interval_days is not None else '暂无数据'} |"
            )

        with config.video_duration_report_md.open("w", encoding="utf-8") as report_file:
            report_file.write("\n".join(report_lines))
    except Exception as exc:
        print(f"❌ 保存抖音视频时长报告失败: {exc}")


def save_cache_inventory_to_csv(config, cache_rows):
    fieldnames = [
        "uploader_name",
        "following_remark",
        "uploader_id",
        "uploader_homepage",
        "follower_count",
        "published_video_count",
        "cache_modes",
        "last_fetch_mode",
        "has_counts_cache",
        "has_monitor_cache",
        "has_delta_cache",
        "has_full_cache",
        "has_followings_cache",
        "followings_cache_saved_at",
        "has_progress_cache",
        "progress_cached_at",
        "summary_scope",
        "cached_video_count",
        "has_latest_video_cache",
        "latest_video_title",
        "latest_publish_date",
        "latest_publish_timestamp",
    ]
    chinese_headers = {
        "uploader_name": "UP主姓名",
        "following_remark": "备注",
        "uploader_id": "UP主UID",
        "uploader_homepage": "UP主主页链接",
        "follower_count": "粉丝数",
        "published_video_count": "发布视频数量",
        "cache_modes": "已缓存模式",
        "last_fetch_mode": "最近抓取模式",
        "has_counts_cache": "有counts缓存",
        "has_monitor_cache": "有monitor缓存",
        "has_delta_cache": "有delta缓存",
        "has_full_cache": "有full缓存",
        "has_followings_cache": "有关注列表缓存",
        "followings_cache_saved_at": "关注列表缓存时间",
        "has_progress_cache": "有进度缓存",
        "progress_cached_at": "进度缓存时间",
        "summary_scope": "统计范围",
        "cached_video_count": "缓存视频数",
        "has_latest_video_cache": "有最新视频缓存",
        "latest_video_title": "缓存的最新视频标题",
        "latest_publish_date": "缓存的最新发布时间",
        "latest_publish_timestamp": "缓存的最新发布时间戳",
    }
    _write_csv(
        config.cache_inventory_csv,
        fieldnames,
        chinese_headers,
        cache_rows,
        "保存抖音缓存清单CSV失败",
    )


def _write_csv(path, fieldnames, chinese_headers, rows, error_message):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", newline="", encoding="utf-8-sig") as csvfile:
            writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
            writer.writerow(chinese_headers)
            writer.writerows(rows)
    except Exception as exc:
        print(f"❌ {error_message}: {exc}")
