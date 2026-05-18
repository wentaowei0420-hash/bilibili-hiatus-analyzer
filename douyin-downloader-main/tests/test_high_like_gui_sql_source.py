import sqlite3
from pathlib import Path

from config import ConfigLoader
from gui import HighLikeDownloaderGUI


def _make_gui_probe(**overrides):
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_candidate_source = overrides.get("source", "SQL库")
    probe.active_filter_mode = overrides.get("mode", "指定等级")
    probe.active_filter_grade = overrides.get("grade", "A")
    probe.active_like_threshold = overrides.get("threshold", 10000)
    probe.active_min_duration = overrides.get("min_duration", 0)
    probe.active_max_duration = overrides.get("max_duration", 0)
    probe.active_csv_path = ""
    return probe


def test_sql_source_can_download_grade_video_below_high_like_threshold(tmp_path):
    rating_db = tmp_path / "douyin_rating_store.db"
    export_db = tmp_path / "douyin_export_store.db"
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        f"database_path: {export_db}\n"
        f"rating_store_db: {rating_db}\n",
        encoding="utf-8",
    )

    with sqlite3.connect(rating_db) as conn:
        conn.execute(
            """
            CREATE TABLE video_score_current (
                "UP主姓名" TEXT,
                "视频标题" TEXT,
                "视频ID" TEXT PRIMARY KEY,
                "视频链接" TEXT,
                "视频时长(秒)" INTEGER,
                "点赞数" INTEGER,
                "视频最终等级" TEXT,
                "视频最终分" REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO video_score_current (
                "UP主姓名", "视频标题", "视频ID", "视频链接",
                "视频时长(秒)", "点赞数", "视频最终等级", "视频最终分"
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("作者A", "低赞A级", "100000000000000001", "", 30, 100, "A", 92),
                ("作者B", "高赞B级", "100000000000000002", "", 30, 20000, "B", 80),
            ],
        )
        conn.commit()

    config = ConfigLoader(str(config_path))
    probe = _make_gui_probe(mode="指定等级", grade="A", threshold=10000)
    rows = probe._load_sql_video_rows(config)
    filtered = probe._filter_rows_for_download(rows)

    assert [row["aweme_id"] for row in filtered] == ["100000000000000001"]
    assert filtered[0]["video_url"] == "https://www.douyin.com/video/100000000000000001"

    probe.active_filter_mode = "高赞视频"
    high_like_rows = probe._filter_rows_for_download(rows)
    assert [row["aweme_id"] for row in high_like_rows] == ["100000000000000002"]
