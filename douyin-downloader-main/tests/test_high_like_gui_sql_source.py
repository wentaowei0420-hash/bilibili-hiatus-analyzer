import asyncio
import queue
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from config import ConfigLoader
import gui as gui_module
from gui import HighLikeDownloaderGUI
from gui_modules import data_source
from gui_modules.detail_failure_cooldown import DetailFailureCooldownPolicy
from gui_modules.filtering import (
    FILTER_ALL,
    FILTER_GRADE,
    FILTER_HIGH_LIKE,
    LEGACY_FILTER_ALL,
    LEGACY_FILTER_GRADE,
    LEGACY_FILTER_HIGH_LIKE,
    normalize_filter_mode,
)


def _make_gui_probe(**overrides):
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_filter_mode = overrides.get("mode", "指定等级")
    probe.active_filter_grade = overrides.get("grade", "A")
    probe.active_like_threshold = overrides.get("threshold", 10000)
    probe.active_min_duration = overrides.get("min_duration", 0)
    probe.active_max_duration = overrides.get("max_duration", 0)
    return probe


def _patch_cooldown_policy(monkeypatch, seconds: float):
    monkeypatch.setattr(
        gui_module,
        "DetailFailureCooldownPolicy",
        lambda: DetailFailureCooldownPolicy(
            random_seconds=lambda low, high: seconds
        ),
    )


def test_filter_mode_normalization_keeps_readable_chinese_and_accepts_legacy_mojibake():
    assert normalize_filter_mode(FILTER_ALL) == FILTER_ALL
    assert normalize_filter_mode(FILTER_HIGH_LIKE) == FILTER_HIGH_LIKE
    assert normalize_filter_mode(FILTER_GRADE) == FILTER_GRADE
    assert normalize_filter_mode(LEGACY_FILTER_ALL) == FILTER_ALL
    assert normalize_filter_mode(LEGACY_FILTER_HIGH_LIKE) == FILTER_HIGH_LIKE
    assert normalize_filter_mode(LEGACY_FILTER_GRADE) == FILTER_GRADE


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


def test_sql_source_uses_video_grade_not_creator_grade(tmp_path):
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
                "点赞数" INTEGER,
                "UP手动等级" TEXT,
                "UP自动等级" TEXT,
                "视频最终等级" TEXT,
                "视频最终分" REAL
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO video_score_current (
                "UP主姓名", "视频标题", "视频ID", "点赞数",
                "UP手动等级", "UP自动等级", "视频最终等级", "视频最终分"
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                ("作者A", "视频B", "200000000000000001", 100, "", "D", "B", 80),
                ("作者B", "视频D", "200000000000000002", 200, "B", "B", "D", 30),
            ],
        )
        conn.commit()

    config = ConfigLoader(str(config_path))
    probe = _make_gui_probe(mode="指定等级", grade="B", threshold=10000)
    rows = probe._load_sql_video_rows(config)
    filtered = probe._filter_rows_for_download(rows)

    assert [row["aweme_id"] for row in filtered] == ["200000000000000001"]
    assert filtered[0]["video_grade"] == "B"


def test_sql_source_orders_by_video_final_score_not_duration_category():
    columns = ["视频ID", "时长分类", "点赞数", "视频最终等级", "视频最终分"]

    order_sql = data_source._order_sql(columns)

    assert '"视频最终分"' in order_sql
    assert '"点赞数"' in order_sql
    assert '"时长分类"' not in order_sql
    assert order_sql.index('"视频最终分"') < order_sql.index('"点赞数"')


def test_delete_sql_video_records_removes_candidate_and_manual_rating(tmp_path):
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
                "视频ID" TEXT,
                "视频链接" TEXT,
                "视频最终分" REAL
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
        conn.execute(
            'INSERT INTO video_score_current ("视频ID", "视频链接", "视频最终分") VALUES (?, ?, ?)',
            (
                "7627069176804381617",
                "https://www.douyin.com/video/7627069176804381617",
                99,
            ),
        )
        conn.execute(
            "INSERT INTO douyin_video_manual_rating VALUES (?, ?, ?, ?)",
            ("7627069176804381617", "S", "", "2026-06-06T02:30:00"),
        )
        conn.commit()

    config = ConfigLoader(str(config_path))
    deleted = data_source.delete_sql_video_records(
        config,
        project_root=Path(__file__).resolve().parents[1],
        aweme_id="7627069176804381617",
        video_url="https://www.douyin.com/video/7627069176804381617",
    )

    assert deleted == 2
    with sqlite3.connect(rating_db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM video_score_current").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM douyin_video_manual_rating").fetchone()[0] == 0


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


def test_download_selected_rows_uses_shared_batch_rate_limiter(tmp_path, monkeypatch):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n"
        "rate_limit: 20\n",
        encoding="utf-8",
    )
    rows = [
        {"aweme_id": "100001", "video_url": "https://www.douyin.com/video/100001"},
        {"aweme_id": "100002", "video_url": "https://www.douyin.com/video/100002"},
    ]
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_config_path = str(config_path)
    probe.active_download_path = ""
    probe.active_browser_fallback_enabled = False
    probe.active_filename_template = ""
    probe.active_batch_count = 2
    probe.events = queue.Queue()
    probe.stop_requested = threading.Event()

    class _FakeDatabase:
        async def is_downloaded(self, _aweme_id):
            return False

        async def close(self):
            return None

    acquire_sequence: list[str] = []
    limiter_instances = []

    class _FakeRateLimiter:
        def __init__(self, max_per_second):
            self.max_per_second = max_per_second
            self.calls = 0
            limiter_instances.append(self)

        async def acquire(self):
            self.calls += 1
            acquire_sequence.append("acquire")

    async def _fake_download_url(
        url, _config, _cookie_manager, database=None, progress_reporter=None
    ):
        acquire_sequence.append(f"download:{url.rsplit('/', 1)[-1]}")
        return SimpleNamespace(success=1, failed=0, skipped=0)

    async def _fake_open_database(_config):
        return _FakeDatabase()

    async def _fake_preflight(_selected_rows, _config):
        return None

    monkeypatch.setattr(gui_module, "RateLimiter", _FakeRateLimiter)
    monkeypatch.setattr(gui_module, "download_url", _fake_download_url)
    probe._load_candidate_rows = lambda _config: rows
    probe._filter_rows_for_download = lambda raw_rows, _config: raw_rows
    probe._open_database = _fake_open_database
    probe._preflight_selected_rows = _fake_preflight
    probe._describe_filter = lambda: "test"
    probe._resolve_failed_csv_path = lambda _config: tmp_path / "failed.csv"
    probe._filename_context_for_row = lambda _row, _config: {}
    probe._remove_failed_record = lambda _path, _aweme_id: None
    probe._append_failed_record = lambda _path, _row: None

    result = asyncio.run(probe._download_selected_rows())

    assert result["success"] == 2
    assert len(limiter_instances) == 1
    assert limiter_instances[0].max_per_second == 20.0
    assert limiter_instances[0].calls == 2
    assert acquire_sequence == [
        "acquire",
        "download:100001",
        "acquire",
        "download:100002",
    ]


def test_download_selected_rows_deletes_unavailable_browser_fallback_video(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n"
        "rate_limit: 20\n",
        encoding="utf-8",
    )
    row = {
        "aweme_id": "7627069176804381617",
        "video_url": "https://www.douyin.com/video/7627069176804381617",
    }
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_config_path = str(config_path)
    probe.active_download_path = ""
    probe.active_browser_fallback_enabled = True
    probe.active_filename_template = ""
    probe.active_batch_count = 1
    probe.events = queue.Queue()
    probe.stop_requested = threading.Event()

    class _FakeDatabase:
        async def is_downloaded(self, _aweme_id):
            return False

        async def close(self):
            return None

    class _FakeRateLimiter:
        def __init__(self, max_per_second):
            pass

        async def acquire(self):
            return None

    async def _fake_download_url(
        url, _config, _cookie_manager, database=None, progress_reporter=None
    ):
        return SimpleNamespace(
            success=0,
            failed=1,
            skipped=0,
            error_kind="video_unavailable",
            error="获取视频详情失败：浏览器兜底确认视频不存在",
        )

    async def _fake_open_database(_config):
        return _FakeDatabase()

    async def _fake_preflight(_rows, _config):
        return None

    deleted_rows = []
    failed_rows = []

    monkeypatch.setattr(gui_module, "RateLimiter", _FakeRateLimiter)
    monkeypatch.setattr(gui_module, "download_url", _fake_download_url)
    probe._load_candidate_rows = lambda _config: [row]
    probe._filter_rows_for_download = lambda raw_rows, _config: raw_rows
    probe._open_database = _fake_open_database
    probe._preflight_selected_rows = _fake_preflight
    probe._describe_filter = lambda: "test"
    probe._resolve_failed_csv_path = lambda _config: tmp_path / "failed.csv"
    probe._filename_context_for_row = lambda _row, _config: {}
    probe._remove_failed_record = lambda _path, aweme_id: None
    probe._append_failed_record = lambda _path, failed_row: failed_rows.append(
        failed_row
    )
    probe._delete_unavailable_video_link = lambda _config, deleted_row: deleted_rows.append(
        deleted_row
    ) or 1

    result = asyncio.run(probe._download_selected_rows())

    assert result["success"] == 0
    assert result["failed"] == 0
    assert result["skipped"] == 1
    assert deleted_rows == [row]
    assert failed_rows == []


def test_download_selected_rows_cools_down_after_tenth_detail_api_failure(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n"
        "rate_limit: 20\n",
        encoding="utf-8",
    )
    rows = [
        {
            "aweme_id": f"7000000000000000{i:02d}",
            "video_url": f"https://www.douyin.com/video/7000000000000000{i:02d}",
        }
        for i in range(10)
    ]
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_config_path = str(config_path)
    probe.active_download_path = ""
    probe.active_browser_fallback_enabled = False
    probe.active_filename_template = ""
    probe.active_batch_count = len(rows)
    probe.events = queue.Queue()
    probe.stop_requested = threading.Event()

    class _FakeDatabase:
        async def is_downloaded(self, _aweme_id):
            return False

        async def close(self):
            return None

    class _FakeRateLimiter:
        def __init__(self, max_per_second):
            self.max_per_second = max_per_second

        async def acquire(self):
            return None

    async def _fake_download_url(
        url, _config, _cookie_manager, database=None, progress_reporter=None
    ):
        return SimpleNamespace(
            success=0,
            failed=1,
            skipped=0,
            error_kind="detail_api",
            error="获取视频详情失败：Empty response body for /aweme/v1/web/aweme/detail/",
        )

    async def _fake_open_database(_config):
        return _FakeDatabase()

    async def _fake_preflight(_selected_rows, _config):
        return None

    sleep_calls = []

    async def _fake_sleep_for_decision(decision):
        sleep_calls.append(decision)

    monkeypatch.setattr(gui_module, "RateLimiter", _FakeRateLimiter)
    monkeypatch.setattr(gui_module, "download_url", _fake_download_url)
    _patch_cooldown_policy(monkeypatch, 2.3)
    monkeypatch.setattr(gui_module, "sleep_for_decision", _fake_sleep_for_decision)
    probe._load_candidate_rows = lambda _config: rows
    probe._filter_rows_for_download = lambda raw_rows, _config: raw_rows
    probe._open_database = _fake_open_database
    probe._preflight_selected_rows = _fake_preflight
    probe._describe_filter = lambda: "test"
    probe._resolve_failed_csv_path = lambda _config: tmp_path / "failed.csv"
    probe._filename_context_for_row = lambda _row, _config: {}
    probe._remove_failed_record = lambda _path, _aweme_id: None
    probe._append_failed_record = lambda _path, _row: None

    result = asyncio.run(probe._download_selected_rows())

    assert result["success"] == 0
    assert result["failed"] == 10
    assert len(sleep_calls) == 1
    assert sleep_calls[0].failure_count == 10
    assert sleep_calls[0].seconds == 2.3


def test_download_selected_rows_uses_long_cooldown_after_thirtieth_detail_api_failure(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n"
        "rate_limit: 20\n",
        encoding="utf-8",
    )
    rows = [
        {
            "aweme_id": f"7100000000000000{i:02d}",
            "video_url": f"https://www.douyin.com/video/7100000000000000{i:02d}",
        }
        for i in range(30)
    ]
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_config_path = str(config_path)
    probe.active_download_path = ""
    probe.active_browser_fallback_enabled = False
    probe.active_filename_template = ""
    probe.active_batch_count = len(rows)
    probe.events = queue.Queue()
    probe.stop_requested = threading.Event()

    class _FakeDatabase:
        async def is_downloaded(self, _aweme_id):
            return False

        async def close(self):
            return None

    class _FakeRateLimiter:
        def __init__(self, max_per_second):
            self.max_per_second = max_per_second

        async def acquire(self):
            return None

    async def _fake_download_url(
        url, _config, _cookie_manager, database=None, progress_reporter=None
    ):
        return SimpleNamespace(
            success=0,
            failed=1,
            skipped=0,
            error_kind="detail_api",
            error="获取视频详情失败：Empty response body for /aweme/v1/web/aweme/detail/",
        )

    async def _fake_open_database(_config):
        return _FakeDatabase()

    async def _fake_preflight(_selected_rows, _config):
        return None

    sleep_calls = []

    async def _fake_sleep_for_decision(decision):
        sleep_calls.append(decision)

    monkeypatch.setattr(gui_module, "RateLimiter", _FakeRateLimiter)
    monkeypatch.setattr(gui_module, "download_url", _fake_download_url)
    _patch_cooldown_policy(monkeypatch, 7.8)
    monkeypatch.setattr(gui_module, "sleep_for_decision", _fake_sleep_for_decision)
    probe._load_candidate_rows = lambda _config: rows
    probe._filter_rows_for_download = lambda raw_rows, _config: raw_rows
    probe._open_database = _fake_open_database
    probe._preflight_selected_rows = _fake_preflight
    probe._describe_filter = lambda: "test"
    probe._resolve_failed_csv_path = lambda _config: tmp_path / "failed.csv"
    probe._filename_context_for_row = lambda _row, _config: {}
    probe._remove_failed_record = lambda _path, _aweme_id: None
    probe._append_failed_record = lambda _path, _row: None

    result = asyncio.run(probe._download_selected_rows())

    assert result["success"] == 0
    assert result["failed"] == 30
    assert len(sleep_calls) == 21
    assert sleep_calls[0].failure_count == 10
    assert sleep_calls[-1].failure_count == 30
    assert sleep_calls[-1].seconds == 7.8


def test_download_selected_rows_success_resets_detail_api_cooldown_counter(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n"
        "rate_limit: 20\n",
        encoding="utf-8",
    )
    rows = [
        {
            "aweme_id": f"7200000000000000{i:02d}",
            "video_url": f"https://www.douyin.com/video/7200000000000000{i:02d}",
        }
        for i in range(12)
    ]
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_config_path = str(config_path)
    probe.active_download_path = ""
    probe.active_browser_fallback_enabled = False
    probe.active_filename_template = ""
    probe.active_batch_count = len(rows)
    probe.events = queue.Queue()
    probe.stop_requested = threading.Event()

    class _FakeDatabase:
        async def is_downloaded(self, _aweme_id):
            return False

        async def close(self):
            return None

    class _FakeRateLimiter:
        def __init__(self, max_per_second):
            self.max_per_second = max_per_second

        async def acquire(self):
            return None

    outcomes = [
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
        SimpleNamespace(success=1, failed=0, skipped=0),
        SimpleNamespace(success=0, failed=1, skipped=0, error_kind="detail_api", error="detail"),
    ]

    async def _fake_download_url(
        url, _config, _cookie_manager, database=None, progress_reporter=None
    ):
        return outcomes.pop(0)

    async def _fake_open_database(_config):
        return _FakeDatabase()

    async def _fake_preflight(_selected_rows, _config):
        return None

    sleep_calls = []

    async def _fake_sleep_for_decision(decision):
        sleep_calls.append(decision)

    monkeypatch.setattr(gui_module, "RateLimiter", _FakeRateLimiter)
    monkeypatch.setattr(gui_module, "download_url", _fake_download_url)
    _patch_cooldown_policy(monkeypatch, 2.5)
    monkeypatch.setattr(gui_module, "sleep_for_decision", _fake_sleep_for_decision)
    probe._load_candidate_rows = lambda _config: rows
    probe._filter_rows_for_download = lambda raw_rows, _config: raw_rows
    probe._open_database = _fake_open_database
    probe._preflight_selected_rows = _fake_preflight
    probe._describe_filter = lambda: "test"
    probe._resolve_failed_csv_path = lambda _config: tmp_path / "failed.csv"
    probe._filename_context_for_row = lambda _row, _config: {}
    probe._remove_failed_record = lambda _path, _aweme_id: None
    probe._append_failed_record = lambda _path, _row: None

    result = asyncio.run(probe._download_selected_rows())

    assert result["success"] == 1
    assert result["failed"] == 11
    assert len(sleep_calls) == 1
    assert sleep_calls[0].failure_count == 10


def test_download_selected_rows_video_unavailable_does_not_increase_detail_api_counter(
    tmp_path, monkeypatch
):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        f"path: {tmp_path / 'downloads'}\n"
        "cookies: {}\n"
        "rate_limit: 20\n",
        encoding="utf-8",
    )
    rows = [
        {
            "aweme_id": f"7300000000000000{i:02d}",
            "video_url": f"https://www.douyin.com/video/7300000000000000{i:02d}",
        }
        for i in range(10)
    ]
    probe = object.__new__(HighLikeDownloaderGUI)
    probe.active_config_path = str(config_path)
    probe.active_download_path = ""
    probe.active_browser_fallback_enabled = True
    probe.active_filename_template = ""
    probe.active_batch_count = len(rows)
    probe.events = queue.Queue()
    probe.stop_requested = threading.Event()

    class _FakeDatabase:
        async def is_downloaded(self, _aweme_id):
            return False

        async def close(self):
            return None

    class _FakeRateLimiter:
        def __init__(self, max_per_second):
            self.max_per_second = max_per_second

        async def acquire(self):
            return None

    outcomes = [
        *[
            SimpleNamespace(
                success=0,
                failed=1,
                skipped=0,
                error_kind="detail_api",
                error="detail",
            )
            for _ in range(8)
        ],
        SimpleNamespace(
            success=0,
            failed=1,
            skipped=0,
            error_kind="video_unavailable",
            error="missing",
        ),
        SimpleNamespace(
            success=0,
            failed=1,
            skipped=0,
            error_kind="detail_api",
            error="detail",
        ),
    ]

    async def _fake_download_url(
        url, _config, _cookie_manager, database=None, progress_reporter=None
    ):
        return outcomes.pop(0)

    async def _fake_open_database(_config):
        return _FakeDatabase()

    async def _fake_preflight(_selected_rows, _config):
        return None

    sleep_calls = []
    deleted_rows = []

    async def _fake_sleep_for_decision(decision):
        sleep_calls.append(decision)

    monkeypatch.setattr(gui_module, "RateLimiter", _FakeRateLimiter)
    monkeypatch.setattr(gui_module, "download_url", _fake_download_url)
    _patch_cooldown_policy(monkeypatch, 2.2)
    monkeypatch.setattr(gui_module, "sleep_for_decision", _fake_sleep_for_decision)
    probe._load_candidate_rows = lambda _config: rows
    probe._filter_rows_for_download = lambda raw_rows, _config: raw_rows
    probe._open_database = _fake_open_database
    probe._preflight_selected_rows = _fake_preflight
    probe._describe_filter = lambda: "test"
    probe._resolve_failed_csv_path = lambda _config: tmp_path / "failed.csv"
    probe._filename_context_for_row = lambda _row, _config: {}
    probe._remove_failed_record = lambda _path, _aweme_id: None
    probe._append_failed_record = lambda _path, _row: None
    probe._delete_unavailable_video_link = lambda _config, deleted_row: deleted_rows.append(
        deleted_row
    ) or 1

    result = asyncio.run(probe._download_selected_rows())

    assert result["success"] == 0
    assert result["failed"] == 9
    assert result["skipped"] == 1
    assert deleted_rows == [rows[8]]
    assert sleep_calls == []
