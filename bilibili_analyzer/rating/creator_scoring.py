from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.export_store import write_dataframe_to_table
from common.file_io import atomic_write_csv
from common.sqlite_utils import connect_sqlite
from common.platform_store import read_summary_rows
from bilibili_analyzer.utils import build_homepage_url, normalize_timestamp, timestamp_to_date
from .store import rating_store_db_path, source_store_db_path
from .video_scoring import GRADE_SCORES, run_bilibili_video_scoring


FIELDNAMES = [
    "uploader_name",
    "uploader_id",
    "homepage_url",
    "manual_grade",
    "auto_score",
    "auto_grade",
    "final_score",
    "final_grade",
    "confidence",
    "score_source",
    "follower_count",
    "total_view_count",
    "total_like_count",
    "published_video_count",
    "cached_video_count",
    "scored_video_count",
    "latest_publish_date",
    "inactive_days",
    "avg_update_days",
    "earliest_publish_date",
    "creator_span_days",
    "avg_view_count",
    "avg_like_count",
    "avg_coin_count",
    "avg_favorite_count",
    "video_grade_score",
    "low_grade_ratio",
    "recent_trend_score",
    "risk_penalty",
    "follower_score",
    "total_view_score",
    "total_like_score",
    "recent_update_score",
    "update_frequency_score",
    "video_count_score",
    "history_span_score",
    "avg_view_score",
    "avg_like_score",
    "avg_coin_score",
    "avg_favorite_score",
    "low_grade_ratio_score",
    "grade_s_count",
    "grade_a_count",
    "grade_b_count",
    "grade_c_count",
    "grade_d_count",
    "score_reasons",
    "missing_metrics",
]

HEADERS = {field: field for field in FIELDNAMES}

VIDEO_GRADE_VALUES = {
    "S": 100,
    "A": 90,
    "B": 72,
    "C": 55,
    "D": 36,
}


def run_bilibili_creator_scoring(config):
    scorer = BilibiliCreatorScorer(config)
    rows = scorer.score()
    output_path = Path(config.output_csv).parent / "bilibili_creator_scores.csv"
    atomic_write_csv(output_path, FIELDNAMES, rows, header_row=HEADERS)
    write_dataframe_to_table(
        rating_store_db_path(config),
        "creator_score_current",
        pd.DataFrame(rows, columns=FIELDNAMES),
    )
    return output_path


class BilibiliCreatorScorer:
    def __init__(self, config):
        self.config = config
        self.source_db = source_store_db_path(config)
        self.rating_db = rating_store_db_path(config)

    def score(self) -> list[dict]:
        if self._sqlite_table_count(self.rating_db, "video_score_current") <= 0:
            run_bilibili_video_scoring(self.config)
        creators = self._load_creator_index()
        analysis_rows = self._load_analysis_index()
        manual_grades = self._load_manual_grades()
        video_rows = self._load_video_scores()
        grouped_videos = {}
        for row in video_rows:
            grouped_videos.setdefault(str(row.get("uploader_id") or "").strip(), []).append(row)

        uploader_ids = sorted(set(creators) | set(grouped_videos))
        rows = []
        for uploader_id in uploader_ids:
            row = self._score_creator(
                uploader_id,
                creators.get(uploader_id, {}),
                analysis_rows.get(uploader_id, {}),
                grouped_videos.get(uploader_id, []),
                manual_grades.get(uploader_id, {}),
            )
            if row:
                rows.append(row)
        rows.sort(
            key=lambda item: (
                -GRADE_SCORES.get(item.get("final_grade"), 0),
                -_safe_float(item.get("final_score"), 0),
                str(item.get("uploader_name") or ""),
            )
        )
        return rows

    def _load_creator_index(self) -> dict[str, dict]:
        if not self.source_db.exists():
            return {}
        creators = {}
        with connect_sqlite(self.source_db) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bilibili_creator_raw'"
            ).fetchone()
            if not exists:
                return {}
            rows = conn.execute(
                "SELECT uploader_id, payload_json FROM bilibili_creator_raw"
            ).fetchall()
        for row in rows:
            payload = _safe_json_loads(row["payload_json"])
            creators[str(row["uploader_id"] or "").strip()] = payload
        return creators

    def _load_analysis_index(self) -> dict[str, dict]:
        rows = read_summary_rows(self.source_db, "bilibili", "analysis")
        index = {}
        for row in rows or []:
            if not isinstance(row, dict):
                continue
            uploader_id = str(_pick(row, "uploader_id", "UP主UID") or "").strip()
            if uploader_id:
                index[uploader_id] = row
        return index

    def _load_video_scores(self) -> list[dict]:
        if not self.rating_db.exists():
            return []
        with connect_sqlite(self.rating_db) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='video_score_current'"
            ).fetchone()
            if not exists:
                return []
            return [dict(row) for row in conn.execute('SELECT * FROM "video_score_current"').fetchall()]

    def _load_manual_grades(self) -> dict[str, dict]:
        if not self.rating_db.exists():
            return {}
        with connect_sqlite(self.rating_db) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS bilibili_creator_manual_rating (
                    uploader_id TEXT PRIMARY KEY,
                    manual_grade TEXT NOT NULL,
                    note TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            rows = conn.execute('SELECT * FROM "bilibili_creator_manual_rating"').fetchall()
        return {str(row["uploader_id"] or "").strip(): dict(row) for row in rows}

    def _score_creator(self, uploader_id, creator, analysis, videos, manual_row):
        if not uploader_id:
            return None
        videos = list(videos or [])
        creator = creator if isinstance(creator, dict) else {}
        analysis = analysis if isinstance(analysis, dict) else {}

        follower_count = _safe_int(_pick(creator, "follower_count", "粉丝数"), 0)
        total_view_count = _safe_int(_pick(creator, "total_view_count", "播放总数"), 0)
        total_like_count = _safe_int(_pick(creator, "total_favorited", "获赞总数"), 0)
        cached_video_count = len(videos)
        published_video_count = max(
            cached_video_count,
            _safe_int(_pick(creator, "published_video_count", "发布视频数量"), 0),
            _safe_int(_pick(analysis, "total_videos", "视频总数"), 0),
        )
        publish_timestamps = sorted(
            [normalize_timestamp(video.get("publish_timestamp")) for video in videos if normalize_timestamp(video.get("publish_timestamp")) > 0]
        )
        latest_publish_ts = publish_timestamps[-1] if publish_timestamps else 0
        earliest_publish_ts = publish_timestamps[0] if publish_timestamps else 0
        inactive_days = _age_days(latest_publish_ts) if latest_publish_ts else _safe_float(
            _pick(creator, "days_since_update", "未更新天数"),
            0.0,
        )
        creator_span_days = (
            round(max((latest_publish_ts - earliest_publish_ts) / 86400, 0), 2)
            if latest_publish_ts and earliest_publish_ts
            else 0.0
        )

        avg_update_days = _safe_float(
            _pick(
                analysis,
                "average_update_interval_days",
                "平均几天一更",
                default=_pick(creator, "average_update_interval_days", "平均几天一更"),
            ),
            0.0,
        )
        avg_view_count = _average(videos, "view_count")
        avg_like_count = _average(videos, "like_count")
        avg_coin_count = _average(videos, "coin_count")
        avg_favorite_count = _average(videos, "favorite_count")

        grade_counts = {grade: 0 for grade in VIDEO_GRADE_VALUES}
        for video in videos:
            grade = str(video.get("final_grade") or "").strip().upper()
            if grade in grade_counts:
                grade_counts[grade] += 1
        scored_video_count = sum(grade_counts.values())
        video_grade_score = _video_grade_score(grade_counts)
        low_grade_ratio = _low_grade_ratio(grade_counts)
        recent_trend_score = _recent_trend_score(videos)

        follower_score = _log_score(follower_count, 500, 5_000_000)
        total_view_score = _log_score(total_view_count, 20_000, 500_000_000)
        total_like_score = _log_score(total_like_count, 2_000, 50_000_000)
        recent_update_score = _recent_update_score(inactive_days)
        update_frequency_score = _update_frequency_score(avg_update_days)
        video_count_score = _log_score(published_video_count, 5, 800)
        history_span_score = _history_span_score(creator_span_days)
        avg_view_score = _log_score(avg_view_count, 500, 500_000)
        avg_like_score = _log_score(avg_like_count, 50, 50_000)
        avg_coin_score = _log_score(avg_coin_count, 5, 10_000)
        avg_favorite_score = _log_score(avg_favorite_count, 5, 10_000)
        low_grade_ratio_score = _clamp(100 - low_grade_ratio * 100)

        risk_penalty = 0.0
        if scored_video_count < 5:
            risk_penalty += 8.0
        if inactive_days >= 180:
            risk_penalty += 6.0
        if inactive_days >= 365:
            risk_penalty += 10.0
        if low_grade_ratio >= 0.5:
            risk_penalty += 6.0

        metric_values = {
            "follower_score": follower_score,
            "total_view_score": total_view_score,
            "total_like_score": total_like_score,
            "recent_update_score": recent_update_score,
            "update_frequency_score": update_frequency_score,
            "video_count_score": video_count_score,
            "history_span_score": history_span_score,
            "avg_view_score": avg_view_score,
            "avg_like_score": avg_like_score,
            "avg_coin_score": avg_coin_score,
            "avg_favorite_score": avg_favorite_score,
            "video_grade_score": video_grade_score,
            "low_grade_ratio_score": low_grade_ratio_score,
            "recent_trend_score": recent_trend_score,
        }
        auto_score = _clamp(
            follower_score * 0.07
            + total_view_score * 0.08
            + total_like_score * 0.07
            + recent_update_score * 0.10
            + update_frequency_score * 0.08
            + video_count_score * 0.07
            + history_span_score * 0.05
            + avg_view_score * 0.09
            + avg_like_score * 0.08
            + avg_coin_score * 0.10
            + avg_favorite_score * 0.10
            + video_grade_score * 0.13
            + low_grade_ratio_score * 0.07
            + recent_trend_score * 0.11
            - risk_penalty
        )
        auto_grade = _grade_from_score(auto_score)

        manual_grade = str((manual_row or {}).get("manual_grade") or "").strip().upper()
        final_score = GRADE_SCORES.get(manual_grade, auto_score)
        final_grade = manual_grade or auto_grade
        confidence = _creator_confidence(scored_video_count, creator, videos)
        missing = []
        if follower_count <= 0:
            missing.append("follower_count")
        if total_view_count <= 0:
            missing.append("total_view_count")
        if total_like_count <= 0:
            missing.append("total_like_count")
        if scored_video_count <= 0:
            missing.append("video_scores")

        reasons = []
        if avg_coin_score >= 80:
            reasons.append("平均投币强")
        if avg_favorite_score >= 80:
            reasons.append("平均收藏强")
        if video_grade_score >= 82:
            reasons.append("视频质量稳定")
        if recent_update_score >= 88:
            reasons.append("近期活跃")
        if recent_trend_score >= 82:
            reasons.append("近期走势向上")
        if not reasons:
            reasons.append("综合表现平稳")

        uploader_name = str(
            _pick(creator, "uploader_name", "uname", "UP主姓名", default=uploader_id)
        ).strip()
        homepage_url = str(
            _pick(creator, "uploader_homepage", "UP主主页链接", default=build_homepage_url(uploader_id))
        ).strip()

        return {
            "uploader_name": uploader_name,
            "uploader_id": uploader_id,
            "homepage_url": homepage_url,
            "manual_grade": manual_grade,
            "auto_score": round(auto_score, 2),
            "auto_grade": auto_grade,
            "final_score": round(final_score, 2),
            "final_grade": final_grade,
            "confidence": confidence,
            "score_source": "manual" if manual_grade else "auto",
            "follower_count": follower_count,
            "total_view_count": total_view_count,
            "total_like_count": total_like_count,
            "published_video_count": published_video_count,
            "cached_video_count": cached_video_count,
            "scored_video_count": scored_video_count,
            "latest_publish_date": (
                timestamp_to_date(latest_publish_ts)
                if latest_publish_ts
                else str(_pick(creator, "upload_date", "最后活跃/发布日期") or "")
            ),
            "inactive_days": round(inactive_days, 2),
            "avg_update_days": round(avg_update_days, 2),
            "earliest_publish_date": timestamp_to_date(earliest_publish_ts) if earliest_publish_ts else "",
            "creator_span_days": round(creator_span_days, 2),
            "avg_view_count": round(avg_view_count, 2),
            "avg_like_count": round(avg_like_count, 2),
            "avg_coin_count": round(avg_coin_count, 2),
            "avg_favorite_count": round(avg_favorite_count, 2),
            "video_grade_score": round(video_grade_score, 2),
            "low_grade_ratio": round(low_grade_ratio, 4),
            "recent_trend_score": round(recent_trend_score, 2),
            "risk_penalty": round(risk_penalty, 2),
            "follower_score": round(follower_score, 2),
            "total_view_score": round(total_view_score, 2),
            "total_like_score": round(total_like_score, 2),
            "recent_update_score": round(recent_update_score, 2),
            "update_frequency_score": round(update_frequency_score, 2),
            "video_count_score": round(video_count_score, 2),
            "history_span_score": round(history_span_score, 2),
            "avg_view_score": round(avg_view_score, 2),
            "avg_like_score": round(avg_like_score, 2),
            "avg_coin_score": round(avg_coin_score, 2),
            "avg_favorite_score": round(avg_favorite_score, 2),
            "low_grade_ratio_score": round(low_grade_ratio_score, 2),
            "grade_s_count": grade_counts["S"],
            "grade_a_count": grade_counts["A"],
            "grade_b_count": grade_counts["B"],
            "grade_c_count": grade_counts["C"],
            "grade_d_count": grade_counts["D"],
            "score_reasons": "、".join(reasons),
            "missing_metrics": ",".join(missing),
        }

    @staticmethod
    def _sqlite_table_count(db_path, table_name):
        db_path = Path(db_path)
        if not db_path.exists():
            return 0
        with connect_sqlite(db_path) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            ).fetchone()
            if not exists:
                return 0
            return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0] or 0)


def _safe_json_loads(value):
    try:
        payload = json.loads(value or "{}")
    except Exception:
        payload = {}
    return payload if isinstance(payload, dict) else {}


def _pick(mapping, *keys, default=""):
    mapping = mapping if isinstance(mapping, dict) else {}
    for key in keys:
        if key in mapping and mapping.get(key) not in (None, ""):
            return mapping.get(key)
    return default


def _safe_int(value, default=0):
    try:
        text = str(value or "").replace(",", "").strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0):
    try:
        text = str(value or "").replace(",", "").strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _average(rows, key):
    values = [_safe_float(row.get(key), None) for row in rows if row.get(key) not in (None, "")]
    values = [value for value in values if value is not None]
    if not values:
        return 0.0
    return sum(values) / len(values)


def _clamp(value, minimum=0.0, maximum=100.0):
    return max(minimum, min(maximum, float(value or 0.0)))


def _log_score(value, lower, upper):
    value = max(_safe_float(value, 0.0), 0.0)
    lower = max(float(lower), 1.0)
    upper = max(float(upper), lower + 1.0)
    if value <= 0:
        return 0.0
    import math

    ratio = (math.log10(value + 1) - math.log10(lower + 1)) / (
        math.log10(upper + 1) - math.log10(lower + 1)
    )
    return _clamp(ratio * 100)


def _video_grade_score(grade_counts):
    total = sum(_safe_int(grade_counts.get(grade), 0) for grade in VIDEO_GRADE_VALUES)
    if total <= 0:
        return 0.0
    return sum(_safe_int(grade_counts.get(grade), 0) * score for grade, score in VIDEO_GRADE_VALUES.items()) / total


def _low_grade_ratio(grade_counts):
    total = sum(_safe_int(grade_counts.get(grade), 0) for grade in VIDEO_GRADE_VALUES)
    if total <= 0:
        return 1.0
    return (_safe_int(grade_counts.get("C"), 0) + _safe_int(grade_counts.get("D"), 0)) / total


def _recent_trend_score(videos):
    if not videos:
        return 0.0
    ordered = sorted(videos, key=lambda item: _safe_int(item.get("publish_timestamp"), 0))
    recent = ordered[-10:]
    baseline = ordered[:-10] or ordered
    recent_avg = _average(recent, "final_score")
    baseline_avg = _average(baseline, "final_score")
    if baseline_avg <= 0:
        return _clamp(recent_avg)
    ratio = recent_avg / baseline_avg
    return _clamp(60 + (ratio - 1.0) * 80)


def _recent_update_score(inactive_days):
    inactive_days = max(_safe_float(inactive_days, 0.0), 0.0)
    if inactive_days <= 7:
        return 100.0
    if inactive_days <= 30:
        return 92.0
    if inactive_days <= 90:
        return 78.0
    if inactive_days <= 180:
        return 62.0
    if inactive_days <= 365:
        return 42.0
    return 24.0


def _update_frequency_score(avg_update_days):
    avg_update_days = _safe_float(avg_update_days, 0.0)
    if avg_update_days <= 0:
        return 45.0
    if avg_update_days <= 3:
        return 100.0
    if avg_update_days <= 7:
        return 90.0
    if avg_update_days <= 14:
        return 78.0
    if avg_update_days <= 30:
        return 62.0
    if avg_update_days <= 60:
        return 48.0
    return 35.0


def _history_span_score(span_days):
    span_days = max(_safe_float(span_days, 0.0), 0.0)
    if span_days <= 0:
        return 25.0
    if span_days <= 30:
        return 45.0
    if span_days <= 180:
        return 70.0
    if span_days <= 365:
        return 84.0
    if span_days <= 1095:
        return 95.0
    return 100.0


def _grade_from_score(score):
    score = _safe_float(score, 0.0)
    if score >= 92:
        return "S"
    if score >= 80:
        return "A"
    if score >= 68:
        return "B"
    if score >= 55:
        return "C"
    return "D"


def _creator_confidence(scored_video_count, creator, videos):
    if scored_video_count >= 20 and creator and len(videos) >= 20:
        return "高"
    if scored_video_count >= 8:
        return "中"
    return "低"


def _age_days(timestamp):
    timestamp = normalize_timestamp(timestamp)
    if timestamp <= 0:
        return 0.0
    return round(max((datetime.now() - datetime.fromtimestamp(timestamp)).total_seconds() / 86400, 0), 2)
