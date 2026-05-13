import json
import math
import sqlite3
from bisect import bisect_right
from datetime import datetime
from pathlib import Path

from common.export_store import write_rows_to_table
from common.file_io import atomic_write_csv


GRADE_SCORES = {
    "S": 100,
    "A": 85,
    "B": 72,
    "C": 57,
    "D": 35,
}

AUTO_GRADE_THRESHOLDS = (
    ("A", 80),
    ("B", 65),
    ("C", 50),
    ("D", 0),
)

BASE_WEIGHTS = {
    "up_base": 0.25,
    "like": 0.18,
    "time": 0.08,
    "duration": 0.05,
    "availability": 0.02,
}

FIELDNAMES = [
    "uploader_name",
    "uploader_id",
    "video_title",
    "aweme_id",
    "video_url",
    "publish_date",
    "publish_timestamp",
    "age_days",
    "duration_seconds",
    "duration_category",
    "like_count",
    "up_manual_grade",
    "up_auto_score",
    "up_auto_grade",
    "video_manual_grade",
    "auto_score",
    "auto_grade",
    "final_score",
    "final_grade",
    "rating_status",
    "confidence",
    "score_source",
    "like_score",
    "time_score",
    "duration_score",
    "availability_score",
    "data_maturity",
    "is_available",
    "download_status",
    "download_time",
    "download_path",
    "score_reasons",
    "missing_metrics",
]

HEADERS = {
    "uploader_name": "UP主姓名",
    "uploader_id": "UP主UID",
    "video_title": "视频标题",
    "aweme_id": "视频ID",
    "video_url": "视频链接",
    "publish_date": "发布日期",
    "publish_timestamp": "发布时间戳",
    "age_days": "发布至今天数",
    "duration_seconds": "视频时长(秒)",
    "duration_category": "时长分类",
    "like_count": "点赞数",
    "up_manual_grade": "UP手动等级",
    "up_auto_score": "UP自动分",
    "up_auto_grade": "UP自动等级",
    "video_manual_grade": "视频手动等级",
    "auto_score": "视频自动分",
    "auto_grade": "视频自动等级",
    "final_score": "视频最终分",
    "final_grade": "视频最终等级",
    "rating_status": "评分状态",
    "confidence": "评分置信度",
    "score_source": "评分来源",
    "like_score": "点赞分",
    "time_score": "时间分",
    "duration_score": "时长适配分",
    "availability_score": "可用状态分",
    "data_maturity": "数据成熟度",
    "is_available": "是否可用",
    "download_status": "下载状态",
    "download_time": "下载时间",
    "download_path": "下载路径",
    "score_reasons": "评分原因",
    "missing_metrics": "缺失指标",
}


def run_douyin_video_scoring(config):
    scorer = DouyinVideoScorer(config)
    rows = scorer.score()
    output_path = Path(getattr(config, "video_score_csv", config.output_csv.parent / "douyin_video_scores.csv"))
    atomic_write_csv(output_path, FIELDNAMES, rows, header_row=HEADERS)
    write_rows_to_table(
        config.export_store_db,
        "video_score_current",
        FIELDNAMES,
        HEADERS,
        rows,
    )

    grade_counts = {}
    for row in rows:
        grade = row.get("final_grade") or "未知"
        grade_counts[grade] = grade_counts.get(grade, 0) + 1
    grade_summary = " / ".join(f"{grade}:{grade_counts.get(grade, 0)}" for grade in ["S", "A", "B", "C", "D"])
    _print_summary(
        "抖音视频评分完成",
        [
            f"评分视频数: {len(rows)}",
            f"等级分布: {grade_summary}",
            f"输出文件: {output_path}",
            "说明: 播放量/收藏数/评论数未采集，已从视频评分模型和输出字段中剔除。",
        ],
    )
    return output_path


def _print_summary(title, lines):
    try:
        from rich.console import Console
        from rich.panel import Panel
    except Exception:
        print(title)
        for line in lines:
            print(line)
        return

    Console().print(Panel("\n".join(lines), title=title, border_style="green"))


class DouyinVideoScorer:
    def __init__(self, config):
        self.config = config
        self.db_path = Path(config.export_store_db)
        self.now_ts = int(datetime.now().timestamp())

    def score(self):
        self._ensure_manual_rating_tables()
        creator_manual = self._load_manual_grades("douyin_creator_manual_rating", "uploader_id")
        video_manual = self._load_manual_grades("douyin_video_manual_rating", "video_id")
        main_by_uid = self._load_summary_rows("main")
        analysis_by_uid = self._load_summary_rows("analysis")
        videos = self._load_video_rows()
        download_status = self._load_download_status()

        up_stats = self._build_uploader_video_stats(videos)
        platform_like_values = sorted(
            math.log1p(_safe_int(video.get("like_count"), 0)) for video in videos if _safe_int(video.get("like_count"), 0) > 0
        )

        rows = []
        for video in videos:
            row = self._score_video(
                video,
                main_by_uid,
                analysis_by_uid,
                creator_manual,
                video_manual,
                up_stats,
                platform_like_values,
                download_status,
            )
            rows.append(row)

        rows.sort(
            key=lambda item: (
                -GRADE_SCORES.get(item.get("final_grade"), 0),
                -_safe_float(item.get("final_score"), 0),
                -_safe_int(item.get("publish_timestamp"), 0),
            )
        )
        return rows

    def _ensure_manual_rating_tables(self):
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_creator_manual_rating (
                    uploader_id TEXT PRIMARY KEY,
                    manual_grade TEXT NOT NULL,
                    note TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
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
            conn.commit()

    def _load_manual_grades(self, table_name, key_column):
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f'SELECT {key_column}, manual_grade FROM "{table_name}"'
            ).fetchall()
        result = {}
        for key, grade in rows:
            normalized = _normalize_grade(grade)
            if key and normalized:
                result[str(key).strip()] = normalized
        return result

    def _load_summary_rows(self, summary_type):
        table = "main_sheet_current" if summary_type == "main" else "analysis_sheet_current"
        if not self._table_exists(table):
            return {}
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(f'SELECT * FROM "{table}"').fetchall()
        result = {}
        for row in rows:
            payload = dict(row)
            uid = str(payload.get("UP主UID") or "").strip()
            if uid:
                result[uid] = payload
        return result

    def _load_video_rows(self):
        progress_videos = self._load_progress_video_rows()
        if progress_videos is not None:
            progress_videos = self._merge_liked_state_videos(progress_videos)
            return progress_videos

        if self._table_exists("douyin_video_state"):
            return self._load_video_state_rows()

        if not self._table_exists("douyin_video_raw"):
            return []
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute('SELECT payload_json FROM douyin_video_raw').fetchall()
        videos = []
        for (payload_json,) in rows:
            payload = _loads_json(payload_json)
            if isinstance(payload, dict):
                videos.append(payload)
        return videos

    def _load_video_state_rows(self, only_liked=False):
        if not self._table_exists("douyin_video_state"):
            return []
        where = "WHERE video_id IS NOT NULL AND TRIM(video_id) != ''"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            raw_rows = conn.execute(
                f"""
                SELECT video_id, uploader_id, uploader_name, publish_timestamp, like_count,
                       duration_seconds, source_mode, is_available, payload_json
                FROM douyin_video_state
                {where}
                """
            ).fetchall()
        videos = []
        for raw in raw_rows:
            payload = _loads_json(raw["payload_json"]) or {}
            video = dict(payload)
            video.setdefault("aweme_id", raw["video_id"])
            video.setdefault("video_id", raw["video_id"])
            video.setdefault("uploader_id", raw["uploader_id"])
            video.setdefault("uploader_name", raw["uploader_name"])
            video.setdefault("publish_timestamp", raw["publish_timestamp"])
            video.setdefault("like_count", raw["like_count"])
            video.setdefault("duration_seconds", raw["duration_seconds"])
            video["source_mode"] = raw["source_mode"] or video.get("source_mode", "")
            video["is_available"] = int(raw["is_available"] or 0)
            metadata = video.get("metadata") if isinstance(video.get("metadata"), dict) else {}
            if only_liked and not (
                str(video.get("source_mode") or "").strip().lower() == "liked"
                or metadata.get("liked_cache") is True
            ):
                continue
            videos.append(video)
        return videos

    def _merge_liked_state_videos(self, videos):
        merged = list(videos or [])
        seen_video_ids = {
            str((video or {}).get("aweme_id") or (video or {}).get("video_id") or "").strip()
            for video in merged
            if isinstance(video, dict)
        }
        for video in self._load_video_state_rows(only_liked=True):
            video_id = str(video.get("aweme_id") or video.get("video_id") or "").strip()
            if video_id and video_id not in seen_video_ids:
                merged.append(video)
                seen_video_ids.add(video_id)
        return merged

    def _load_progress_video_rows(self):
        try:
            from .cache import CacheStore

            cache_store = CacheStore(self.config)
            followings = cache_store.load_followings_cache()
            progress = cache_store.load_progress()
        except Exception:
            return None

        if not isinstance(progress, dict) or not progress:
            return None

        active_uids = {
            str((user or {}).get("sec_uid") or "").strip()
            for user in (followings or [])
            if isinstance(user, dict) and str((user or {}).get("sec_uid") or "").strip()
        }
        if not active_uids:
            return []

        videos = []
        seen_video_ids = set()
        for uid, entry in progress.items():
            uid = str(uid or "").strip()
            if uid not in active_uids or not isinstance(entry, dict):
                continue
            user = entry.get("user") if isinstance(entry.get("user"), dict) else {}
            uploader_name = user.get("nickname") or user.get("uploader_name") or ""
            source_mode = str(entry.get("last_fetch_mode") or "progress").strip() or "progress"
            for video in entry.get("videos", []) or []:
                if not isinstance(video, dict):
                    continue
                video_id = str(video.get("aweme_id") or video.get("video_id") or "").strip()
                if not video_id or video_id in seen_video_ids:
                    continue
                seen_video_ids.add(video_id)
                row = _normalize_progress_video(video, video_id, uid, uploader_name)
                row["source_mode"] = row.get("source_mode") or source_mode
                row["is_available"] = row.get("is_available") if row.get("is_available") not in (None, "") else 1
                videos.append(row)
        return videos

    def _load_download_status(self):
        if not self._table_exists("aweme"):
            return {}
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT aweme_id, download_time, file_path FROM aweme"
            ).fetchall()
        statuses = {}
        for aweme_id, download_time, file_path in rows:
            video_id = str(aweme_id or "").strip()
            if not video_id:
                continue
            path_text = str(file_path or "").strip()
            statuses[video_id] = {
                "download_status": "downloaded" if path_text else "downloaded_missing_path",
                "download_time": _format_unix_timestamp(download_time),
                "download_path": path_text,
            }
            if path_text and not Path(path_text).exists():
                statuses[video_id]["download_status"] = "db_record_missing_path"
        return statuses

    def _table_exists(self, table_name):
        if not self.db_path.exists():
            return False
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
        return row is not None

    def _build_uploader_video_stats(self, videos):
        grouped = {}
        for video in videos:
            uid = str(video.get("uploader_id") or "").strip()
            if not uid:
                continue
            item = grouped.setdefault(uid, {"likes": []})
            like_count = _safe_int(video.get("like_count"), 0)
            if like_count > 0:
                item["likes"].append(like_count)
        return {
            uid: {
                "avg_like": sum(values["likes"]) / len(values["likes"]) if values["likes"] else 0,
            }
            for uid, values in grouped.items()
        }

    def _score_video(
        self,
        video,
        main_by_uid,
        analysis_by_uid,
        creator_manual,
        video_manual,
        up_stats,
        platform_like_values,
        download_status,
    ):
        uid = str(video.get("uploader_id") or "").strip()
        video_id = str(video.get("aweme_id") or video.get("video_id") or "").strip()
        main = main_by_uid.get(uid, {})
        analysis = analysis_by_uid.get(uid, {})
        up_manual_grade = creator_manual.get(uid, "")
        video_manual_grade = video_manual.get(video_id, "")
        up_auto_score = self._score_uploader(main, analysis)
        up_auto_grade = _grade_from_score(up_auto_score)
        up_base_score = GRADE_SCORES.get(up_manual_grade, up_auto_score)

        publish_ts = _safe_int(video.get("publish_timestamp"), 0)
        age_days = max((self.now_ts - publish_ts) / 86400, 0) if publish_ts > 0 else None
        data_maturity = _data_maturity(age_days)
        like_count = _safe_int(video.get("like_count"), 0)
        uploader_stats = up_stats.get(uid, {})

        like_score = _relative_metric_score(like_count, uploader_stats.get("avg_like", 0), platform_like_values)
        time_score = _time_score(age_days)
        duration_seconds = _safe_int(video.get("duration_seconds"), 0)
        duration_score = _douyin_duration_score(duration_seconds, like_score)
        is_available = _safe_int(video.get("is_available"), 1)
        availability_score = 100 if is_available else 0

        metric_scores = {
            "up_base": up_base_score,
            "like": like_score,
            "time": time_score,
            "duration": duration_score,
            "availability": availability_score,
        }
        video_base_score = _weighted_score(metric_scores)
        final_score = up_base_score * (1 - data_maturity) + video_base_score * data_maturity
        final_score += _freshness_bonus(age_days)
        final_score -= _stale_penalty(age_days, like_score)
        final_score = _clamp(final_score)

        score_source = "auto"
        final_grade = _grade_from_score(final_score)
        auto_grade = final_grade
        auto_score = final_score
        reasons = []
        if up_manual_grade == "S":
            final_grade = "S"
            final_score = 100
            score_source = "inherited_from_s_creator"
            reasons.append("继承自手动S级UP")
        elif video_manual_grade:
            final_grade = video_manual_grade
            final_score = GRADE_SCORES.get(video_manual_grade, final_score)
            score_source = "manual_video_grade"
            reasons.append(f"视频手动等级={video_manual_grade}")
        else:
            capped_grade = _duration_grade_cap(final_grade, duration_seconds)
            if capped_grade != final_grade:
                reasons.append(f"时长过短，自动等级上限调整为{capped_grade}")
                final_grade = capped_grade
                final_score = min(final_score, GRADE_SCORES.get(capped_grade, final_score))

        reasons.extend(_score_reasons(up_base_score, like_score, time_score, duration_score, is_available, data_maturity))
        status = download_status.get(video_id, {})
        return {
            "uploader_name": video.get("uploader_name") or main.get("UP主姓名") or "",
            "uploader_id": uid,
            "video_title": video.get("video_title") or "",
            "aweme_id": video_id,
            "video_url": video.get("video_url") or "",
            "publish_date": video.get("publish_date") or _date_from_timestamp(publish_ts),
            "publish_timestamp": publish_ts or "",
            "age_days": "" if age_days is None else round(age_days, 2),
            "duration_seconds": duration_seconds or "",
            "duration_category": video.get("duration_category") or "",
            "like_count": like_count,
            "up_manual_grade": up_manual_grade,
            "up_auto_score": round(up_auto_score, 2),
            "up_auto_grade": up_auto_grade,
            "video_manual_grade": video_manual_grade,
            "auto_score": round(auto_score, 2),
            "auto_grade": auto_grade,
            "final_score": round(final_score, 2),
            "final_grade": final_grade,
            "rating_status": _rating_status(age_days),
            "confidence": _confidence_label(age_days, like_count),
            "score_source": score_source,
            "like_score": round(like_score, 2),
            "time_score": round(time_score, 2),
            "duration_score": round(duration_score, 2),
            "availability_score": availability_score,
            "data_maturity": round(data_maturity, 2),
            "is_available": is_available,
            "download_status": status.get("download_status", ""),
            "download_time": status.get("download_time", ""),
            "download_path": status.get("download_path", ""),
            "score_reasons": "；".join(reasons),
            "missing_metrics": "",
        }

    def _score_uploader(self, main, analysis):
        follower_score = _log_score(_safe_int(main.get("粉丝数"), 0), 1000, 1_000_000)
        avg_like_score = _log_score(_safe_int(main.get("平均点赞数") or analysis.get("平均点赞数"), 0), 100, 100_000)
        total_favorited_score = _log_score(_safe_int(main.get("获赞总数") or analysis.get("获赞总数"), 0), 10_000, 10_000_000)
        video_count_score = _log_score(_safe_int(main.get("发布视频数量") or analysis.get("视频总数"), 0), 20, 1000)
        days_since_update = _safe_float(main.get("未更新天数"), None)
        activity_score = _activity_score(days_since_update)
        update_interval_score = _update_interval_score(_safe_float(main.get("平均几天一更") or analysis.get("平均几天一更"), None))
        return _clamp(
            follower_score * 0.20
            + avg_like_score * 0.30
            + total_favorited_score * 0.15
            + activity_score * 0.20
            + update_interval_score * 0.10
            + video_count_score * 0.05
        )


def _weighted_score(metric_scores):
    available_weights = {
        key: BASE_WEIGHTS[key]
        for key in metric_scores
        if metric_scores.get(key) is not None
    }
    total_weight = sum(available_weights.values()) or 1
    return sum(metric_scores[key] * weight for key, weight in available_weights.items()) / total_weight


def _relative_metric_score(value, uploader_average, platform_values):
    value = max(_safe_float(value, 0), 0)
    platform_score = _percentile_score(math.log1p(value), platform_values) if platform_values else 50
    if uploader_average and uploader_average > 0:
        ratio = max(value / uploader_average, 0.01)
        uploader_score = _clamp(50 + 25 * math.log(ratio, 2))
        return uploader_score * 0.60 + platform_score * 0.40
    return platform_score


def _percentile_score(value, sorted_values):
    if not sorted_values:
        return 50
    return bisect_right(sorted_values, value) / len(sorted_values) * 100


def _data_maturity(age_days):
    if age_days is None:
        return 0.75
    if age_days <= 1:
        return 0.10
    if age_days <= 3:
        return 0.25
    if age_days <= 7:
        return 0.45
    if age_days <= 30:
        return 0.75
    return 1.0


def _rating_status(age_days):
    if age_days is None:
        return "发布时间未知"
    if age_days <= 1:
        return "待观察"
    if age_days <= 7:
        return "初评"
    if age_days <= 30:
        return "基本成熟"
    return "成熟评分"


def _confidence_label(age_days, like_count):
    activity = _safe_int(like_count, 0)
    if age_days is None:
        return "中"
    if age_days <= 1:
        return "很低"
    if age_days <= 3:
        return "低"
    if age_days <= 7:
        return "中" if activity > 0 else "低"
    if age_days <= 30:
        return "高" if activity > 0 else "中"
    return "很高" if activity > 0 else "高"


def _time_score(age_days):
    if age_days is None:
        return 60
    if age_days <= 7:
        return 100
    if age_days <= 30:
        return 90
    if age_days <= 90:
        return 75
    if age_days <= 365:
        return 65
    if age_days <= 1095:
        return 55
    return 45


def _freshness_bonus(age_days):
    if age_days is None:
        return 0
    if age_days <= 7:
        return 3
    if age_days <= 30:
        return 1
    return 0


def _stale_penalty(age_days, like_score):
    if age_days is None:
        return 0
    penalty = 0
    if age_days > 1095:
        penalty = 8
    elif age_days > 365:
        penalty = 3
    if like_score >= 80:
        penalty *= 0.5
    return penalty


def _douyin_duration_score(duration_seconds, like_score):
    duration_seconds = _safe_int(duration_seconds, 0)
    if duration_seconds <= 0:
        return 55
    if duration_seconds <= 7:
        return 40
    if duration_seconds <= 15:
        return 65
    if duration_seconds <= 60:
        return 90
    if duration_seconds <= 180:
        return 80
    if duration_seconds <= 300:
        return 65
    if duration_seconds <= 600:
        return 65 if like_score >= 80 else 45
    return 55 if like_score >= 85 else 30


def _duration_grade_cap(grade, duration_seconds):
    order = ["D", "C", "B", "A", "S"]
    duration_seconds = _safe_int(duration_seconds, 0)
    if duration_seconds and duration_seconds <= 7:
        cap = "B"
    elif duration_seconds and duration_seconds <= 15:
        cap = "A"
    else:
        return grade
    return grade if order.index(grade) <= order.index(cap) else cap


def _score_reasons(up_score, like_score, time_score, duration_score, is_available, maturity):
    reasons = []
    if up_score >= 80:
        reasons.append("UP基础分较高")
    if like_score >= 80:
        reasons.append("点赞表现优秀")
    elif like_score < 45:
        reasons.append("点赞表现偏低")
    if time_score >= 90:
        reasons.append("发布时间较新")
    elif time_score <= 55:
        reasons.append("发布时间较久")
    if duration_score >= 80:
        reasons.append("时长适配较好")
    elif duration_score <= 45:
        reasons.append("时长适配偏弱")
    if not is_available:
        reasons.append("视频当前不可用")
    if maturity < 0.5:
        reasons.append("数据尚未成熟")
    return reasons


def _grade_from_score(score):
    for grade, threshold in AUTO_GRADE_THRESHOLDS:
        if score >= threshold:
            return grade
    return "D"


def _normalize_grade(value):
    grade = str(value or "").strip().upper()
    return grade if grade in GRADE_SCORES else ""


def _log_score(value, low, high):
    value = max(_safe_float(value, 0), 0)
    if value <= 0:
        return 0
    low_log = math.log1p(low)
    high_log = math.log1p(high)
    return _clamp((math.log1p(value) - low_log) / (high_log - low_log) * 100)


def _activity_score(days_since_update):
    if days_since_update is None:
        return 55
    if days_since_update <= 7:
        return 100
    if days_since_update <= 30:
        return 85
    if days_since_update <= 90:
        return 65
    if days_since_update <= 180:
        return 45
    return 25


def _update_interval_score(interval_days):
    if interval_days is None:
        return 55
    if interval_days <= 3:
        return 100
    if interval_days <= 7:
        return 85
    if interval_days <= 14:
        return 70
    if interval_days <= 30:
        return 50
    return 30


def _date_from_timestamp(timestamp):
    timestamp = _safe_int(timestamp, 0)
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")


def _format_unix_timestamp(value):
    timestamp = _safe_int(value, 0)
    if timestamp <= 0:
        return ""
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S")


def _loads_json(value):
    try:
        return json.loads(value) if value else None
    except Exception:
        return None


def _first_value(payload, *keys):
    for key in keys:
        value = payload.get(key)
        if value not in (None, ""):
            return value
    return None


def _normalize_progress_video(video, video_id, uploader_id, uploader_name):
    row = dict(video or {})
    row["aweme_id"] = str(row.get("aweme_id") or row.get("video_id") or video_id or "").strip()
    row["video_id"] = str(row.get("video_id") or row.get("aweme_id") or video_id or "").strip()
    row["uploader_id"] = str(row.get("uploader_id") or row.get("author_id") or uploader_id or "").strip()
    row["uploader_name"] = str(row.get("uploader_name") or row.get("author_name") or uploader_name or "").strip()

    title = _first_value(row, "video_title", "title", "desc", "description")
    if title is not None:
        row["video_title"] = title

    like_count = _first_value(row, "like_count", "digg_count", "点赞数")
    if like_count is not None:
        row["like_count"] = like_count

    publish_timestamp = _first_value(row, "publish_timestamp", "create_time", "发布时间戳")
    if publish_timestamp is not None:
        row["publish_timestamp"] = publish_timestamp

    duration_seconds = _first_value(row, "duration_seconds", "duration", "video_duration")
    if duration_seconds is not None:
        row["duration_seconds"] = duration_seconds

    if not row.get("video_url") and row["aweme_id"]:
        row["video_url"] = f"https://www.douyin.com/video/{row['aweme_id']}"
    return row


def _safe_int(value, default=0):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0):
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _clamp(value, lower=0, upper=100):
    return max(lower, min(upper, float(value)))
