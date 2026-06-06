from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


BILIBILI_CREATOR_BUCKETS = (
    ("0~50", 0, 50),
    ("51~300", 51, 300),
    ("301~500", 301, 500),
    ("501~1000", 501, 1000),
    ("1001以上", 1001, None),
)

BILIBILI_DURATION_BUCKETS = (
    ("0~30s", 0, 30),
    ("31~60s", 31, 60),
    ("61~240s", 61, 240),
    ("241s以上", 241, None),
)


def _load_bilibili_config():
    from bilibili_analyzer.config import load_analyzer_config

    return load_analyzer_config()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        text = str(value or "").replace(",", "").strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
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


def _is_expired(cached_at: Any, max_age_hours: int) -> bool:
    timestamp = _safe_int(cached_at, 0)
    if timestamp <= 0:
        return True
    import time

    return time.time() - timestamp >= max(1, int(max_age_hours or 1)) * 3600


def _bucket_counts(items: list[int], buckets) -> dict[str, int]:
    counts = {label: 0 for label, _lower, _upper in buckets}
    for value in items:
        for label, lower, upper in buckets:
            if value < lower:
                continue
            if upper is None or value <= upper:
                counts[label] += 1
                break
    return counts


def _read_precise_progress(config) -> tuple[str, dict[str, dict[str, Any]]]:
    path = Path(config.progress_json)
    if not path.exists():
        return "", {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return "", {}
    rows = payload.get("results_by_mid", {})
    return (
        str(payload.get("saved_at") or "").strip(),
        rows if isinstance(rows, dict) else {},
    )


def _read_duration_progress(config) -> tuple[str, dict[str, dict[str, Any]]]:
    from bilibili_analyzer.cache import CacheStore

    manifest_path = Path(config.video_duration_progress_json)
    saved_at = ""
    if manifest_path.exists():
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            saved_at = str(manifest.get("saved_at") or "").strip()
        except Exception:
            saved_at = ""
    progress = CacheStore(config).load_video_duration_progress()
    return saved_at, progress if isinstance(progress, dict) else {}


def _read_export_rows(config, table_name: str) -> list[dict[str, Any]]:
    db_path = Path(config.export_store_db)
    if not db_path.exists():
        return []
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        if not _table_exists(conn, table_name):
            return []
        rows = conn.execute(f'SELECT * FROM "{table_name}"').fetchall()
    return [dict(row) for row in rows]


def _read_video_rows(config) -> list[dict[str, Any]]:
    db_path = Path(config.export_store_db)
    if not db_path.exists():
        return []
    rows: list[dict[str, Any]] = []
    with sqlite3.connect(str(db_path), timeout=5) as conn:
        conn.row_factory = sqlite3.Row
        if _table_exists(conn, "bilibili_video_state"):
            state_count = conn.execute('SELECT COUNT(*) FROM "bilibili_video_state"').fetchone()[0]
            if state_count:
                state_rows = conn.execute(
                    """
                    SELECT video_id, uploader_id, uploader_name, publish_timestamp,
                           like_count, coin_count, favorite_count, view_count, duration_seconds
                    FROM "bilibili_video_state"
                    WHERE COALESCE(is_available, 1) = 1
                    """
                ).fetchall()
                return [dict(row) for row in state_rows]
        if not _table_exists(conn, "bilibili_video_raw"):
            return []
        raw_rows = conn.execute('SELECT payload_json FROM "bilibili_video_raw"').fetchall()
    for raw_row in raw_rows:
        try:
            payload = json.loads(raw_row["payload_json"])
        except Exception:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _creator_rows_from_precise(precise_rows: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows = []
    for uploader_id, payload in (precise_rows or {}).items():
        if not isinstance(payload, dict):
            continue
        rows.append(
            {
                "UP主UID": str(payload.get("uploader_id") or uploader_id or "").strip(),
                "UP主姓名": str(payload.get("uploader_name") or "").strip(),
                "UP主主页链接": str(payload.get("uploader_homepage") or "").strip(),
                "发布视频数量": _safe_int(payload.get("published_video_count"), 0),
            }
        )
    return rows


def get_bilibili_stats(high_like_threshold: int = 10000) -> dict[str, Any]:
    config = _load_bilibili_config()
    precise_saved_at, precise_rows = _read_precise_progress(config)
    duration_saved_at, duration_progress = _read_duration_progress(config)
    main_rows = _read_export_rows(config, "main_sheet_current")
    analysis_rows = _read_export_rows(config, "analysis_sheet_current")
    creator_rows = main_rows or _creator_rows_from_precise(precise_rows)
    total_followings = max(
        len(creator_rows),
        len(precise_rows),
        len(analysis_rows),
        len(duration_progress),
    )
    basic_count = max(len(precise_rows), len(main_rows))
    full_count = max(len(duration_progress), len(analysis_rows))

    valid_count = 0
    expired_count = 0
    for entry in (duration_progress or {}).values():
        if not isinstance(entry, dict):
            continue
        summary = entry.get("summary", {})
        if not isinstance(summary, dict) or not summary:
            expired_count += 1
            continue
        if _is_expired(entry.get("cached_at"), config.video_duration_cache_max_age_hours):
            expired_count += 1
        else:
            valid_count += 1
    if not duration_progress and analysis_rows:
        valid_count = len(analysis_rows)
        expired_count = 0
    if full_count < valid_count + expired_count:
        full_count = valid_count + expired_count
    unfetched_count = max(total_followings - full_count, 0)

    creator_video_counts = [
        _safe_int(row.get("视频总数", row.get("发布视频数量")), 0)
        for row in creator_rows
    ]
    creator_buckets = _bucket_counts(creator_video_counts, BILIBILI_CREATOR_BUCKETS)

    if analysis_rows:
        duration_buckets = {
            "0~30s": sum(_safe_int(row.get("短视频数量(0~30s)"), 0) for row in analysis_rows),
            "31~60s": sum(_safe_int(row.get("中视频数量(30~60s)"), 0) for row in analysis_rows),
            "61~240s": sum(_safe_int(row.get("中长视频数量(60~240s)"), 0) for row in analysis_rows),
            "241s以上": sum(_safe_int(row.get("长视频数量(240s+)"), 0) for row in analysis_rows),
        }
    else:
        video_rows = _read_video_rows(config)
        duration_buckets = _bucket_counts(
            [_safe_int(row.get("duration_seconds"), 0) for row in video_rows],
            BILIBILI_DURATION_BUCKETS,
        )

    video_rows = _read_video_rows(config)
    cached_video_count = len(video_rows)
    high_like_video_count = sum(
        1
        for row in video_rows
        if _safe_int(row.get("like_count"), 0) > int(high_like_threshold or 10000)
    )

    return {
        "total_followings": total_followings,
        "precise_cached_at": precise_saved_at,
        "full_cached_at": duration_saved_at,
        "progress_count": basic_count,
        "modes": {
            "basic": {
                "count": basic_count,
                "percent": (basic_count / total_followings * 100) if total_followings else 0,
            },
            "full": {
                "count": full_count,
                "percent": (full_count / total_followings * 100) if total_followings else 0,
                "valid_count": valid_count,
                "expired_count": expired_count,
                "unfetched_count": unfetched_count,
            },
        },
        "cached_video_count": cached_video_count,
        "high_like_video_count": high_like_video_count,
        "high_like_ratio": (high_like_video_count / cached_video_count * 100) if cached_video_count else 0,
        "creator_buckets": creator_buckets,
        "duration_buckets": duration_buckets,
    }


def get_bilibili_video_count_stats(min_video_count: int = 1000) -> dict[str, Any]:
    config = _load_bilibili_config()
    threshold = max(int(min_video_count or 0), 0)
    precise_saved_at, precise_rows = _read_precise_progress(config)
    _duration_saved_at, duration_progress = _read_duration_progress(config)
    main_rows = _read_export_rows(config, "main_sheet_current")
    creator_rows = main_rows or _creator_rows_from_precise(precise_rows)
    full_uid_set = {str(uid).strip() for uid in (duration_progress or {}).keys() if str(uid).strip()}
    rows = []
    for row in creator_rows:
        uploader_id = str(row.get("UP主UID") or "").strip()
        if not uploader_id:
            continue
        published_video_count = _safe_int(row.get("发布视频数量"), 0)
        if published_video_count <= threshold:
            continue
        rows.append(
            {
                "uploader_id": uploader_id,
                "uploader_name": str(row.get("UP主姓名") or "").strip() or uploader_id,
                "published_video_count": published_video_count,
                "has_full_fetch": uploader_id in full_uid_set,
                "homepage_url": str(row.get("UP主主页链接") or "").strip(),
            }
        )
    rows.sort(key=lambda item: (-item["published_video_count"], item["uploader_name"]))
    return {
        "threshold": threshold,
        "total_followings": max(len(creator_rows), len(precise_rows)),
        "precise_cached_at": precise_saved_at,
        "rows": rows,
    }
