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
                    source_mode=excluded.source_mode,
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
