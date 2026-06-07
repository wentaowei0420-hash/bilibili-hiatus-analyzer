from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

from common.platform_store import read_video_counts_by_uploader

from .cache import CacheStore
from .utils import build_homepage_url, normalize_timestamp, timestamp_to_date


ARCHIVE_TABLE = "bilibili_archived_creators"
ACTIVE_STATUS = "active"
RESTORED_STATUS = "restored"


def _now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@contextmanager
def _connect(db_path):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _safe_int(value, default=0) -> int:
    try:
        text = str(value or "").replace(",", "").strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _safe_float(value, default=0.0) -> float:
    try:
        text = str(value or "").replace(",", "").strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _text(value) -> str:
    return str(value or "").strip()


def _json_dumps(data) -> str:
    return json.dumps(data or {}, ensure_ascii=False, default=str)


def _inactive_days_from_timestamp(timestamp) -> float | None:
    publish_ts = normalize_timestamp(timestamp)
    if publish_ts <= 0:
        return None
    try:
        seconds = (datetime.now() - datetime.fromtimestamp(publish_ts)).total_seconds()
    except Exception:
        return None
    return round(max(seconds / 86400, 0), 2)


def _summary_from_entry(entry) -> dict:
    summary = (entry or {}).get("summary", {})
    return summary if isinstance(summary, dict) else {}


def _videos_from_entry(entry) -> list[dict]:
    videos = (entry or {}).get("videos", [])
    return videos if isinstance(videos, list) else []


def _latest_video_from_entry(entry) -> dict:
    latest_video = {}
    latest_ts = 0
    for video in _videos_from_entry(entry):
        if not isinstance(video, dict):
            continue
        publish_ts = normalize_timestamp(
            video.get("publish_timestamp") or video.get("upload_timestamp")
        )
        if publish_ts > latest_ts:
            latest_ts = publish_ts
            latest_video = video
    return latest_video


def _latest_publish_timestamp(precise_entry, duration_entry) -> int:
    summary = _summary_from_entry(duration_entry)
    timestamp = normalize_timestamp(summary.get("latest_publish_timestamp"))
    if timestamp > 0:
        return timestamp

    latest_video = _latest_video_from_entry(duration_entry)
    timestamp = normalize_timestamp(
        latest_video.get("publish_timestamp") or latest_video.get("upload_timestamp")
    )
    if timestamp > 0:
        return timestamp

    return normalize_timestamp((precise_entry or {}).get("upload_timestamp"))


def _has_full_data(duration_entry) -> bool:
    summary = _summary_from_entry(duration_entry)
    return bool(summary)


def _cache_modes(precise_entry, duration_entry) -> str:
    modes = []
    if isinstance(precise_entry, dict) and precise_entry:
        modes.append("precise")
    if _has_full_data(duration_entry):
        modes.append("full")
    return ",".join(modes)


def _merge_creator_snapshot(uid, following, precise_entry, duration_entry, cached_video_count) -> dict:
    following = following if isinstance(following, dict) else {}
    precise_entry = precise_entry if isinstance(precise_entry, dict) else {}
    duration_entry = duration_entry if isinstance(duration_entry, dict) else {}
    summary = _summary_from_entry(duration_entry)
    latest_video = _latest_video_from_entry(duration_entry)
    latest_publish_timestamp = _latest_publish_timestamp(precise_entry, duration_entry)
    inactive_days = _inactive_days_from_timestamp(latest_publish_timestamp)
    if inactive_days is None:
        inactive_days = _safe_float(precise_entry.get("days_since_update"), 0.0)

    published_video_count = max(
        _safe_int(summary.get("total_videos"), 0),
        _safe_int(precise_entry.get("published_video_count"), 0),
    )
    homepage_url = _text(precise_entry.get("uploader_homepage")) or build_homepage_url(uid)
    latest_video_title = _text(latest_video.get("video_title")) or _text(precise_entry.get("latest_video_title"))

    return {
        "uploader_id": uid,
        "uploader_name": _text(following.get("uname")) or _text(precise_entry.get("uploader_name")) or uid,
        "homepage_url": homepage_url,
        "manual_grade": "",
        "final_grade": "",
        "final_score": 0.0,
        "confidence": "",
        "follower_count": max(
            _safe_int(following.get("follower_count"), 0),
            _safe_int(summary.get("follower_count"), 0),
            _safe_int(precise_entry.get("follower_count"), 0),
        ),
        "total_favorited": max(
            _safe_int(following.get("total_favorited"), 0),
            _safe_int(precise_entry.get("total_favorited"), 0),
        ),
        "total_view_count": max(
            _safe_int(following.get("total_view_count"), 0),
            _safe_int(precise_entry.get("total_view_count"), 0),
        ),
        "published_video_count": published_video_count,
        "cached_video_count": max(int(cached_video_count or 0), len(_videos_from_entry(duration_entry))),
        "latest_video_title": latest_video_title,
        "latest_publish_time": (
            timestamp_to_date(latest_publish_timestamp) if latest_publish_timestamp > 0 else _text(precise_entry.get("upload_date"))
        ),
        "inactive_days": inactive_days,
        "avg_update_days": _safe_float(summary.get("average_update_interval_days"), 0.0),
        "cached_modes": _cache_modes(precise_entry, duration_entry),
        "last_fetch_mode": "full" if _has_full_data(duration_entry) else ("precise" if precise_entry else "followings"),
        "has_full_cache": "是" if _has_full_data(duration_entry) else "",
        "source_snapshot": {
            "following": following,
            "precise_entry": precise_entry,
            "duration_entry": duration_entry,
        },
    }


def ensure_archive_table(db_path) -> None:
    with _connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{ARCHIVE_TABLE}" (
                uploader_id TEXT PRIMARY KEY,
                uploader_name TEXT,
                homepage_url TEXT,
                manual_grade TEXT,
                final_grade TEXT,
                final_score REAL,
                confidence TEXT,
                follower_count INTEGER,
                total_favorited INTEGER,
                total_view_count INTEGER,
                published_video_count INTEGER,
                cached_video_count INTEGER,
                latest_video_title TEXT,
                latest_publish_time TEXT,
                inactive_days REAL,
                avg_update_days REAL,
                cached_modes TEXT,
                last_fetch_mode TEXT,
                has_full_cache TEXT,
                archived_at TEXT,
                archive_reason TEXT,
                archive_status TEXT NOT NULL DEFAULT 'active',
                restored_at TEXT,
                restore_reason TEXT,
                source_snapshot_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{ARCHIVE_TABLE}_status" '
            f'ON "{ARCHIVE_TABLE}" (archive_status)'
        )
        conn.commit()


def load_active_archived_uids(db_path) -> set[str]:
    ensure_archive_table(db_path)
    with _connect(db_path) as conn:
        rows = conn.execute(
            f'SELECT uploader_id FROM "{ARCHIVE_TABLE}" WHERE archive_status=?',
            (ACTIVE_STATUS,),
        ).fetchall()
    return {_text(row["uploader_id"]) for row in rows if _text(row["uploader_id"])}


def load_archive_candidates(config, inactive_days_threshold=100) -> list[dict]:
    ensure_archive_table(config.export_store_db)
    threshold = _safe_float(inactive_days_threshold, 100.0)
    cache_store = CacheStore(config)
    followings = cache_store.load_followings_cache()
    precise_rows = cache_store.load_precise_progress()
    duration_progress = cache_store.load_video_duration_progress()
    active_archived = load_active_archived_uids(config.export_store_db)
    cached_video_counts = read_video_counts_by_uploader(config.export_store_db, "bilibili")

    followings_by_uid = {
        _text(item.get("mid")): item
        for item in followings
        if isinstance(item, dict) and _text(item.get("mid"))
    }
    all_uids = set(followings_by_uid) | {
        _text(uid) for uid in (precise_rows or {}).keys() if _text(uid)
    } | {
        _text(uid) for uid in (duration_progress or {}).keys() if _text(uid)
    }

    candidates = []
    for uid in all_uids:
        if uid in active_archived:
            continue
        precise_entry = (precise_rows or {}).get(uid)
        duration_entry = (duration_progress or {}).get(uid)
        if not _has_full_data(duration_entry):
            continue
        row = _merge_creator_snapshot(
            uid,
            followings_by_uid.get(uid, {}),
            precise_entry,
            duration_entry,
            cached_video_counts.get(uid, 0),
        )
        if float(row.get("inactive_days") or 0) < threshold:
            continue
        candidates.append(row)

    candidates.sort(
        key=lambda item: (
            float(item.get("inactive_days") or 0),
            int(item.get("follower_count") or 0),
            item.get("uploader_name") or "",
        ),
        reverse=True,
    )
    return candidates


def archive_creators(db_path, creator_rows, reason=None) -> int:
    ensure_archive_table(db_path)
    now = _now_text()
    reason = _text(reason) or "长期未更新，手动归档"
    rows = [row for row in (creator_rows or []) if _text((row or {}).get("uploader_id"))]
    if not rows:
        return 0
    with _connect(db_path) as conn:
        for row in rows:
            conn.execute(
                f"""
                INSERT INTO "{ARCHIVE_TABLE}" (
                    uploader_id, uploader_name, homepage_url, manual_grade, final_grade,
                    final_score, confidence, follower_count, total_favorited, total_view_count,
                    published_video_count, cached_video_count, latest_video_title,
                    latest_publish_time, inactive_days, avg_update_days, cached_modes,
                    last_fetch_mode, has_full_cache, archived_at, archive_reason,
                    archive_status, restored_at, restore_reason, source_snapshot_json,
                    updated_at
                ) VALUES (
                    :uploader_id, :uploader_name, :homepage_url, :manual_grade, :final_grade,
                    :final_score, :confidence, :follower_count, :total_favorited, :total_view_count,
                    :published_video_count, :cached_video_count, :latest_video_title,
                    :latest_publish_time, :inactive_days, :avg_update_days, :cached_modes,
                    :last_fetch_mode, :has_full_cache, :archived_at, :archive_reason,
                    :archive_status, '', '', :source_snapshot_json, :updated_at
                )
                ON CONFLICT(uploader_id) DO UPDATE SET
                    uploader_name=excluded.uploader_name,
                    homepage_url=excluded.homepage_url,
                    manual_grade=excluded.manual_grade,
                    final_grade=excluded.final_grade,
                    final_score=excluded.final_score,
                    confidence=excluded.confidence,
                    follower_count=excluded.follower_count,
                    total_favorited=excluded.total_favorited,
                    total_view_count=excluded.total_view_count,
                    published_video_count=excluded.published_video_count,
                    cached_video_count=excluded.cached_video_count,
                    latest_video_title=excluded.latest_video_title,
                    latest_publish_time=excluded.latest_publish_time,
                    inactive_days=excluded.inactive_days,
                    avg_update_days=excluded.avg_update_days,
                    cached_modes=excluded.cached_modes,
                    last_fetch_mode=excluded.last_fetch_mode,
                    has_full_cache=excluded.has_full_cache,
                    archived_at=excluded.archived_at,
                    archive_reason=excluded.archive_reason,
                    archive_status=excluded.archive_status,
                    restored_at='',
                    restore_reason='',
                    source_snapshot_json=excluded.source_snapshot_json,
                    updated_at=excluded.updated_at
                """,
                {
                    **row,
                    "archived_at": now,
                    "archive_reason": reason,
                    "archive_status": ACTIVE_STATUS,
                    "source_snapshot_json": _json_dumps(row.get("source_snapshot")),
                    "updated_at": now,
                },
            )
        conn.commit()
    return len(rows)


def load_archived_creators(db_path, active_only=False) -> list[dict]:
    ensure_archive_table(db_path)
    with _connect(db_path) as conn:
        if active_only:
            rows = conn.execute(
                f'SELECT * FROM "{ARCHIVE_TABLE}" WHERE archive_status=? ORDER BY inactive_days DESC',
                (ACTIVE_STATUS,),
            ).fetchall()
        else:
            rows = conn.execute(
                f'SELECT * FROM "{ARCHIVE_TABLE}" ORDER BY archive_status ASC, inactive_days DESC'
            ).fetchall()
    return [dict(row) for row in rows]


def restore_creators(db_path, uploader_ids, reason=None) -> int:
    ensure_archive_table(db_path)
    now = _now_text()
    ids = [_text(uid) for uid in (uploader_ids or []) if _text(uid)]
    if not ids:
        return 0
    with _connect(db_path) as conn:
        count = 0
        for uid in ids:
            cursor = conn.execute(
                f"""
                UPDATE "{ARCHIVE_TABLE}"
                SET archive_status=?, restored_at=?, restore_reason=?, updated_at=?
                WHERE uploader_id=? AND archive_status=?
                """,
                (RESTORED_STATUS, now, _text(reason) or "手动恢复", now, uid, ACTIVE_STATUS),
            )
            count += cursor.rowcount or 0
        conn.commit()
    return count
