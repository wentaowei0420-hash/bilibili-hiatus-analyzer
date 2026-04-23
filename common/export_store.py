import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path

import pandas as pd


SNAPSHOT_META_TABLE = "_sheet_current_meta"
SNAPSHOT_HISTORY_TABLE = "_sheet_snapshots"


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


def write_dataframe_to_table(db_path, table_name, dataframe):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    dataframe = dataframe.copy()
    content_hash, payload_json = _calculate_hash(dataframe)
    now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    with sqlite3.connect(db_path) as conn:
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

        if previous_hash != content_hash:
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
        conn.commit()


def write_rows_to_table(db_path, table_name, fieldnames, headers, rows):
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    dataframe = _normalize_rows(fieldnames, headers, rows)
    write_dataframe_to_table(db_path, table_name, dataframe)


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

    with sqlite3.connect(db_path) as conn:
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

    with sqlite3.connect(db_path) as conn:
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
    placeholders = ",".join("?" for _ in values)

    with sqlite3.connect(db_path) as conn:
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
