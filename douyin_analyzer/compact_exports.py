import csv
import sqlite3
from pathlib import Path

from common.file_io import atomic_write_csv


CREATOR_FIELDS = [
    "uploader_id",
    "uploader_name",
    "final_grade",
    "final_score",
    "confidence",
    "manual_grade",
    "followers",
    "total_likes",
    "published_video_count",
    "scored_video_count",
    "avg_likes",
    "avg_update_days",
    "latest_publish_date",
    "inactive_days",
    "latest_video_title",
    "short_ratio",
    "medium_ratio",
    "long_medium_ratio",
    "long_ratio",
    "cached_video_count",
    "cached_mode",
    "cache_expired",
    "homepage_url",
    "follow_group",
    "data_source",
    "score_reasons",
]

CREATOR_HEADERS = {
    "uploader_id": "UP主UID",
    "uploader_name": "UP主姓名",
    "final_grade": "UP最终等级",
    "final_score": "UP最终分",
    "confidence": "评级置信度",
    "manual_grade": "UP手动等级",
    "followers": "粉丝数",
    "total_likes": "获赞总数",
    "published_video_count": "发布视频数量",
    "scored_video_count": "已评分视频数",
    "avg_likes": "平均点赞数",
    "avg_update_days": "平均几天一更",
    "latest_publish_date": "最后活跃/发布日期",
    "inactive_days": "未更新天数",
    "latest_video_title": "最新视频标题",
    "short_ratio": "短视频占比",
    "medium_ratio": "中视频占比",
    "long_medium_ratio": "中长视频占比",
    "long_ratio": "长视频占比",
    "cached_video_count": "缓存视频数",
    "cached_mode": "已缓存模式",
    "cache_expired": "缓存是否到期",
    "homepage_url": "UP主主页链接",
    "follow_group": "关注分组名称",
    "data_source": "数据来源",
    "score_reasons": "评分原因",
}

VIDEO_FIELDS = [
    "video_id",
    "uploader_id",
    "uploader_name",
    "final_grade",
    "final_score",
    "confidence",
    "rating_status",
    "is_high_like",
    "video_title",
    "publish_date",
    "age_days",
    "duration_seconds",
    "duration_category",
    "like_count",
    "download_status",
    "download_time",
    "download_path",
    "video_url",
    "score_reasons",
]

VIDEO_HEADERS = {
    "video_id": "视频ID",
    "uploader_id": "UP主UID",
    "uploader_name": "UP主姓名",
    "final_grade": "视频最终等级",
    "final_score": "视频最终分",
    "confidence": "评分置信度",
    "rating_status": "评分状态",
    "is_high_like": "是否高赞",
    "video_title": "视频标题",
    "publish_date": "发布日期",
    "age_days": "发布至今天数",
    "duration_seconds": "视频时长(秒)",
    "duration_category": "时长分类",
    "like_count": "点赞数",
    "download_status": "下载状态",
    "download_time": "下载时间",
    "download_path": "下载路径",
    "video_url": "视频链接",
    "score_reasons": "评分原因",
}

DIAGNOSTIC_FIELDS = [
    "type",
    "record_time",
    "mode",
    "stage",
    "uploader_id",
    "uploader_name",
    "homepage_url",
    "summary",
    "detail",
]

DIAGNOSTIC_HEADERS = {
    "type": "类型",
    "record_time": "记录时间",
    "mode": "模式",
    "stage": "阶段",
    "uploader_id": "UP主UID",
    "uploader_name": "UP主姓名",
    "homepage_url": "UP主主页链接",
    "summary": "摘要",
    "detail": "详情",
}


def run_douyin_compact_exports(config, high_like_threshold=10000):
    exporter = DouyinCompactExporter(config, high_like_threshold=high_like_threshold)
    result = exporter.export()
    _print_summary(
        "抖音精简合并表导出完成",
        [
            f"UP主总表: {result['creator_path']} ({result['creator_rows']} 行)",
            f"视频总表: {result['video_path']} ({result['video_rows']} 行)",
            f"诊断总表: {result['diagnostic_path']} ({result['diagnostic_rows']} 行)",
            "说明: 高赞视频已合并到视频总表的“是否高赞”列。",
        ],
    )
    return result


class DouyinCompactExporter:
    def __init__(self, config, high_like_threshold=10000):
        self.config = config
        self.db_path = Path(config.export_store_db)
        self.high_like_threshold = int(high_like_threshold or 10000)
        output_dir = Path(config.output_csv).parent
        self.creator_path = Path(getattr(config, "compact_creator_csv", output_dir / "douyin_creators_summary.csv"))
        self.video_path = Path(getattr(config, "compact_video_csv", output_dir / "douyin_videos_summary.csv"))
        self.diagnostic_path = Path(
            getattr(config, "compact_diagnostic_csv", output_dir / "douyin_diagnostics_summary.csv")
        )

    def export(self):
        creator_rows = self._build_creator_rows()
        video_rows = self._build_video_rows()
        diagnostic_rows = self._build_diagnostic_rows()
        atomic_write_csv(self.creator_path, CREATOR_FIELDS, creator_rows, header_row=CREATOR_HEADERS)
        atomic_write_csv(self.video_path, VIDEO_FIELDS, video_rows, header_row=VIDEO_HEADERS)
        atomic_write_csv(self.diagnostic_path, DIAGNOSTIC_FIELDS, diagnostic_rows, header_row=DIAGNOSTIC_HEADERS)
        return {
            "creator_path": self.creator_path,
            "creator_rows": len(creator_rows),
            "video_path": self.video_path,
            "video_rows": len(video_rows),
            "diagnostic_path": self.diagnostic_path,
            "diagnostic_rows": len(diagnostic_rows),
        }

    def _build_creator_rows(self):
        main = _index_by(_read_table(self.db_path, "main_sheet_current"), "UP主UID")
        duration = _index_by(_read_table(self.db_path, "analysis_sheet_current"), "UP主UID")
        scores = _index_by(_read_table(self.db_path, "creator_score_current"), "UP主UID")
        inventory_rows = _read_table(self.db_path, "cache_inventory_current")
        if not inventory_rows:
            inventory_rows = _read_csv(getattr(self.config, "cache_inventory_csv", ""))
        inventory = _index_by(inventory_rows, "UP主UID")

        uids = sorted(set(main) | set(duration) | set(scores) | set(inventory))
        rows = []
        for uid in uids:
            main_row = main.get(uid, {})
            duration_row = duration.get(uid, {})
            score_row = scores.get(uid, {})
            inventory_row = inventory.get(uid, {})
            rows.append(
                {
                    "uploader_id": uid,
                    "uploader_name": _first(score_row, main_row, duration_row, inventory_row, key="UP主姓名"),
                    "final_grade": score_row.get("UP最终等级", ""),
                    "final_score": score_row.get("UP最终分", ""),
                    "confidence": score_row.get("评级置信度", ""),
                    "manual_grade": score_row.get("UP手动等级", ""),
                    "followers": _first(main_row, duration_row, inventory_row, score_row, key="粉丝数"),
                    "total_likes": _first(main_row, duration_row, score_row, key="获赞总数"),
                    "published_video_count": _first(main_row, duration_row, score_row, key="发布视频数量", fallback_key="视频总数"),
                    "scored_video_count": score_row.get("已评分视频数", ""),
                    "avg_likes": _first(main_row, duration_row, score_row, key="平均点赞数"),
                    "avg_update_days": _first(main_row, duration_row, score_row, key="平均几天一更"),
                    "latest_publish_date": _first(main_row, score_row, inventory_row, key="最后活跃/发布日期", fallback_key="缓存最新发布时间"),
                    "inactive_days": _first(main_row, score_row, key="未更新天数"),
                    "latest_video_title": _first(main_row, inventory_row, key="最新视频标题", fallback_key="缓存最新视频标题"),
                    "short_ratio": duration_row.get("短视频占比", ""),
                    "medium_ratio": duration_row.get("中视频占比", ""),
                    "long_medium_ratio": duration_row.get("中长视频占比", ""),
                    "long_ratio": duration_row.get("长视频占比", ""),
                    "cached_video_count": inventory_row.get("缓存视频数", ""),
                    "cached_mode": inventory_row.get("已缓存模式", ""),
                    "cache_expired": inventory_row.get("是否已到期", ""),
                    "homepage_url": _first(main_row, inventory_row, key="UP主主页链接"),
                    "follow_group": main_row.get("关注分组名称", ""),
                    "data_source": main_row.get("数据来源", ""),
                    "score_reasons": score_row.get("评分原因", ""),
                }
            )
        return rows

    def _build_video_rows(self):
        rows = _read_table(self.db_path, "video_score_current")
        result = []
        for row in rows:
            like_count = _safe_int(row.get("点赞数"), 0)
            result.append(
                {
                    "video_id": row.get("视频ID", ""),
                    "uploader_id": row.get("UP主UID", ""),
                    "uploader_name": row.get("UP主姓名", ""),
                    "final_grade": row.get("视频最终等级", ""),
                    "final_score": row.get("视频最终分", ""),
                    "confidence": row.get("评分置信度", ""),
                    "rating_status": row.get("评分状态", ""),
                    "is_high_like": "是" if like_count >= self.high_like_threshold else "否",
                    "video_title": row.get("视频标题", ""),
                    "publish_date": row.get("发布日期", ""),
                    "age_days": row.get("发布至今天数", ""),
                    "duration_seconds": row.get("视频时长(秒)", ""),
                    "duration_category": row.get("时长分类", ""),
                    "like_count": row.get("点赞数", ""),
                    "download_status": row.get("下载状态", ""),
                    "download_time": row.get("下载时间", ""),
                    "download_path": row.get("下载路径", ""),
                    "video_url": row.get("视频链接", ""),
                    "score_reasons": row.get("评分原因", ""),
                }
            )
        return result

    def _build_diagnostic_rows(self):
        rows = []
        rows.extend(self._load_failed_profile_rows())
        rows.extend(self._load_full_fetch_mismatch_rows())
        return rows

    def _load_failed_profile_rows(self):
        path = Path(getattr(self.config, "failed_profiles_csv", ""))
        result = []
        for row in _read_csv(path):
            result.append(
                {
                    "type": "failed_profile",
                    "record_time": row.get("failed_time", ""),
                    "mode": row.get("mode", ""),
                    "stage": row.get("stage", ""),
                    "uploader_id": row.get("uploader_id", ""),
                    "uploader_name": row.get("uploader_name", ""),
                    "homepage_url": row.get("homepage", ""),
                    "summary": row.get("reason", ""),
                    "detail": "",
                }
            )
        return result

    def _load_full_fetch_mismatch_rows(self):
        path = Path(getattr(self.config, "full_fetch_mismatch_csv", ""))
        result = []
        for row in _read_csv(path):
            detail = (
                f"主页作品数={row.get('主页作品数', '')}; "
                f"实际抓取数={row.get('实际抓取数', '')}; "
                f"抓取次数={row.get('抓取次数', '')}; "
                f"出现底部={row.get('是否出现暂时没有更多了', '')}"
            )
            result.append(
                {
                    "type": "full_fetch_mismatch",
                    "record_time": row.get("记录时间", ""),
                    "mode": "full",
                    "stage": "count_validation",
                    "uploader_id": row.get("UP主UID", ""),
                    "uploader_name": row.get("UP主姓名", ""),
                    "homepage_url": row.get("UP主页链接", ""),
                    "summary": row.get("最后校验结果", ""),
                    "detail": detail,
                }
            )
        return result


def _read_table(db_path, table_name):
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return []
        conn.row_factory = sqlite3.Row
        return [dict(row) for row in conn.execute(f'SELECT * FROM "{table_name}"')]


def _read_csv(path):
    path = Path(path)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8-sig", newline="") as file:
        return list(csv.DictReader(file))


def _index_by(rows, key):
    result = {}
    for row in rows:
        value = str(row.get(key) or "").strip()
        if value:
            result[value] = row
    return result


def _first(*rows, key, fallback_key=None):
    keys = [key]
    if fallback_key:
        keys.append(fallback_key)
    for row in rows:
        for item_key in keys:
            value = row.get(item_key)
            if value not in (None, ""):
                return value
    return ""


def _safe_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _print_summary(title, lines):
    try:
        from rich.console import Console
        from rich.panel import Panel
    except Exception:
        print(title)
        for line in lines:
            print(line)
        return
    Console().print(Panel("\n".join(str(line) for line in lines), title=title, border_style="green"))
