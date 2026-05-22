import sqlite3
from pathlib import Path


DEFAULT_SQLITE_TIMEOUT_SECONDS = 30
DEFAULT_SQLITE_BUSY_TIMEOUT_MS = DEFAULT_SQLITE_TIMEOUT_SECONDS * 1000


def apply_sqlite_pragmas(conn: sqlite3.Connection) -> sqlite3.Connection:
    """Make local SQLite stores friendlier to concurrent GUI/background tasks."""
    conn.execute(f"PRAGMA busy_timeout = {DEFAULT_SQLITE_BUSY_TIMEOUT_MS}")
    try:
        conn.execute("PRAGMA journal_mode = WAL")
    except sqlite3.DatabaseError:
        # Some temporary/read-only databases may reject WAL; keep the connection usable.
        pass
    conn.execute("PRAGMA synchronous = NORMAL")
    return conn


def connect_sqlite(db_path, *, timeout: int = DEFAULT_SQLITE_TIMEOUT_SECONDS, **kwargs) -> sqlite3.Connection:
    db_path = Path(db_path)
    if db_path.parent:
        db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=timeout, **kwargs)
    return apply_sqlite_pragmas(conn)
