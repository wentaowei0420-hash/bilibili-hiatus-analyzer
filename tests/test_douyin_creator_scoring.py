import json
import sqlite3
from types import SimpleNamespace

from douyin_analyzer.creator_scoring import DouyinCreatorScorer
from douyin_analyzer.video_scoring import run_douyin_video_scoring


def test_followed_creator_with_liked_s_video_is_included_without_creator_s_override(tmp_path):
    db_path = tmp_path / "export_store.sqlite"
    followings_cache_json = tmp_path / "douyin_followings_cache.json"
    progress_json = tmp_path / "douyin_progress.json"

    followed_uid = "sec-followed"
    unfollowed_uid = "sec-unfollowed"
    followings_cache_json.write_text(
        json.dumps(
            {
                "followings": [
                    {
                        "sec_uid": followed_uid,
                        "nickname": "Followed Creator",
                        "homepage": f"https://www.douyin.com/user/{followed_uid}",
                        "follower_count": 100,
                        "aweme_count": 3,
                        "total_favorited": 300,
                    }
                ]
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    progress_json.write_text(json.dumps({"ups": {}}, ensure_ascii=False), encoding="utf-8")

    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE douyin_video_state (
                video_id TEXT PRIMARY KEY,
                uploader_id TEXT,
                uploader_name TEXT,
                publish_timestamp INTEGER,
                like_count INTEGER,
                duration_seconds INTEGER,
                source_mode TEXT,
                is_available INTEGER DEFAULT 1,
                payload_json TEXT NOT NULL,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE douyin_video_manual_rating (
                video_id TEXT PRIMARY KEY,
                manual_grade TEXT NOT NULL,
                note TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        for video_id, uid in (("liked-followed", followed_uid), ("liked-unfollowed", unfollowed_uid)):
            payload = {
                "aweme_id": video_id,
                "uploader_id": uid,
                "uploader_name": "Creator",
                "source_mode": "liked",
                "video_manual_grade": "S",
                "metadata": {"liked_cache": True},
            }
            conn.execute(
                """
                INSERT INTO douyin_video_state (
                    video_id, uploader_id, uploader_name, publish_timestamp, like_count,
                    duration_seconds, source_mode, is_available, payload_json, metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    video_id,
                    uid,
                    "Creator",
                    1_700_000_000,
                    10,
                    30,
                    "liked",
                    1,
                    json.dumps(payload, ensure_ascii=False),
                    json.dumps({"liked_cache": True, "manual_grade": "S"}, ensure_ascii=False),
                ),
            )
            conn.execute(
                """
                INSERT INTO douyin_video_manual_rating (video_id, manual_grade, note, updated_at)
                VALUES (?, 'S', 'test', '2026-01-01 00:00:00')
                """,
                (video_id,),
            )
        conn.commit()

    config = SimpleNamespace(
        export_store_db=db_path,
        followings_cache_json=followings_cache_json,
        followings_cache_dir=tmp_path / "followings",
        progress_json=progress_json,
        progress_dir=tmp_path / "progress",
        output_csv=tmp_path / "douyin.csv",
    )

    run_douyin_video_scoring(config)
    rows = DouyinCreatorScorer(config).score()

    assert len(rows) == 1
    assert rows[0]["uploader_id"] == followed_uid
    assert rows[0]["grade_s_count"] == 1
    assert rows[0]["final_grade"] != "S"
    assert rows[0]["manual_grade"] == ""
    assert rows[0]["score_source"] == "auto"
