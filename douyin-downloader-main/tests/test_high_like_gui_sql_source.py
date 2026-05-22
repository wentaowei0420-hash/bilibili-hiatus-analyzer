import asyncio
import sqlite3
from pathlib import Path

import pytest

from config import ConfigLoader
import gui as gui_module
from gui import HighLikeDownloaderGUI


def _make_gui_probe(**overrides):
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_filter_mode = overrides.get("mode", "指定等级")
    probe.active_filter_grade = overrides.get("grade", "A")
    probe.active_like_threshold = overrides.get("threshold", 10000)
    probe.active_min_duration = overrides.get("min_duration", 0)
    probe.active_max_duration = overrides.get("max_duration", 0)
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


def test_preflight_selected_rows_raises_clear_error_when_detail_api_fails(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n",
        encoding="utf-8",
    )
    config = ConfigLoader(str(config_path))
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_browser_fallback_enabled = False
    probe.active_preflight_sample_enabled = True

    class _FakeAPIClient:
        def __init__(self, *_args, **_kwargs):
            self.last_error = "Empty response body for /aweme/v1/web/aweme/detail/"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_video_detail(self, _aweme_id, suppress_error=False):
            return None

    monkeypatch.setattr(gui_module, "DouyinAPIClient", _FakeAPIClient)

    with pytest.raises(RuntimeError) as exc_info:
        asyncio.run(
            probe._preflight_selected_rows(
                [
                    {"aweme_id": "100001", "video_url": "https://www.douyin.com/video/100001"},
                    {"aweme_id": "100002", "video_url": "https://www.douyin.com/video/100002"},
                    {"aweme_id": "100003", "video_url": "https://www.douyin.com/video/100003"},
                ],
                config,
            )
        )

    message = str(exc_info.value)
    assert "指定等级筛选本身没有问题" in message
    assert "Empty response body" in message


def test_preflight_selected_rows_can_be_disabled(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n",
        encoding="utf-8",
    )
    config = ConfigLoader(str(config_path))
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_browser_fallback_enabled = False
    probe.active_preflight_sample_enabled = False

    class _UnexpectedAPIClient:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("preflight API should not be called")

    monkeypatch.setattr(gui_module, "DouyinAPIClient", _UnexpectedAPIClient)

    asyncio.run(
        probe._preflight_selected_rows(
            [
                {"aweme_id": "100001", "video_url": "https://www.douyin.com/video/100001"},
            ],
            config,
        )
    )
