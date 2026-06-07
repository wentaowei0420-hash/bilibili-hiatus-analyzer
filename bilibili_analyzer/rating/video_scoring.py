from __future__ import annotations

import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.export_store import write_dataframe_to_table
from common.file_io import atomic_write_csv
from common.sqlite_utils import connect_sqlite
from bilibili_analyzer.utils import categorize_duration, normalize_timestamp, timestamp_to_date
from .store import rating_store_db_path, source_store_db_path


GRADE_SCORES = {
    "S": 100,
    "A": 85,
    "B": 72,
    "C": 58,
    "D": 38,
}

AUTO_GRADE_THRESHOLDS = (
    ("S", 92),
    ("A", 80),
    ("B", 68),
    ("C", 55),
    ("D", 0),
)

FIELDNAMES = [
    "uploader_name",
    "uploader_id",
    "video_title",
    "bvid",
    "video_url",
    "publish_date",
    "publish_timestamp",
    "age_days",
    "duration_seconds",
    "duration_category",
    "view_count",
    "like_count",
    "coin_count",
    "favorite_count",
    "like_rate",
    "coin_rate",
    "favorite_rate",
    "up_base_score",
    "view_score",
    "like_rate_score",
    "coin_rate_score",
    "favorite_rate_score",
    "interaction_score",
    "freshness_score",
    "duration_score",
    "auto_score",
    "auto_grade",
    "final_score",
    "final_grade",
    "confidence",
    "score_source",
    "score_reasons",
    "missing_metrics",
]

HEADERS = {
    "uploader_name": "uploader_name",
    "uploader_id": "uploader_id",
    "video_title": "video_title",
    "bvid": "bvid",
    "video_url": "video_url",
    "publish_date": "publish_date",
    "publish_timestamp": "publish_timestamp",
    "age_days": "age_days",
    "duration_seconds": "duration_seconds",
    "duration_category": "duration_category",
    "view_count": "view_count",
    "like_count": "like_count",
    "coin_count": "coin_count",
    "favorite_count": "favorite_count",
    "like_rate": "like_rate",
    "coin_rate": "coin_rate",
    "favorite_rate": "favorite_rate",
    "up_base_score": "up_base_score",
    "view_score": "view_score",
    "like_rate_score": "like_rate_score",
    "coin_rate_score": "coin_rate_score",
    "favorite_rate_score": "favorite_rate_score",
    "interaction_score": "interaction_score",
    "freshness_score": "freshness_score",
    "duration_score": "duration_score",
    "auto_score": "auto_score",
    "auto_grade": "auto_grade",
    "final_score": "final_score",
    "final_grade": "final_grade",
    "confidence": "confidence",
    "score_source": "score_source",
    "score_reasons": "score_reasons",
    "missing_metrics": "missing_metrics",
}


def run_bilibili_video_scoring(config):
    scorer = BilibiliVideoScorer(config)
    rows = scorer.score()
    output_path = Path(config.output_csv).parent / "bilibili_video_scores.csv"
    atomic_write_csv(output_path, FIELDNAMES, rows, header_row=HEADERS)
    write_dataframe_to_table(
        rating_store_db_path(config),
        "video_score_current",
        pd.DataFrame(rows, columns=FIELDNAMES),
    )
    return output_path


class BilibiliVideoScorer:
    def __init__(self, config):
        self.config = config
        self.source_db = source_store_db_path(config)

    def score(self) -> list[dict]:
        videos = self._load_video_rows()
        creators = self._load_creator_index()
        rows = []
        for video in videos:
            uploader_id = str(video.get("uploader_id") or "").strip()
            creator = creators.get(uploader_id, {})
            row = self._score_video(video, creator)
            if row:
                rows.append(row)
        rows.sort(
            key=lambda item: (
                -GRADE_SCORES.get(item.get("final_grade"), 0),
                -_safe_float(item.get("final_score"), 0),
                str(item.get("publish_timestamp") or 0),
            )
        )
        return rows

    def _load_video_rows(self) -> list[dict]:
        if not self.source_db.exists():
            return []
        rows = []
        with connect_sqlite(self.source_db) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name='bilibili_video_state'"
            ).fetchone()
            if not exists:
                return []
            query_rows = conn.execute(
                """
                SELECT video_id, uploader_id, uploader_name, publish_timestamp,
                       like_count, coin_count, favorite_count, view_count,
                       duration_seconds, payload_json
                FROM bilibili_video_state
                WHERE COALESCE(is_available, 1) = 1
                """
            ).fetchall()
        for row in query_rows:
            payload = _safe_json_loads(row["payload_json"])
            payload.setdefault("bvid", str(row["video_id"] or "").strip())
            payload.setdefault("uploader_id", str(row["uploader_id"] or "").strip())
            payload.setdefault("uploader_name", str(row["uploader_name"] or "").strip())
            payload.setdefault("publish_timestamp", row["publish_timestamp"])
            payload.setdefault("like_count", row["like_count"])
            payload.setdefault("coin_count", row["coin_count"])
            payload.setdefault("favorite_count", row["favorite_count"])
            payload.setdefault("view_count", row["view_count"])
            payload.setdefault("duration_seconds", row["duration_seconds"])
            rows.append(payload)
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

    def _score_video(self, video: dict, creator: dict) -> dict | None:
        bvid = str(video.get("bvid") or video.get("video_id") or "").strip()
        uploader_id = str(video.get("uploader_id") or "").strip()
        if not bvid or not uploader_id:
            return None

        publish_timestamp = normalize_timestamp(video.get("publish_timestamp"))
        age_days = _age_days(publish_timestamp)
        view_count = _safe_int(video.get("view_count"), 0)
        like_count = _safe_int(video.get("like_count"), 0)
        coin_count = _safe_int(video.get("coin_count"), 0)
        favorite_count = _safe_int(video.get("favorite_count"), 0)
        duration_seconds = _safe_int(video.get("duration_seconds"), 0)
        like_rate = _ratio(like_count, view_count)
        coin_rate = _ratio(coin_count, view_count)
        favorite_rate = _ratio(favorite_count, view_count)

        missing = []
        if view_count <= 0:
            missing.append("view_count")
        if like_count <= 0:
            missing.append("like_count")
        if coin_count <= 0:
            missing.append("coin_count")
        if favorite_count <= 0:
            missing.append("favorite_count")

        up_base_score = _creator_influence_score(
            _safe_int(_pick(creator, "follower_count", "粉丝数"), 0),
            _safe_int(_pick(creator, "total_view_count", "播放总数"), 0),
            _safe_int(_pick(creator, "total_favorited", "获赞总数"), 0),
        )
        view_score = _log_score(view_count, 500, 2_000_000)
        like_rate_score = _ratio_score(like_rate, 0.015, 0.12)
        coin_rate_score = _ratio_score(coin_rate, 0.0015, 0.03)
        favorite_rate_score = _ratio_score(favorite_rate, 0.0015, 0.03)
        interaction_score = _log_score(like_count + coin_count * 3 + favorite_count * 2, 20, 200_000)
        freshness_score = _freshness_score(age_days)
        duration_score = _duration_score(duration_seconds)

        auto_score = _clamp(
            up_base_score * 0.10
            + view_score * 0.22
            + like_rate_score * 0.16
            + coin_rate_score * 0.20
            + favorite_rate_score * 0.20
            + interaction_score * 0.08
            + freshness_score * 0.02
            + duration_score * 0.02
        )
        confidence = _video_confidence(view_count, missing)
        final_score = auto_score
        final_grade = _grade_from_score(final_score)

        reasons = []
        if favorite_rate_score >= 85:
            reasons.append("收藏转化强")
        if coin_rate_score >= 85:
            reasons.append("投币转化强")
        if view_score >= 85:
            reasons.append("播放表现强")
        if freshness_score >= 90:
            reasons.append("近期新作")
        if not reasons:
            reasons.append("综合表现稳定")

        return {
            "uploader_name": str(
                video.get("uploader_name")
                or _pick(creator, "uploader_name", "uname", "UP主姓名")
                or ""
            ).strip(),
            "uploader_id": uploader_id,
            "video_title": str(video.get("video_title") or "").strip(),
            "bvid": bvid,
            "video_url": str(video.get("video_url") or f"https://www.bilibili.com/video/{bvid}").strip(),
            "publish_date": str(video.get("publish_date") or timestamp_to_date(publish_timestamp)).strip(),
            "publish_timestamp": publish_timestamp,
            "age_days": age_days,
            "duration_seconds": duration_seconds,
            "duration_category": str(video.get("duration_category") or categorize_duration(duration_seconds)).strip(),
            "view_count": view_count,
            "like_count": like_count,
            "coin_count": coin_count,
            "favorite_count": favorite_count,
            "like_rate": round(like_rate * 100, 4),
            "coin_rate": round(coin_rate * 100, 4),
            "favorite_rate": round(favorite_rate * 100, 4),
            "up_base_score": round(up_base_score, 2),
            "view_score": round(view_score, 2),
            "like_rate_score": round(like_rate_score, 2),
            "coin_rate_score": round(coin_rate_score, 2),
            "favorite_rate_score": round(favorite_rate_score, 2),
            "interaction_score": round(interaction_score, 2),
            "freshness_score": round(freshness_score, 2),
            "duration_score": round(duration_score, 2),
            "auto_score": round(auto_score, 2),
            "auto_grade": final_grade,
            "final_score": round(final_score, 2),
            "final_grade": final_grade,
            "confidence": confidence,
            "score_source": "auto",
            "score_reasons": "、".join(reasons),
            "missing_metrics": ",".join(missing),
        }


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


def _ratio(numerator, denominator):
    denominator = max(_safe_float(denominator, 0.0), 0.0)
    if denominator <= 0:
        return 0.0
    return max(_safe_float(numerator, 0.0), 0.0) / denominator


def _clamp(value, minimum=0.0, maximum=100.0):
    return max(minimum, min(maximum, float(value or 0.0)))


def _log_score(value, lower, upper):
    value = max(_safe_float(value, 0.0), 0.0)
    lower = max(float(lower), 1.0)
    upper = max(float(upper), lower + 1.0)
    if value <= 0:
        return 0.0
    ratio = (math.log10(value + 1) - math.log10(lower + 1)) / (
        math.log10(upper + 1) - math.log10(lower + 1)
    )
    return _clamp(ratio * 100)


def _ratio_score(value, lower, upper):
    lower = float(lower)
    upper = max(float(upper), lower + 1e-9)
    ratio = (max(float(value or 0.0), 0.0) - lower) / (upper - lower)
    return _clamp(ratio * 100)


def _creator_influence_score(follower_count, total_view_count, total_like_count):
    follower_score = _log_score(follower_count, 500, 5_000_000)
    total_view_score = _log_score(total_view_count, 20_000, 500_000_000)
    total_like_score = _log_score(total_like_count, 2_000, 50_000_000)
    return _clamp(follower_score * 0.38 + total_view_score * 0.34 + total_like_score * 0.28)


def _freshness_score(age_days):
    age_days = max(_safe_float(age_days, 0.0), 0.0)
    if age_days <= 14:
        return 100.0
    if age_days <= 30:
        return 95.0
    if age_days <= 90:
        return 86.0
    if age_days <= 180:
        return 75.0
    if age_days <= 365:
        return 62.0
    if age_days <= 730:
        return 48.0
    return 36.0


def _duration_score(duration_seconds):
    duration_seconds = _safe_int(duration_seconds, 0)
    if duration_seconds <= 0:
        return 60.0
    if duration_seconds <= 30:
        return 72.0
    if duration_seconds <= 60:
        return 82.0
    if duration_seconds <= 240:
        return 100.0
    if duration_seconds <= 1800:
        return 92.0
    return 84.0


def _grade_from_score(score):
    for grade, threshold in AUTO_GRADE_THRESHOLDS:
        if _safe_float(score, 0) >= threshold:
            return grade
    return "D"


def _video_confidence(view_count, missing):
    if view_count >= 5_000 and not missing:
        return "高"
    if view_count >= 800 and len(missing) <= 1:
        return "中"
    return "低"


def _age_days(timestamp):
    timestamp = normalize_timestamp(timestamp)
    if timestamp <= 0:
        return 0.0
    return round(max((datetime.now() - datetime.fromtimestamp(timestamp)).total_seconds() / 86400, 0), 2)
