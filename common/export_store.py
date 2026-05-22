import hashlib
import json
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd

from common.sqlite_utils import connect_sqlite


SNAPSHOT_META_TABLE = "_sheet_current_meta"
SNAPSHOT_HISTORY_TABLE = "_sheet_snapshots"
DEFAULT_SNAPSHOT_RETENTION = 3
DEFAULT_SNAPSHOT_DISABLED_TABLES = {
    "video_score_current",
    "creator_score_current",
    "cache_inventory_current",
}


def _normalize_rows(fieldnames, headers, rows):
    normalized_rows = []
    ordered_columns = [headers[field] for field in fieldnames]
    for row in rows or []:
        normalized = {}
        row = row if isinstance(row, dict) else {}
        for field in fieldnames:
            normalized[headers[field]] = row.get(field, "")
        normalized_rows.append(normalized)
    return pd.DataFrame(normalized_rows, columns=ordered_columns)


def _calculate_hash(dataframe):
    payload = dataframe.to_json(orient="records", force_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest(), payload


def _calculate_content_hash(dataframe):
    payload = dataframe.to_json(orient="records", force_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _ensure_snapshot_tables(conn):
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{SNAPSHOT_META_TABLE}" (
            sheet_name TEXT PRIMARY KEY,
            updated_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            content_hash TEXT NOT NULL
        )
        """
    )
    conn.execute(
        f"""
        CREATE TABLE IF NOT EXISTS "{SNAPSHOT_HISTORY_TABLE}" (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sheet_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            row_count INTEGER NOT NULL,
            column_count INTEGER NOT NULL,
            content_hash TEXT NOT NULL,
            payload_json TEXT NOT NULL
        )
        """
    )


def _snapshot_retention_limit():
    try:
        return max(0, int(os.getenv("EXPORT_STORE_SNAPSHOT_RETENTION", str(DEFAULT_SNAPSHOT_RETENTION))))
    except (TypeError, ValueError):
        return DEFAULT_SNAPSHOT_RETENTION


def _snapshot_disabled_tables():
    raw_value = os.getenv("EXPORT_STORE_DISABLE_SNAPSHOTS_FOR", "")
    configured = {
        item.strip()
        for item in raw_value.split(",")
        if item.strip()
    }
    return DEFAULT_SNAPSHOT_DISABLED_TABLES | configured


def _should_store_snapshot(table_name):
    if os.getenv("EXPORT_STORE_DISABLE_SNAPSHOTS", "").strip().lower() in {"1", "true", "yes", "on"}:
        return False
    return str(table_name or "").strip() not in _snapshot_disabled_tables()


def _prune_snapshot_history(conn, table_name):
    retention = _snapshot_retention_limit()
    if retention <= 0:
        conn.execute(
            f'DELETE FROM "{SNAPSHOT_HISTORY_TABLE}" WHERE sheet_name=?',
            (table_name,),
        )
        return

    keep_rows = conn.execute(
        f"""
        SELECT id
        FROM "{SNAPSHOT_HISTORY_TABLE}"
        WHERE sheet_name=?
        ORDER BY id DESC
        LIMIT ?
        """,
        (table_name, retention),
    ).fetchall()
    keep_ids = [row[0] for row in keep_rows]
    if not keep_ids:
        return
    placeholders = ",".join("?" for _ in keep_ids)
    conn.execute(
        f"""
        DELETE FROM "{SNAPSHOT_HISTORY_TABLE}"
        WHERE sheet_name=? AND id NOT IN ({placeholders})
        """,
        [table_name, *keep_ids],
    )


def write_dataframe_to_table(db_path, table_name, dataframe):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe = dataframe.copy()
    store_snapshot = _should_store_snapshot(table_name)
    if store_snapshot:
        content_hash, payload_json = _calculate_hash(dataframe)
    else:
        content_hash = _calculate_content_hash(dataframe)
        payload_json = None
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with connect_sqlite(db_path) as conn:
        dataframe.to_sql(table_name, conn, if_exists="replace", index=False)
        _ensure_snapshot_tables(conn)

        existing = conn.execute(
            f'SELECT content_hash FROM "{SNAPSHOT_META_TABLE}" WHERE sheet_name=?',
            (table_name,),
        ).fetchone()
        previous_hash = existing[0] if existing else None

        conn.execute(
            f"""
            INSERT INTO "{SNAPSHOT_META_TABLE}" (
                sheet_name, updated_at, row_count, column_count, content_hash
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(sheet_name) DO UPDATE SET
                updated_at=excluded.updated_at,
                row_count=excluded.row_count,
                column_count=excluded.column_count,
                content_hash=excluded.content_hash
            """,
            (
                table_name,
                now_text,
                len(dataframe.index),
                len(dataframe.columns),
                content_hash,
            ),
        )

        if not store_snapshot:
            conn.execute(
                f'DELETE FROM "{SNAPSHOT_HISTORY_TABLE}" WHERE sheet_name=?',
                (table_name,),
            )
        elif previous_hash != content_hash:
            conn.execute(
                f"""
                INSERT INTO "{SNAPSHOT_HISTORY_TABLE}" (
                    sheet_name, created_at, row_count, column_count, content_hash, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    table_name,
                    now_text,
                    len(dataframe.index),
                    len(dataframe.columns),
                    content_hash,
                    payload_json,
                ),
            )
        _prune_snapshot_history(conn, table_name)
        conn.commit()


def write_rows_to_table(db_path, table_name, fieldnames, headers, rows):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    dataframe = _normalize_rows(fieldnames, headers, rows)
    write_dataframe_to_table(db_path, table_name, dataframe)


def prune_disabled_snapshot_history(db_path):
    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    with connect_sqlite(db_path) as conn:
        _ensure_snapshot_tables(conn)
        tables = sorted(_snapshot_disabled_tables())
        deleted = 0
        for table_name in tables:
            cursor = conn.execute(
                f'DELETE FROM "{SNAPSHOT_HISTORY_TABLE}" WHERE sheet_name=?',
                (table_name,),
            )
            deleted += cursor.rowcount if cursor.rowcount is not None else 0
        conn.commit()
        return deleted


def upsert_rows_to_table(db_path, table_name, fieldnames, headers, rows, key_field="uploader_id"):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    incoming = _normalize_rows(fieldnames, headers, rows)
    if incoming.empty:
        return

    key_column = headers.get(key_field, key_field)
    if key_column not in incoming.columns:
        write_dataframe_to_table(db_path, table_name, incoming)
        return

    incoming[key_column] = incoming[key_column].astype(str).str.strip()
    incoming = incoming[incoming[key_column] != ""]
    if incoming.empty:
        return

    existing = read_table_to_dataframe(db_path, table_name)
    ordered_columns = [headers[field] for field in fieldnames]
    if existing is None or existing.empty:
        merged = incoming.reindex(columns=ordered_columns)
    else:
        existing = existing.copy()
        if key_column not in existing.columns:
            merged = incoming.reindex(columns=ordered_columns)
        else:
            existing[key_column] = existing[key_column].astype(str).str.strip()
            all_columns = ordered_columns + [
                column for column in existing.columns if column not in ordered_columns
            ]
            merged = pd.concat(
                [
                    existing.reindex(columns=all_columns),
                    incoming.reindex(columns=all_columns),
                ],
                ignore_index=True,
            )
            merged = merged[merged[key_column].astype(str).str.strip() != ""]
            merged = merged.drop_duplicates(subset=[key_column], keep="last")
            merged = merged.reindex(columns=all_columns)

    write_dataframe_to_table(db_path, table_name, merged)


def read_table_to_dataframe(db_path, table_name):
    db_path = Path(db_path)
    if not db_path.exists():
        return None

    with connect_sqlite(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if cursor.fetchone() is None:
            return None
        return pd.read_sql_query(f'SELECT * FROM "{table_name}"', conn)


def read_latest_snapshot_to_dataframe(db_path, table_name):
    db_path = Path(db_path)
    if not db_path.exists():
        return None

    with connect_sqlite(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (SNAPSHOT_HISTORY_TABLE,),
        )
        if cursor.fetchone() is None:
            return None
        cursor.execute(
            f"""
            SELECT payload_json
            FROM "{SNAPSHOT_HISTORY_TABLE}"
            WHERE sheet_name=?
            ORDER BY id DESC
            LIMIT 1
            """,
            (table_name,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        payload = json.loads(row[0])
        return pd.DataFrame(payload)


def delete_rows_by_values(db_path, table_name, values, candidate_columns=None):
    db_path = Path(db_path)
    values = sorted({str(item).strip() for item in (values or []) if str(item).strip()})
    if not values or not db_path.exists():
        return 0

    candidate_columns = list(candidate_columns or ["UP主UID", "UP涓籙ID", "uploader_id", "target_uid"])
    if "UP主UID" not in candidate_columns:
        candidate_columns.insert(0, "UP主UID")
    placeholders = ",".join("?" for _ in values)

    with connect_sqlite(db_path) as conn:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        )
        if cursor.fetchone() is None:
            return 0

        table_info = cursor.execute(f'PRAGMA table_info("{table_name}")').fetchall()
        columns = {str(row[1]) for row in table_info}
        target_column = next((column for column in candidate_columns if column in columns), None)
        if not target_column:
            return 0

        cursor.execute(
            f'DELETE FROM "{table_name}" WHERE "{target_column}" IN ({placeholders})',
            values,
        )
        deleted_rows = cursor.rowcount if cursor.rowcount > 0 else 0
        conn.commit()
        return deleted_rows
