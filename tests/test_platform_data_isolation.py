from bilibili_analyzer.config import load_analyzer_config as load_bilibili_config
from douyin_analyzer.config import load_analyzer_config as load_douyin_config
from douyin_analyzer.rating.store import (
    migrate_legacy_rating_tables,
    rating_store_db_path,
    source_store_db_path,
)
from types import SimpleNamespace
import sqlite3


def test_default_platform_databases_are_separate(monkeypatch):
    monkeypatch.delenv("DOUYIN_RATING_STORE_DB", raising=False)

    bilibili_config = load_bilibili_config()
    douyin_config = load_douyin_config()

    bilibili_db = bilibili_config.export_store_db.resolve()
    douyin_source_db = source_store_db_path(douyin_config).resolve()
    douyin_rating_db = rating_store_db_path(douyin_config).resolve()

    assert bilibili_db != douyin_source_db
    assert bilibili_db != douyin_rating_db
    assert douyin_source_db != douyin_rating_db
    assert "bilibili" in bilibili_db.parts
    assert "douyin" in douyin_source_db.parts
    assert "douyin" in douyin_rating_db.parts


def test_migrate_legacy_rating_tables_moves_only_rating_tables(tmp_path):
    source_db = tmp_path / "douyin_export_store.db"
    rating_db = tmp_path / "douyin_rating_store.db"
    with sqlite3.connect(source_db) as conn:
        conn.execute('CREATE TABLE main_sheet_current ("UP主UID" TEXT)')
        conn.execute('CREATE TABLE video_score_current ("视频ID" TEXT, "视频最终等级" TEXT)')
        conn.execute('INSERT INTO main_sheet_current VALUES ("creator-1")')
        conn.execute('INSERT INTO video_score_current VALUES ("video-1", "S")')
        conn.commit()

    result = migrate_legacy_rating_tables(
        SimpleNamespace(export_store_db=source_db, rating_store_db=rating_db),
        drop_legacy=True,
    )

    with sqlite3.connect(source_db) as conn:
        source_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
    with sqlite3.connect(rating_db) as conn:
        rating_tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        rows = conn.execute('SELECT "视频ID", "视频最终等级" FROM video_score_current').fetchall()

    assert result["video_score_current"]["migrated"] is True
    assert "main_sheet_current" in source_tables
    assert "video_score_current" not in source_tables
    assert rating_tables == {"video_score_current"}
    assert rows == [("video-1", "S")]
