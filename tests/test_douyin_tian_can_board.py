import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import gui_data, tian_can_board


def _seed_tian_can_db(rating_db, source_db):
    with sqlite3.connect(rating_db) as conn:
        conn.execute(
            """
            CREATE TABLE creator_score_current (
                "UP主姓名" TEXT,
                "UP主UID" TEXT PRIMARY KEY,
                "UP最终等级" TEXT,
                "UP最终分" REAL,
                "评级置信度" TEXT,
                "粉丝数" INTEGER,
                "视频数量" INTEGER,
                "未更新天数" REAL,
                "低等级视频比例" REAL,
                "UP主主页链接" TEXT
            )
            """
        )
        rows = []
        for index in range(40):
            uid = f"uid-{index:02d}"
            rows.append(
                (
                    f"Creator {index:02d}",
                    uid,
                    "D",
                    30 + index,
                    "高",
                    1000 + index,
                    100 + index,
                    200 + index,
                    1.0,
                    f"https://www.douyin.com/user/{uid}",
                )
            )
        conn.executemany(
            """
            INSERT INTO creator_score_current (
                "UP主姓名", "UP主UID", "UP最终等级", "UP最终分", "评级置信度",
                "粉丝数", "视频数量", "未更新天数", "低等级视频比例", "UP主主页链接"
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        conn.commit()

    with sqlite3.connect(source_db) as conn:
        conn.execute(
            """
            CREATE TABLE cache_inventory_current (
                "UP主UID" TEXT PRIMARY KEY,
                "有full缓存" TEXT,
                "已缓存模式" TEXT,
                "最近抓取模式" TEXT,
                "统计范围" TEXT
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO cache_inventory_current (
                "UP主UID", "有full缓存", "已缓存模式", "最近抓取模式", "统计范围"
            )
            VALUES (?, '是', 'full', 'full', 'full')
            """,
            [(f"uid-{index:02d}",) for index in range(40)],
        )
        conn.commit()


def test_tian_can_board_keeps_33_creators_and_refills_after_dismiss(tmp_path, monkeypatch):
    rating_db = tmp_path / "rating.sqlite"
    source_db = tmp_path / "source.sqlite"
    _seed_tian_can_db(rating_db, source_db)
    monkeypatch.setattr(gui_data, "_rating_db_paths", lambda: (rating_db, source_db))

    rows = gui_data.get_rating_overview()["tables"]["creator_low"]
    assert len(rows) == 33
    assert all(row[1] == "F" for row in rows)
    assert rows[0][7] == "uid-00"
    assert rows[-1][7] == "uid-32"

    gui_data.dismiss_tian_can_creator("uid-00")

    rows = gui_data.get_rating_overview()["tables"]["creator_low"]
    assert len(rows) == 33
    assert "uid-00" not in {row[7] for row in rows}
    assert rows[0][7] == "uid-01"
    assert rows[-1][7] == "uid-33"

    with sqlite3.connect(rating_db) as conn:
        row = conn.execute(
            """
            SELECT manual_grade
            FROM douyin_creator_manual_rating
            WHERE uploader_id = ?
            """,
            ("uid-00",),
        ).fetchone()
        hidden = conn.execute(
            f"""
            SELECT action_type
            FROM "{tian_can_board.TIAN_CAN_HIDDEN_TABLE}"
            WHERE uploader_id = ?
            """,
            ("uid-00",),
        ).fetchone()
    assert row[0] == "C"
    assert hidden[0] == "dismiss"


def test_tian_can_board_unfollow_hides_creator_and_refills(tmp_path, monkeypatch):
    rating_db = tmp_path / "rating.sqlite"
    source_db = tmp_path / "source.sqlite"
    _seed_tian_can_db(rating_db, source_db)
    monkeypatch.setattr(gui_data, "_rating_db_paths", lambda: (rating_db, source_db))

    result = gui_data.unfollow_tian_can_creator(
        "uid-01",
        "https://www.douyin.com/user/uid-01",
    )
    rows = gui_data.get_rating_overview()["tables"]["creator_low"]

    assert result["status"] == "hidden"
    assert len(rows) == 33
    assert "uid-01" not in {row[7] for row in rows}
    assert rows[0][7] == "uid-00"
    assert rows[-1][7] == "uid-33"

    with sqlite3.connect(rating_db) as conn:
        hidden = conn.execute(
            f"""
            SELECT action_type
            FROM "{tian_can_board.TIAN_CAN_HIDDEN_TABLE}"
            WHERE uploader_id = ?
            """,
            ("uid-01",),
        ).fetchone()
    assert hidden[0] == "unfollow"
