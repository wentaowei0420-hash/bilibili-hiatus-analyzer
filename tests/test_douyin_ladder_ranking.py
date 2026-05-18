import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from backend import gui_data


def _seed_ladder_db(rating_db, source_db):
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
                "低等级视频比例" REAL
            )
            """
        )
        rows = []
        for index in range(70):
            uid = f"uid-{index:02d}"
            rows.append(
                (
                    f"Creator {index:02d}",
                    uid,
                    "S" if index == 0 else "A",
                    200 - index,
                    "高",
                    1000 + index,
                    100 + index,
                    0,
                    0,
                )
            )
        conn.executemany(
            """
            INSERT INTO creator_score_current (
                "UP主姓名", "UP主UID", "UP最终等级", "UP最终分",
                "评级置信度", "粉丝数", "视频数量", "未更新天数", "低等级视频比例"
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            [(f"uid-{index:02d}",) for index in range(70)],
        )
        conn.commit()


def test_ladder_keeps_66_non_s_creators_and_refills_after_actions(tmp_path, monkeypatch):
    rating_db = tmp_path / "rating.sqlite"
    source_db = tmp_path / "source.sqlite"
    _seed_ladder_db(rating_db, source_db)
    monkeypatch.setattr(gui_data, "_rating_db_paths", lambda: (rating_db, source_db))

    rows = gui_data.get_rating_overview()["tables"]["creator_ladder"]
    assert len(rows) == 66
    assert rows[0][1] == "A"
    assert rows[0][6] == "uid-01"
    assert rows[-1][6] == "uid-66"
    assert "uid-00" not in {row[6] for row in rows}

    gui_data.exclude_creator_from_ladder("uid-01")
    rows = gui_data.get_rating_overview()["tables"]["creator_ladder"]
    assert len(rows) == 66
    assert "uid-01" not in {row[6] for row in rows}
    assert rows[0][6] == "uid-02"
    assert rows[-1][6] == "uid-67"

    gui_data.save_creator_manual_grade("uid-02", "S", "test")
    rows = gui_data.get_rating_overview()["tables"]["creator_ladder"]
    assert len(rows) == 66
    assert "uid-02" not in {row[6] for row in rows}
    assert rows[0][6] == "uid-03"
    assert rows[-1][6] == "uid-68"
