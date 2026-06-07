import time
from pathlib import Path
from types import SimpleNamespace

from bilibili_analyzer.archive import (
    ACTIVE_STATUS,
    load_active_archived_uids,
    load_archive_candidates,
    load_archived_creators,
    archive_creators,
    restore_creators,
)
from bilibili_analyzer.cache import CacheStore


def _config(tmp_path):
    return SimpleNamespace(
        followings_cache_json=tmp_path / "bilibili_followings_cache.json",
        progress_json=tmp_path / "bilibili_hiatus_progress.json",
        video_duration_progress_json=tmp_path / "bilibili_video_duration_progress.json",
        video_duration_progress_dir=tmp_path / "video_duration_progress",
        export_store_db=tmp_path / "bilibili_export_store.db",
        precise_cache_max_age_hours=12,
        video_duration_cache_max_age_hours=672,
    )


def test_bilibili_archive_candidates_and_restore_flow(tmp_path):
    config = _config(tmp_path)
    cache = CacheStore(config)
    old_ts = int(time.time()) - 140 * 86400

    cache.save_followings_cache(
        [
            {
                "mid": "1001",
                "uname": "UP 1001",
                "follower_count": 12345,
                "total_favorited": 67890,
                "total_view_count": 13579,
            }
        ]
    )
    cache.save_precise_progress(
        {
            "1001": {
                "uploader_id": "1001",
                "uploader_name": "UP 1001",
                "uploader_homepage": "https://space.bilibili.com/1001",
                "published_video_count": 12,
                "upload_timestamp": old_ts,
                "upload_date": "2026-01-01",
                "days_since_update": 140,
            }
        }
    )
    cache.save_video_duration_progress(
        {
            "1001": {
                "uploader_id": "1001",
                "uploader_name": "UP 1001",
                "cached_at": int(time.time()),
                "videos": [
                    {
                        "uploader_id": "1001",
                        "uploader_name": "UP 1001",
                        "video_title": "Test Video",
                        "bvid": "BV1001",
                        "publish_timestamp": old_ts,
                    }
                ],
                "summary": {
                    "uploader_id": "1001",
                    "uploader_name": "UP 1001",
                    "follower_count": 12345,
                    "total_videos": 12,
                    "latest_publish_timestamp": old_ts,
                    "average_update_interval_days": 9.5,
                },
            }
        }
    )

    candidates = load_archive_candidates(config, inactive_days_threshold=100)

    assert len(candidates) == 1
    assert candidates[0]["uploader_id"] == "1001"
    assert candidates[0]["final_grade"] == ""
    assert candidates[0]["has_full_cache"] == "是"
    assert candidates[0]["published_video_count"] == 12

    archived_count = archive_creators(Path(config.export_store_db), candidates)
    assert archived_count == 1
    assert load_active_archived_uids(config.export_store_db) == {"1001"}

    archived_rows = load_archived_creators(config.export_store_db, active_only=True)
    assert len(archived_rows) == 1
    assert archived_rows[0]["archive_status"] == ACTIVE_STATUS

    restored_count = restore_creators(config.export_store_db, ["1001"])
    assert restored_count == 1
    assert load_active_archived_uids(config.export_store_db) == set()
