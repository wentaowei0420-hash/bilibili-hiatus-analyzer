from __future__ import annotations

import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from common.sqlite_utils import connect_sqlite
from bilibili_analyzer.archive import load_archived_creators
from bilibili_analyzer.config import load_analyzer_config
from bilibili_analyzer.rating.store import rating_store_db_path


CREATOR_TABLE = "creator_score_current"
VIDEO_TABLE = "video_score_current"
GRADE_ORDER = ("S", "A", "B", "C", "D")
FACTOR_KEYS = [
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
    "video_grade_score",
    "low_grade_ratio_score",
    "recent_trend_score",
    "risk_penalty",
]


def _load_config():
    return load_analyzer_config()


def _table_exists(conn, table_name: str) -> bool:
    row = conn.execute(
        """
        SELECT 1 FROM sqlite_master WHERE type IN ('table', 'view') AND name=?
        UNION ALL
        SELECT 1 FROM sqlite_temp_master WHERE type IN ('table', 'view') AND name=?
        LIMIT 1
        """,
        (table_name, table_name),
    ).fetchone()
    return row is not None


def _safe_float(value, default=0.0) -> float:
    try:
        text = str(value or "").replace(",", "").strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _safe_int(value, default=0) -> int:
    try:
        text = str(value or "").replace(",", "").strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def get_rating_overview(search_uid: str = "") -> dict[str, Any]:
    config = _load_config()
    db_path = rating_store_db_path(config)
    archive_db = Path(config.export_store_db)
    if not db_path.exists():
        return {"ok": False, "message": "未找到 B 站评分数据库，请先运行 B 站评分。"}

    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, CREATOR_TABLE) or not _table_exists(conn, VIDEO_TABLE):
            return {"ok": False, "message": "未找到评分表，请先运行 B 站评分。"}
        creator_rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{CREATOR_TABLE}"').fetchall()]
        video_rows = [dict(row) for row in conn.execute(f'SELECT * FROM "{VIDEO_TABLE}"').fetchall()]

    active_archived_uids = {
        str(row.get("uploader_id") or "").strip()
        for row in load_archived_creators(archive_db, active_only=True)
    }
    current_rows = [
        row
        for row in creator_rows
        if str(row.get("uploader_id") or "").strip() not in active_archived_uids
    ]
    if search_uid:
        target = str(search_uid or "").strip()
        current_rows = [row for row in current_rows if str(row.get("uploader_id") or "").strip() == target]
        video_rows = [row for row in video_rows if str(row.get("uploader_id") or "").strip() == target]

    current_rows.sort(
        key=lambda item: (-_safe_float(item.get("final_score"), 0), str(item.get("uploader_name") or ""))
    )
    watch_rows = [
        row for row in current_rows
        if str(row.get("final_grade") or "").strip().upper() in {"C", "D"}
        or _safe_float(row.get("inactive_days"), 0) >= 90
        or _safe_float(row.get("low_grade_ratio"), 0) >= 0.40
    ]
    archived_rows = load_archived_creators(archive_db, active_only=False)
    if search_uid:
        archived_rows = [
            row for row in archived_rows if str(row.get("uploader_id") or "").strip() == str(search_uid or "").strip()
        ]

    return {
        "ok": True,
        "message": "B站评分数据已加载",
        "summary": {
            "creator": _grade_summary(current_rows, "confidence"),
            "video": _grade_summary(video_rows, "confidence"),
        },
        "tables": {
            "current": [_creator_table_row(row) for row in current_rows],
            "watch": [_watch_table_row(row) for row in watch_rows],
            "archived": [_archived_table_row(row) for row in archived_rows],
        },
        "updated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "db_path": str(db_path),
        "archive_db_path": str(archive_db),
    }


def get_creator_detail(uploader_id: str) -> dict[str, Any]:
    uploader_id = str(uploader_id or "").strip()
    config = _load_config()
    db_path = rating_store_db_path(config)
    archive_db = Path(config.export_store_db)
    if not uploader_id or not db_path.exists():
        return {}
    with connect_sqlite(db_path) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, CREATOR_TABLE):
            return {}
        creator = conn.execute(
            f'SELECT * FROM "{CREATOR_TABLE}" WHERE uploader_id = ? LIMIT 1',
            (uploader_id,),
        ).fetchone()
        video_rows = []
        if creator:
            video_rows = [
                dict(row)
                for row in conn.execute(
                    f'SELECT * FROM "{VIDEO_TABLE}" WHERE uploader_id = ? ORDER BY publish_timestamp DESC, final_score DESC',
                    (uploader_id,),
                ).fetchall()
            ]
    if creator:
        creator_dict = dict(creator)
    else:
        archived = next(
            (
                row for row in load_archived_creators(archive_db, active_only=False)
                if str(row.get("uploader_id") or "").strip() == uploader_id
            ),
            None,
        )
        if not archived:
            return {}
        creator_dict = {
            "uploader_name": archived.get("uploader_name", ""),
            "uploader_id": archived.get("uploader_id", ""),
            "homepage_url": archived.get("homepage_url", ""),
            "manual_grade": archived.get("manual_grade", ""),
            "final_score": archived.get("final_score", 0),
            "final_grade": archived.get("final_grade", ""),
            "confidence": archived.get("confidence", ""),
            "follower_count": archived.get("follower_count", 0),
            "total_view_count": archived.get("total_view_count", 0),
            "total_like_count": archived.get("total_favorited", 0),
            "published_video_count": archived.get("published_video_count", 0),
            "cached_video_count": archived.get("cached_video_count", 0),
            "scored_video_count": 0,
            "latest_publish_date": archived.get("latest_publish_time", ""),
            "inactive_days": archived.get("inactive_days", 0),
            "avg_update_days": archived.get("avg_update_days", 0),
            "earliest_publish_date": "",
            "creator_span_days": 0,
            "score_source": "archived",
            "score_reasons": archived.get("archive_reason", ""),
            "missing_metrics": "video_scores",
            "low_grade_ratio": 0,
        }
    return {
        "creator": creator_dict,
        "factor_rows": [(key, creator_dict.get(key)) for key in FACTOR_KEYS],
        "grade_rows": _count_pairs(video_rows, "final_grade", GRADE_ORDER),
        "duration_rows": _count_pairs(video_rows, "duration_category"),
        "recent_videos": [
            {
                "video_title": row.get("video_title", ""),
                "bvid": row.get("bvid", ""),
                "publish_date": row.get("publish_date", ""),
                "final_grade": row.get("final_grade", ""),
                "final_score": round(_safe_float(row.get("final_score"), 0), 2),
                "view_count": _safe_int(row.get("view_count"), 0),
                "like_count": _safe_int(row.get("like_count"), 0),
                "coin_count": _safe_int(row.get("coin_count"), 0),
                "favorite_count": _safe_int(row.get("favorite_count"), 0),
                "video_url": row.get("video_url", ""),
            }
            for row in video_rows[:20]
        ],
    }


def save_creator_manual_grade(uploader_id: str, grade: str, note: str = "") -> dict[str, Any]:
    uploader_id = str(uploader_id or "").strip()
    grade = str(grade or "").strip().upper()
    if not uploader_id:
        raise ValueError("Missing uploader_id")
    if grade and grade not in set(GRADE_ORDER):
        raise ValueError(f"Unsupported grade: {grade}")
    db_path = rating_store_db_path(_load_config())
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with connect_sqlite(db_path) as conn:
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
        if grade:
            conn.execute(
                """
                INSERT INTO bilibili_creator_manual_rating (uploader_id, manual_grade, note, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(uploader_id) DO UPDATE SET
                    manual_grade=excluded.manual_grade,
                    note=excluded.note,
                    updated_at=excluded.updated_at
                """,
                (uploader_id, grade, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            )
        else:
            conn.execute(
                "DELETE FROM bilibili_creator_manual_rating WHERE uploader_id = ?",
                (uploader_id,),
            )
        conn.commit()
    return {"ok": True, "uploader_id": uploader_id, "manual_grade": grade}


def _grade_summary(rows: list[dict], confidence_key: str) -> dict[str, Any]:
    counts = {grade: 0 for grade in GRADE_ORDER}
    low_confidence = 0
    for row in rows:
        grade = str(row.get("final_grade") or "").strip().upper()
        if grade in counts:
            counts[grade] += 1
        if str(row.get(confidence_key) or "").strip() in {"低", "中"}:
            low_confidence += 1
    return {"total": len(rows), "counts": counts, "low_confidence": low_confidence}


def _creator_table_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "uploader_name": row.get("uploader_name", ""),
        "final_grade": row.get("final_grade", ""),
        "final_score": round(_safe_float(row.get("final_score"), 0), 2),
        "confidence": row.get("confidence", ""),
        "follower_count": _safe_int(row.get("follower_count"), 0),
        "total_view_count": _safe_int(row.get("total_view_count"), 0),
        "published_video_count": _safe_int(row.get("published_video_count"), 0),
        "uploader_id": row.get("uploader_id", ""),
        "homepage_url": row.get("homepage_url", ""),
    }


def _watch_table_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "uploader_name": row.get("uploader_name", ""),
        "final_grade": row.get("final_grade", ""),
        "final_score": round(_safe_float(row.get("final_score"), 0), 2),
        "confidence": row.get("confidence", ""),
        "inactive_days": round(_safe_float(row.get("inactive_days"), 0), 2),
        "low_grade_ratio": round(_safe_float(row.get("low_grade_ratio"), 0) * 100, 2),
        "homepage_url": row.get("homepage_url", ""),
        "uploader_id": row.get("uploader_id", ""),
    }


def _archived_table_row(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "uploader_name": row.get("uploader_name", ""),
        "archive_status": row.get("archive_status", ""),
        "inactive_days": round(_safe_float(row.get("inactive_days"), 0), 2),
        "latest_publish_time": row.get("latest_publish_time", ""),
        "final_grade": row.get("final_grade", ""),
        "archived_at": row.get("archived_at", ""),
        "archive_reason": row.get("archive_reason", ""),
        "homepage_url": row.get("homepage_url", ""),
        "uploader_id": row.get("uploader_id", ""),
    }


def _count_pairs(rows: list[dict], key: str, order: tuple[str, ...] | None = None) -> list[tuple[str, int]]:
    counts = {}
    for row in rows:
        value = str(row.get(key) or "").strip() or "未知"
        counts[value] = counts.get(value, 0) + 1
    if order:
        ordered = [(name, counts.pop(name, 0)) for name in order if name in counts or name in order]
        return [(name, count) for name, count in ordered if count > 0] + sorted(counts.items())
    return sorted(counts.items(), key=lambda item: (-item[1], item[0]))
