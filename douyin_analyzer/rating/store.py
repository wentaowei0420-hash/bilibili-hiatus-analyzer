from pathlib import Path
import sqlite3


RATING_TABLES = (
    "video_score_current",
    "creator_score_current",
    "douyin_creator_manual_rating",
    "douyin_video_manual_rating",
)


def rating_store_db_path(config):
    return Path(getattr(config, "rating_store_db", None) or getattr(config, "export_store_db", ""))


def source_store_db_path(config):
    return Path(getattr(config, "export_store_db", ""))


def _table_exists(conn, table_name):
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone() is not None


def _table_count(conn, table_name):
    if not _table_exists(conn, table_name):
        return 0
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0] or 0)


def migrate_legacy_rating_tables(config, *, drop_legacy=False):
    source_db = source_store_db_path(config)
    rating_db = rating_store_db_path(config)
    result = {}
    if not source_db.exists() or source_db == rating_db:
        return result

    rating_db.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source_db) as source_conn, sqlite3.connect(rating_db) as rating_conn:
        for table_name in RATING_TABLES:
            if not _table_exists(source_conn, table_name):
                result[table_name] = {"source_rows": 0, "target_rows": _table_count(rating_conn, table_name), "migrated": False}
                continue

            source_rows = _table_count(source_conn, table_name)
            target_rows = _table_count(rating_conn, table_name)
            migrated = False
            if source_rows > 0 and target_rows <= 0:
                schema_row = source_conn.execute(
                    "SELECT sql FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                ).fetchone()
                if schema_row and schema_row[0]:
                    rating_conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                    rating_conn.execute(schema_row[0])
                    columns = [
                        row[1]
                        for row in source_conn.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                    ]
                    column_sql = ", ".join(f'"{column}"' for column in columns)
                    placeholders = ", ".join("?" for _ in columns)
                    rows = source_conn.execute(f'SELECT {column_sql} FROM "{table_name}"').fetchall()
                    rating_conn.executemany(
                        f'INSERT INTO "{table_name}" ({column_sql}) VALUES ({placeholders})',
                        rows,
                    )
                    rating_conn.commit()
                    target_rows = _table_count(rating_conn, table_name)
                    migrated = target_rows == source_rows

            if drop_legacy and (source_rows <= 0 or _table_count(rating_conn, table_name) >= source_rows):
                source_conn.execute(f'DROP TABLE IF EXISTS "{table_name}"')
                source_conn.commit()

            result[table_name] = {
                "source_rows": source_rows,
                "target_rows": _table_count(rating_conn, table_name),
                "migrated": migrated,
                "dropped_legacy": drop_legacy and not _table_exists(source_conn, table_name),
            }
    return result
