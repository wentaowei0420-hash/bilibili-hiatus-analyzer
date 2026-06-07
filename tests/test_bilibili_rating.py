from __future__ import annotations

import sqlite3
import sys
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import bilibili_rating
from bilibili_analyzer.rating.creator_scoring import BilibiliCreatorScorer


def _write_table(db_path, table_name, rows):
    dataframe = pd.DataFrame(rows)
    with sqlite3.connect(str(db_path)) as conn:
        dataframe.to_sql(table_name, conn, if_exists="replace", index=False)


def test_bilibili_rating_overview_and_manual_grade(tmp_path, monkeypatch):
    export_db = tmp_path / "bilibili_export_store.db"
    rating_db = tmp_path / "bilibili_rating_store.db"
    config = SimpleNamespace(export_store_db=export_db)
    monkeypatch.setattr(bilibili_rating, "_load_config", lambda: config)

    _write_table(
        rating_db,
        "creator_score_current",
        [
            {
                "uploader_name": "Alpha",
                "uploader_id": "1001",
                "homepage_url": "https://space.bilibili.com/1001",
                "manual_grade": "",
                "auto_score": 93.0,
                "auto_grade": "S",
                "final_score": 93.0,
                "final_grade": "S",
                "confidence": "高",
                "follower_count": 10000,
                "total_view_count": 200000,
                "published_video_count": 12,
                "inactive_days": 4,
                "low_grade_ratio": 0.08,
            },
            {
                "uploader_name": "Beta",
                "uploader_id": "1002",
                "homepage_url": "https://space.bilibili.com/1002",
                "manual_grade": "",
                "auto_score": 54.0,
                "auto_grade": "D",
                "final_score": 54.0,
                "final_grade": "D",
                "confidence": "低",
                "follower_count": 1000,
                "total_view_count": 5000,
                "published_video_count": 3,
                "inactive_days": 160,
                "low_grade_ratio": 0.66,
            },
        ],
    )
    _write_table(
        rating_db,
        "video_score_current",
        [
            {
                "uploader_id": "1001",
                "video_title": "A1",
                "final_grade": "S",
                "final_score": 95.0,
                "confidence": "高",
                "publish_timestamp": 1710000000,
                "publish_date": "2024-03-09 00:00:00",
                "duration_category": "61~240s",
            },
            {
                "uploader_id": "1002",
                "video_title": "B1",
                "final_grade": "D",
                "final_score": 40.0,
                "confidence": "低",
                "publish_timestamp": 1700000000,
                "publish_date": "2023-11-14 00:00:00",
                "duration_category": "0~30s",
            },
        ],
    )

    overview = bilibili_rating.get_rating_overview()
    assert overview["ok"] is True
    assert overview["summary"]["creator"]["counts"]["S"] == 1
    assert overview["summary"]["creator"]["counts"]["D"] == 1
    assert overview["summary"]["video"]["counts"]["S"] == 1
    assert overview["summary"]["video"]["counts"]["D"] == 1
    assert overview["tables"]["current"][0]["uploader_name"] == "Alpha"
    assert overview["tables"]["watch"][0]["uploader_name"] == "Beta"

    result = bilibili_rating.save_creator_manual_grade("1002", "A", "promoted")
    assert result["ok"] is True
    with sqlite3.connect(str(rating_db)) as conn:
        row = conn.execute(
            "SELECT manual_grade, note FROM bilibili_creator_manual_rating WHERE uploader_id=?",
            ("1002",),
        ).fetchone()
    assert row == ("A", "promoted")


def test_creator_scoring_reads_existing_chinese_source_fields(tmp_path):
    export_db = tmp_path / "bilibili_export_store.db"
    rating_db = tmp_path / "bilibili_rating_store.db"
    config = SimpleNamespace(
        export_store_db=export_db,
        rating_store_db=rating_db,
        output_csv=tmp_path / "bilibili_hiatus_ranking.csv",
    )

    with sqlite3.connect(str(export_db)) as conn:
        conn.execute(
            """
            CREATE TABLE bilibili_creator_raw (
                uploader_id TEXT PRIMARY KEY,
                payload_json TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                source_mode TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO bilibili_creator_raw (uploader_id, payload_json, updated_at, source_mode)
            VALUES (?, ?, '2026-06-07 00:00:00', 'current')
            """,
            (
                "1001",
                '{"UP主姓名":"测试UP","UP主UID":"1001","UP主主页链接":"https://space.bilibili.com/1001","粉丝数":12345,"获赞总数":67890,"播放总数":246810,"发布视频数量":9,"平均几天一更":3.5,"最后活跃/发布日期":"2026-06-01 12:00:00","未更新天数":6}',
            ),
        )
        conn.commit()

    _write_table(
        rating_db,
        "video_score_current",
        [
            {
                "uploader_name": "测试UP",
                "uploader_id": "1001",
                "video_title": "A1",
                "bvid": "BV1",
                "publish_date": "2026-06-01 12:00:00",
                "publish_timestamp": 1780286400,
                "duration_category": "61~240s",
                "view_count": 10000,
                "like_count": 800,
                "coin_count": 120,
                "favorite_count": 150,
                "final_score": 90.0,
                "final_grade": "A",
            },
            {
                "uploader_name": "测试UP",
                "uploader_id": "1001",
                "video_title": "A2",
                "bvid": "BV2",
                "publish_date": "2026-05-28 12:00:00",
                "publish_timestamp": 1779940800,
                "duration_category": "61~240s",
                "view_count": 9000,
                "like_count": 700,
                "coin_count": 110,
                "favorite_count": 130,
                "final_score": 88.0,
                "final_grade": "A",
            },
        ],
    )

    rows = BilibiliCreatorScorer(config).score()
    assert len(rows) == 1
    row = rows[0]
    assert row["uploader_name"] == "测试UP"
    assert row["follower_count"] == 12345
    assert row["total_view_count"] == 246810
    assert row["total_like_count"] == 67890
    assert row["published_video_count"] == 9
