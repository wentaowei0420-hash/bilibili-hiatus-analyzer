import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


def _now_text():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _creator_table(platform):
    return f"{platform}_creator_raw"


def _video_table(platform):
    return f"{platform}_video_raw"


def _video_state_table(platform):
    return f"{platform}_video_state"


def _cache_table(platform):
    return f"{platform}_cache_state"


def _summary_table(platform):
    return f"{platform}_summary_current"


def ensure_platform_schema(db_path, platform):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_creator_table(platform)}" (
                uploader_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_mode TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_video_table(platform)}" (
                uploader_id TEXT NOT NULL,
                video_id TEXT NOT NULL,
                publish_timestamp INTEGER,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (uploader_id, video_id)
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_video_state_table(platform)}" (
                video_id TEXT PRIMARY KEY,
                uploader_id TEXT,
                uploader_name TEXT,
                publish_timestamp INTEGER,
                like_count INTEGER,
                duration_seconds INTEGER,
                source_mode TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                is_available INTEGER NOT NULL DEFAULT 1,
                payload_json TEXT NOT NULL,
                metadata_json TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{platform}_video_state_uploader" '
            f'ON "{_video_state_table(platform)}" (uploader_id)'
        )
        conn.execute(
            f'CREATE INDEX IF NOT EXISTS "idx_{platform}_video_state_publish" '
            f'ON "{_video_state_table(platform)}" (publish_timestamp)'
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_cache_table(platform)}" (
                cache_key TEXT PRIMARY KEY,
                cache_type TEXT NOT NULL,
                uploader_id TEXT,
                source_mode TEXT,
                cached_at TEXT,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS "{_summary_table(platform)}" (
                summary_type TEXT NOT NULL,
                uploader_id TEXT NOT NULL,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY (summary_type, uploader_id)
            )
            """
        )
        conn.commit()


def _safe_int(value, default=None):
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def upsert_creator_rows(db_path, platform, rows, uploader_id_column="UP主UID", source_mode="current"):
    ensure_platform_schema(db_path, platform)
    now_text = _now_text()
    with sqlite3.connect(db_path) as conn:
        for row in rows or []:
            row = row if isinstance(row, dict) else {}
            uploader_id = str(row.get(uploader_id_column) or "").strip()
            if not uploader_id:
                continue
            conn.execute(
                f"""
                INSERT INTO "{_creator_table(platform)}" (uploader_id, payload_json, updated_at, source_mode)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(uploader_id) DO UPDATE SET
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at,
                    source_mode=excluded.source_mode
                """,
                (uploader_id, json.dumps(row, ensure_ascii=False), now_text, source_mode),
            )
        conn.commit()


def upsert_video_state_rows(
    db_path,
    platform,
    rows,
    video_id_column="aweme_id",
    uploader_id_column="uploader_id",
    uploader_name_column="uploader_name",
    source_mode="current",
):
    ensure_platform_schema(db_path, platform)
    now_text = _now_text()
    with sqlite3.connect(db_path) as conn:
        for row in rows or []:
            row = row if isinstance(row, dict) else {}
            video_id = str(row.get(video_id_column) or row.get("video_id") or row.get("aweme_id") or "").strip()
            if not video_id:
                continue
            uploader_id = str(row.get(uploader_id_column) or row.get("author_id") or "").strip()
            uploader_name = str(row.get(uploader_name_column) or row.get("author_name") or "").strip()
            publish_timestamp = _safe_int(row.get("publish_timestamp") or row.get("create_time"))
            like_count = _safe_int(row.get("like_count"), 0)
            duration_seconds = _safe_int(row.get("duration_seconds"))
            metadata = row.get("metadata")
            metadata_json = json.dumps(metadata, ensure_ascii=False) if isinstance(metadata, (dict, list)) else None
            conn.execute(
                f"""
                INSERT INTO "{_video_state_table(platform)}" (
                    video_id,
                    uploader_id,
                    uploader_name,
                    publish_timestamp,
                    like_count,
                    duration_seconds,
                    source_mode,
                    first_seen_at,
                    last_seen_at,
                    is_available,
                    payload_json,
                    metadata_json,
                    updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
                ON CONFLICT(video_id) DO UPDATE SET
                    uploader_id=COALESCE(NULLIF(excluded.uploader_id, ''), "{_video_state_table(platform)}".uploader_id),
                    uploader_name=COALESCE(NULLIF(excluded.uploader_name, ''), "{_video_state_table(platform)}".uploader_name),
                    publish_timestamp=COALESCE(excluded.publish_timestamp, "{_video_state_table(platform)}".publish_timestamp),
                    like_count=COALESCE(excluded.like_count, "{_video_state_table(platform)}".like_count),
                    duration_seconds=COALESCE(excluded.duration_seconds, "{_video_state_table(platform)}".duration_seconds),
                    source_mode=CASE
                        WHEN LOWER(COALESCE("{_video_state_table(platform)}".source_mode, '')) = 'full'
                             AND LOWER(COALESCE(excluded.source_mode, '')) != 'full'
                        THEN "{_video_state_table(platform)}".source_mode
                        ELSE COALESCE(NULLIF(excluded.source_mode, ''), "{_video_state_table(platform)}".source_mode)
                    END,
                    last_seen_at=excluded.last_seen_at,
                    is_available=1,
                    payload_json=excluded.payload_json,
                    metadata_json=COALESCE(excluded.metadata_json, "{_video_state_table(platform)}".metadata_json),
                    updated_at=excluded.updated_at
                """,
                (
                    video_id,
                    uploader_id,
                    uploader_name,
                    publish_timestamp,
                    like_count,
                    duration_seconds,
                    source_mode,
                    now_text,
                    now_text,
                    json.dumps(row, ensure_ascii=False),
                    metadata_json,
                    now_text,
                ),
            )
        conn.commit()


def read_latest_video_state_timestamp(db_path, platform, uploader_id):
    db_path = Path(db_path)
    uploader_id = str(uploader_id or "").strip()
    if not uploader_id or not db_path.exists():
        return None

    ensure_platform_schema(db_path, platform)
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            f"""
            SELECT MAX(publish_timestamp)
            FROM "{_video_state_table(platform)}"
            WHERE uploader_id=? AND is_available=1
            """,
            (uploader_id,),
        ).fetchone()
    return row[0] if row and row[0] is not None else None


def replace_video_rows_for_uploader(
    db_path,
    platform,
    uploader_id,
    rows,
    video_id_column,
    publish_timestamp_column="publish_timestamp",
):
    ensure_platform_schema(db_path, platform)
    uploader_id = str(uploader_id or "").strip()
    if not uploader_id:
        return
    now_text = _now_text()
    with sqlite3.connect(db_path) as conn:
        conn.execute(f'DELETE FROM "{_video_table(platform)}" WHERE uploader_id=?', (uploader_id,))
        for row in rows or []:
            row = row if isinstance(row, dict) else {}
            video_id = str(row.get(video_id_column) or "").strip()
            if not video_id:
                continue
            publish_timestamp = row.get(publish_timestamp_column)
            try:
                publish_timestamp = int(publish_timestamp) if publish_timestamp not in (None, "") else None
            except (TypeError, ValueError):
                publish_timestamp = None
            conn.execute(
                f"""
                INSERT OR REPLACE INTO "{_video_table(platform)}"
                (uploader_id, video_id, publish_timestamp, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    uploader_id,
                    video_id,
                    publish_timestamp,
                    json.dumps(row, ensure_ascii=False),
                    now_text,
                ),
            )
        conn.commit()
    upsert_video_state_rows(
        db_path,
        platform,
        rows,
        video_id_column=video_id_column,
        uploader_id_column="uploader_id",
        source_mode="video_raw",
    )


def read_video_rows_for_uploader(db_path, platform, uploader_id):
    db_path = Path(db_path)
    uploader_id = str(uploader_id or "").strip()
    if not uploader_id or not db_path.exists():
        return []

    ensure_platform_schema(db_path, platform)
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_video_table(platform),),
        )
        if cursor.fetchone() is None:
            return []
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM "{_video_table(platform)}"
            WHERE uploader_id=?
            ORDER BY publish_timestamp DESC
            """,
            (uploader_id,),
        ).fetchall()

    video_rows = []
    for (payload_json,) in rows:
        try:
            payload = json.loads(payload_json)
        except Exception:
            continue
        if isinstance(payload, dict):
            video_rows.append(payload)
    return video_rows


def upsert_cache_entries(
    db_path,
    platform,
    entries,
    cache_type,
    source_mode="",
    uploader_id_getter=None,
    cached_at_getter=None,
):
    ensure_platform_schema(db_path, platform)
    now_text = _now_text()
    uploader_id_getter = uploader_id_getter or (lambda key, payload: key)
    cached_at_getter = cached_at_getter or (lambda payload: payload.get("cached_at") if isinstance(payload, dict) else "")

    with sqlite3.connect(db_path) as conn:
        for key, payload in (entries or {}).items():
            uploader_id = uploader_id_getter(key, payload)
            uploader_id = str(uploader_id or "").strip()
            cached_at = cached_at_getter(payload)
            conn.execute(
                f"""
                INSERT INTO "{_cache_table(platform)}"
                (cache_key, cache_type, uploader_id, source_mode, cached_at, payload_json, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(cache_key) DO UPDATE SET
                    cache_type=excluded.cache_type,
                    uploader_id=excluded.uploader_id,
                    source_mode=CASE
                        WHEN LOWER(COALESCE("{_cache_table(platform)}".source_mode, '')) = 'full'
                             AND LOWER(COALESCE(excluded.source_mode, '')) != 'full'
                        THEN "{_cache_table(platform)}".source_mode
                        ELSE excluded.source_mode
                    END,
                    cached_at=excluded.cached_at,
                    payload_json=excluded.payload_json,
                    updated_at=excluded.updated_at
                """,
                (
                    str(key),
                    cache_type,
                    uploader_id,
                    source_mode,
                    str(cached_at or ""),
                    json.dumps(payload, ensure_ascii=False),
                    now_text,
                ),
            )
        conn.commit()


def replace_summary_rows(db_path, platform, summary_type, rows, uploader_id_column="UP主UID"):
    ensure_platform_schema(db_path, platform)
    now_text = _now_text()
    with sqlite3.connect(db_path) as conn:
        conn.execute(f'DELETE FROM "{_summary_table(platform)}" WHERE summary_type=?', (summary_type,))
        for row in rows or []:
            row = row if isinstance(row, dict) else {}
            uploader_id = str(row.get(uploader_id_column) or "").strip()
            if not uploader_id:
                continue
            conn.execute(
                f"""
                INSERT INTO "{_summary_table(platform)}"
                (summary_type, uploader_id, payload_json, updated_at)
                VALUES (?, ?, ?, ?)
                """,
                (summary_type, uploader_id, json.dumps(row, ensure_ascii=False), now_text),
            )
        conn.commit()


def read_summary_rows(db_path, platform, summary_type):
    db_path = Path(db_path)
    if not db_path.exists():
        return []
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_summary_table(platform),),
        )
        if cursor.fetchone() is None:
            return []
        df = pd.read_sql_query(
            f'SELECT payload_json FROM "{_summary_table(platform)}" WHERE summary_type=? ORDER BY uploader_id',
            conn,
            params=(summary_type,),
        )
    return [json.loads(item) for item in df["payload_json"].tolist()]


def load_uploader_ids_from_tables(
    db_path,
    table_targets,
    *,
    candidate_columns=None,
):
    db_path = Path(db_path)
    if not db_path.exists():
        return set()

    candidate_columns = list(
        candidate_columns
        or ["UP主UID", "UP涓籙ID", "UP娑撶睓ID", "uploader_id", "target_uid"]
    )
    if "UP主UID" not in candidate_columns:
        candidate_columns.insert(0, "UP主UID")
    uploader_ids = set()

    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        for table_name, preferred_column in table_targets or []:
            cursor.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                (table_name,),
            )
            if cursor.fetchone() is None:
                continue

            target_column = preferred_column
            if not target_column:
                table_info = cursor.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                columns = {str(row[1]) for row in table_info}
                target_column = next(
                    (column for column in candidate_columns if column in columns),
                    None,
                )
            if not target_column:
                continue

            try:
                rows = cursor.execute(
                    f'SELECT DISTINCT "{target_column}" FROM "{table_name}" '
                    f'WHERE "{target_column}" IS NOT NULL AND TRIM("{target_column}") <> \'\''
                ).fetchall()
            except sqlite3.Error:
                continue

            for (value,) in rows:
                normalized = str(value or "").strip()
                if normalized:
                    uploader_ids.add(normalized)

    return uploader_ids


def delete_uploader_rows(db_path, platform, uploader_ids):
    db_path = Path(db_path)
    uploader_ids = sorted({str(item).strip() for item in (uploader_ids or []) if str(item).strip()})
    if not uploader_ids or not db_path.exists():
        return 0

    ensure_platform_schema(db_path, platform)
    placeholders = ",".join("?" for _ in uploader_ids)
    deleted_rows = 0
    with sqlite3.connect(db_path) as conn:
        cursor = conn.cursor()
        statements = [
            (f'DELETE FROM "{_creator_table(platform)}" WHERE uploader_id IN ({placeholders})', uploader_ids),
            (f'DELETE FROM "{_video_table(platform)}" WHERE uploader_id IN ({placeholders})', uploader_ids),
            (f'DELETE FROM "{_video_state_table(platform)}" WHERE uploader_id IN ({placeholders})', uploader_ids),
            (
                f'DELETE FROM "{_cache_table(platform)}" WHERE uploader_id IN ({placeholders}) OR cache_key IN ({placeholders})',
                uploader_ids + uploader_ids,
            ),
            (f'DELETE FROM "{_summary_table(platform)}" WHERE uploader_id IN ({placeholders})', uploader_ids),
        ]

        for statement, params in statements:
            cursor.execute(statement, params)
            deleted_rows += cursor.rowcount if cursor.rowcount > 0 else 0
        conn.commit()
    return deleted_rows
