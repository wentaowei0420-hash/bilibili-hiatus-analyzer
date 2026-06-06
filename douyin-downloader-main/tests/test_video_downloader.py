import asyncio
import json
from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from auth import CookieManager
from config import ConfigLoader
from control import QueueManager, RateLimiter, RetryHandler
import core.video_downloader as video_module
from core.api_client import DouyinAPIClient
from core.browser_detail_fallback import (
    BrowserDetailFallback,
    BrowserVideoUnavailableError,
)
from core.video_downloader import VideoDownloader
from storage import FileManager


class _FakeProgressReporter:
    def __init__(self):
        self.step_updates = []
        self.item_totals = []
        self.item_events = []

    def update_step(self, step: str, detail: str = "") -> None:
        self.step_updates.append((step, detail))

    def set_item_total(self, total: int, detail: str = "") -> None:
        self.item_totals.append((total, detail))

    def advance_item(self, status: str, detail: str = "") -> None:
        self.item_events.append((status, detail))


def _build_downloader(tmp_path):
    config = ConfigLoader()
    config.update(path=str(tmp_path))

    file_manager = FileManager(str(tmp_path))
    cookie_manager = CookieManager(str(tmp_path / ".cookies.json"))
    api_client = DouyinAPIClient({})

    downloader = VideoDownloader(
        config,
        api_client,
        file_manager,
        cookie_manager,
        database=None,
        rate_limiter=RateLimiter(max_per_second=5),
        retry_handler=RetryHandler(max_retries=1),
        queue_manager=QueueManager(max_workers=1),
    )

    return downloader, api_client


def test_local_aweme_index_is_reused_between_downloaders(tmp_path):
    aweme_id = "7447096682656894267"
    (tmp_path / f"video_{aweme_id}.mp4").write_bytes(b"video")
    first_downloader, first_api_client = _build_downloader(tmp_path)
    second_downloader, second_api_client = _build_downloader(tmp_path)
    cache_key = str(tmp_path.resolve())
    original_cache = VideoDownloader._local_aweme_index_cache.copy()
    VideoDownloader._local_aweme_index_cache.pop(cache_key, None)

    try:
        first_downloader._build_local_aweme_index()
        assert aweme_id in first_downloader._local_aweme_ids

        (tmp_path / "video_7555555555555555555.mp4").write_bytes(b"new video")
        second_downloader._build_local_aweme_index()
        assert second_downloader._local_aweme_ids is first_downloader._local_aweme_ids
        assert "7555555555555555555" not in second_downloader._local_aweme_ids
    finally:
        VideoDownloader._local_aweme_index_cache.clear()
        VideoDownloader._local_aweme_index_cache.update(original_cache)
        asyncio.run(first_api_client.close())
        asyncio.run(second_api_client.close())


@pytest.mark.asyncio
async def test_video_downloader_skip_counts_total(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    async def _fake_should_download(self, _):
        return False

    downloader._should_download = _fake_should_download.__get__(
        downloader, VideoDownloader
    )

    result = await downloader.download({"aweme_id": "123"})

    assert result.total == 1
    assert result.skipped == 1
    assert result.success == 0
    assert result.failed == 0

    await api_client.close()


@pytest.mark.asyncio
async def test_video_downloader_reports_item_progress(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    reporter = _FakeProgressReporter()
    downloader.progress_reporter = reporter

    async def _fake_should_download(self, _aweme_id):
        return True

    async def _fake_get_video_detail(_aweme_id: str):
        return {"aweme_id": "123", "author": {"nickname": "tester"}}

    async def _fake_download_aweme(self, _aweme_data):
        return True

    downloader._should_download = _fake_should_download.__get__(
        downloader, VideoDownloader
    )
    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    downloader._download_aweme = _fake_download_aweme.__get__(
        downloader, VideoDownloader
    )

    result = await downloader.download({"aweme_id": "123"})

    assert result.total == 1
    assert result.success == 1
    assert reporter.item_totals == [(1, "单视频下载")]
    assert ("下载作品", "单视频资源下载中") in reporter.step_updates
    assert reporter.item_events == [("success", "123")]

    await api_client.close()


def test_fetch_aweme_data_uses_browser_fallback_when_enabled(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    api_client.last_error = "Empty response body"
    downloader.config.update(
        browser_fallback={
            "enabled": True,
            "user_data_path": str(tmp_path / "profile"),
            "detail_timeout_seconds": 7,
            "detail_page_load_delay": 0,
        }
    )

    async def _fake_get_video_detail(_aweme_id):
        return None

    calls = []

    class _FakeFallback:
        def fetch(self, aweme_id, **kwargs):
            calls.append((aweme_id, kwargs))
            return {"aweme_id": aweme_id, "author": {"nickname": "browser"}}

    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    monkeypatch.setattr(
        video_module,
        "get_shared_browser_detail_fallback",
        lambda: _FakeFallback(),
    )

    detail = asyncio.run(downloader._fetch_aweme_data("123"))

    assert detail["aweme_id"] == "123"
    assert calls == [
        (
            "123",
            {
                "user_data_path": str(tmp_path / "profile"),
                "browser_binary_path": "",
                "timeout_seconds": 7,
                "page_load_delay": 0.0,
            },
        )
    ]

    asyncio.run(api_client.close())


def test_fetch_aweme_data_does_not_use_browser_fallback_when_disabled(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(browser_fallback={"enabled": False})

    async def _fake_get_video_detail(_aweme_id):
        return None

    class _UnexpectedFallback:
        def fetch(self, *_args, **_kwargs):
            raise AssertionError("browser fallback should not be called")

    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    monkeypatch.setattr(
        video_module,
        "get_shared_browser_detail_fallback",
        lambda: _UnexpectedFallback(),
    )

    detail = asyncio.run(downloader._fetch_aweme_data("123"))

    assert detail is None

    asyncio.run(api_client.close())


def test_browser_detail_fallback_raises_when_page_says_video_unavailable():
    class _FakeListen:
        def start(self, _patterns):
            return None

        def stop(self):
            return None

        def steps(self, timeout, gap):
            return iter(())

    class _FakePage:
        listen = _FakeListen()
        title = "在抖音记录美好生活"
        html = "<main>你要观看的视频不存在</main>"

        def get(self, _url):
            return None

    fallback = BrowserDetailFallback()

    with pytest.raises(BrowserVideoUnavailableError):
        fallback._fetch_with_page(
            _FakePage(),
            "7627069176804381617",
            timeout_seconds=5,
            page_load_delay=0,
        )


def test_video_download_result_exposes_browser_unavailable_failure(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(browser_fallback={"enabled": True})
    api_client.last_error = "Empty response body for /aweme/v1/web/aweme/detail/"

    async def _fake_get_video_detail(_aweme_id):
        return None

    class _UnavailableFallback:
        def fetch(self, *_args, **_kwargs):
            raise BrowserVideoUnavailableError("gone")

    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)
    monkeypatch.setattr(
        video_module,
        "get_shared_browser_detail_fallback",
        lambda: _UnavailableFallback(),
    )

    result = asyncio.run(downloader.download({"aweme_id": "7627069176804381617"}))

    assert result.failed == 1
    assert result.error_kind == "video_unavailable"
    assert "视频不存在" in result.error

    asyncio.run(api_client.close())


def test_video_download_result_exposes_detail_api_failure(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(browser_fallback={"enabled": False})
    api_client.last_error = "Empty response body for /aweme/v1/web/aweme/detail/"

    async def _fake_get_video_detail(_aweme_id):
        return None

    monkeypatch.setattr(api_client, "get_video_detail", _fake_get_video_detail)

    result = asyncio.run(downloader.download({"aweme_id": "6672593134737820942"}))

    assert result.failed == 1
    assert result.error_kind == "detail_api"
    assert "Empty response body" in result.error

    asyncio.run(api_client.close())


def test_browser_fallback_default_profile_is_separate_from_main_analyzer():
    profile_path = Path(video_module.VideoDownloader._browser_user_data_path({}))

    assert profile_path.name == "downloader_edge_data"
    assert profile_path.name != "edge_data"


@pytest.mark.asyncio
async def test_build_no_watermark_url_signs_with_headers(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)

    signed_url = "https://www.douyin.com/aweme/v1/play/?video_id=1&X-Bogus=signed"

    def _fake_sign(url: str):
        return signed_url, "UnitTestAgent/1.0"

    monkeypatch.setattr(api_client, "sign_url", _fake_sign)

    aweme = {
        "aweme_id": "1",
        "video": {
            "play_addr": {
                "url_list": [
                    "https://www.douyin.com/aweme/v1/play/?video_id=1&watermark=0"
                ]
            }
        },
    }

    url, headers = downloader._build_no_watermark_url(aweme)

    assert url == signed_url
    assert headers["User-Agent"] == "UnitTestAgent/1.0"
    assert headers["Accept"] == "*/*"
    assert headers["Referer"].startswith("https://www.douyin.com")

    await api_client.close()


@pytest.mark.asyncio
async def test_should_download_skips_when_aweme_exists_locally(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)
    aweme_id = "7600223638943468863"

    existing_file = tmp_path / f"2026-02-18_demo_{aweme_id}.mp4"
    existing_file.write_bytes(b"1")

    should_download = await downloader._should_download(aweme_id)
    assert should_download is False

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_uses_publish_date_and_writes_manifest(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_id = "7600224486650121526"
    publish_ts = 1707303025
    expected_date_prefix = datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "测试下载日期文件名",
        "create_time": publish_ts,
        "text_extra": [{"hashtag_name": "测试标签"}],
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert len(saved_paths) == 1

    save_path = saved_paths[0]
    assert save_path.name.startswith(f"{expected_date_prefix}_")
    assert aweme_id in save_path.name
    assert save_path.parent.name.startswith(f"{expected_date_prefix}_")

    manifest_path = tmp_path / "download_manifest.jsonl"
    assert manifest_path.exists()
    lines = manifest_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1

    manifest_entry = json.loads(lines[0])
    assert manifest_entry["date"] == expected_date_prefix
    assert manifest_entry["aweme_id"] == aweme_id
    assert manifest_entry["tags"] == ["测试标签"]
    assert save_path.name in manifest_entry["file_names"]

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_uses_filename_template(tmp_path, monkeypatch):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=False,
        filename_template="{author}_{date}_{like_count}",
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_id = "7600224486650121528"
    publish_ts = 1707303025
    publish_date = datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "模板测试",
        "create_time": publish_ts,
        "author": {"nickname": "模板作者"},
        "statistics": {"digg_count": 888},
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }

    success = await downloader._download_aweme_assets(aweme_data, author_name="备用作者")

    assert success is True
    assert saved_paths[0].name == f"模板作者_{publish_date}_888_{aweme_id}.mp4"

    await api_client.close()


def test_build_file_stem_supports_chinese_filename_context(tmp_path):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        filename_template="等级_UP主_视频标题_点赞数",
        filename_context={
            "视频等级": "A",
            "发表时间": "2026-05-09 13:14:15",
            "UP主": "测试作者",
            "视频标题": "模板标题",
            "点赞数": "1234",
        },
    )

    aweme_id = "7600224486650121528"
    stem = downloader._build_file_stem(
        {
            "aweme_id": aweme_id,
            "author": {"nickname": "接口作者"},
            "statistics": {"digg_count": 888},
        },
        author_name="备用作者",
        title="接口标题",
        publish_date="2026-05-09",
    )

    assert stem == f"A_测试作者_模板标题_1234_{aweme_id}"

    downloader.config.update(filename_template="等级_发表时间_UP主_视频标题_点赞数")
    stem = downloader._build_file_stem(
        {
            "aweme_id": aweme_id,
            "author": {"nickname": "接口作者"},
            "statistics": {"digg_count": 888},
        },
        author_name="备用作者",
        title="接口标题",
        publish_date="2026-05-09",
    )
    assert stem == f"A_2026-05-09_13_14_15_测试作者_模板标题_1234_{aweme_id}"

    downloader.config.update(filename_template="视频id_作品ID_aweme_id_video_id")
    stem = downloader._build_file_stem(
        {
            "aweme_id": aweme_id,
            "author": {"nickname": "接口作者"},
            "statistics": {"digg_count": 888},
        },
        author_name="备用作者",
        title="接口标题",
        publish_date="2026-05-09",
    )
    assert stem == f"{aweme_id}_{aweme_id}_{aweme_id}_{aweme_id}"
    asyncio.run(api_client.close())


@pytest.mark.asyncio
async def test_download_aweme_assets_keeps_success_when_transcript_skipped(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=False,
        avatar=False,
        json=False,
        folderstyle=True,
        transcript={
            "enabled": True,
            "api_key_env": "OPENAI_API_KEY",
            "api_key": "",
            "output_dir": "",
            "response_formats": ["txt", "json"],
        },
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    async def _fake_download_with_retry(self, _url, _save_path, _session, **_kwargs):
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_data = {
        "aweme_id": "7600224486650121527",
        "desc": "转写缺 key 也不应影响下载",
        "video": {"play_addr": {"url_list": ["https://example.com/video.mp4"]}},
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_video_writes_cover_avatar_and_json(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False,
        cover=True,
        avatar=True,
        json=True,
        folderstyle=True,
        transcript={"enabled": False},
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_data = {
        "aweme_id": "7600224486650121527",
        "desc": "附加资源",
        "create_time": 1707303025,
        "author": {
            "nickname": "测试作者",
            "avatar_larger": {"url_list": ["https://example.com/avatar.jpg"]},
        },
        "video": {
            "play_addr": {"url_list": ["https://example.com/video.mp4"]},
            "cover": {"url_list": ["https://example.com/cover.jpg"]},
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert any(path.name.endswith(".mp4") for path in saved_paths)
    assert any(path.name.endswith("_cover.jpg") for path in saved_paths)
    assert any(path.name.endswith("_avatar.jpg") for path in saved_paths)
    metadata_files = list(tmp_path.rglob("*_data.json"))
    assert len(metadata_files) == 1

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_downloads_live_photo_videos(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_data = {
        "aweme_id": "7600224486650121528",
        "desc": "实况图文",
        "image_post_info": {
            "images": [
                {
                    "display_image": {"url_list": ["https://example.com/1.webp"]},
                    "video": {
                        "play_addr": {"url_list": ["https://example.com/1_live.mp4"]}
                    },
                },
                {
                    "video": {
                        "play_addr": {"url_list": ["https://example.com/2_live.mp4"]}
                    },
                },
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert any(path.suffix == ".webp" for path in saved_paths)
    assert sum(path.suffix == ".mp4" for path in saved_paths) == 2
    assert any("_live_1.mp4" in path.name for path in saved_paths)
    assert any("_live_2.mp4" in path.name for path in saved_paths)

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_preserves_real_image_extensions(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_data = {
        "aweme_id": "7600224486650121991",
        "desc": "图集后缀归一化",
        "image_post_info": {
            "images": [
                {
                    "display_image": {
                        "url_list": [
                            "https://example.com/gallery_1.png~tplv-obj.image?x=1"
                        ]
                    }
                },
                {
                    "display_image": {
                        "url_list": [
                            "https://example.com/gallery_2.jpeg~tplv-resize:1080:0.image"
                        ]
                    }
                },
                {
                    "display_image": {
                        "url_list": ["https://example.com/gallery_3.jpg?from=unit-test"]
                    }
                },
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert [path.suffix for path in saved_paths] == [".png", ".jpeg", ".jpg"]

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_uses_response_content_type_for_suffix(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    content = b"fake png content"
    publish_ts = 1707303025
    publish_date = datetime.fromtimestamp(publish_ts).strftime("%Y-%m-%d")
    aweme_id = "7600224486650121992"

    mock_response = AsyncMock()
    mock_response.status = 200
    mock_response.content_length = len(content)
    mock_response.headers = {"Content-Type": "image/png; charset=binary"}

    async def iter_chunked(_size):
        yield content

    mock_response.content = MagicMock()
    mock_response.content.iter_chunked = iter_chunked

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=mock_response)
    ctx.__aexit__ = AsyncMock(return_value=False)

    mock_session = MagicMock()
    mock_session.get.return_value = ctx

    async def _fake_get_session():
        return mock_session

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    aweme_data = {
        "aweme_id": aweme_id,
        "desc": "响应头决定后缀",
        "create_time": publish_ts,
        "image_post_info": {
            "images": [
                {
                    "display_image": {
                        "url_list": ["https://example.com/gallery_1.image?x=1"]
                    }
                }
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    save_dir = tmp_path / "测试作者" / "post" / f"{publish_date}_响应头决定后缀_{aweme_id}"
    saved_files = sorted(path.name for path in save_dir.iterdir() if path.is_file())
    assert saved_files == [f"{publish_date}_响应头决定后缀_{aweme_id}_1.png"]

    manifest_path = tmp_path / "download_manifest.jsonl"
    lines = manifest_path.read_text(encoding="utf-8").strip().splitlines()
    manifest_entry = json.loads(lines[-1])
    assert manifest_entry["file_names"] == saved_files

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_succeeds_with_only_live_videos(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_data = {
        "aweme_id": "7600224486650121529",
        "desc": "仅实况图文",
        "image_post_info": {
            "images": [
                {
                    "video": {
                        "play_addr": {
                            "url_list": ["https://example.com/only_live_1.mp4"]
                        }
                    }
                },
                {
                    "video": {
                        "play_addr": {
                            "url_list": ["https://example.com/only_live_2.mp4"]
                        }
                    }
                },
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is True
    assert len(saved_paths) == 2
    assert all(path.suffix == ".mp4" for path in saved_paths)
    assert any("_live_1.mp4" in path.name for path in saved_paths)
    assert any("_live_2.mp4" in path.name for path in saved_paths)

    await api_client.close()


@pytest.mark.asyncio
async def test_download_aweme_assets_gallery_fails_when_live_video_download_fails(
    tmp_path, monkeypatch
):
    downloader, api_client = _build_downloader(tmp_path)
    downloader.config.update(
        music=False, cover=False, avatar=False, json=False, folderstyle=True
    )

    async def _fake_get_session():
        return object()

    monkeypatch.setattr(api_client, "get_session", _fake_get_session)

    saved_paths = []

    async def _fake_download_with_retry(self, _url, save_path, _session, **_kwargs):
        saved_paths.append(save_path)
        if save_path.name.endswith("_live_2.mp4"):
            return False
        return True

    downloader._download_with_retry = _fake_download_with_retry.__get__(
        downloader, VideoDownloader
    )

    aweme_data = {
        "aweme_id": "7600224486650121530",
        "desc": "实况下载失败场景",
        "image_post_info": {
            "images": [
                {
                    "display_image": {"url_list": ["https://example.com/ok.webp"]},
                    "video": {
                        "play_addr": {"url_list": ["https://example.com/live_ok.mp4"]}
                    },
                },
                {
                    "video": {
                        "play_addr": {"url_list": ["https://example.com/live_fail.mp4"]}
                    }
                },
            ]
        },
    }

    success = await downloader._download_aweme_assets(
        aweme_data, author_name="测试作者", mode="post"
    )

    assert success is False
    assert any(path.name.endswith(".webp") for path in saved_paths)
    assert any(path.name.endswith("_live_1.mp4") for path in saved_paths)
    assert any(path.name.endswith("_live_2.mp4") for path in saved_paths)

    await api_client.close()


def test_detect_media_type_by_aweme_type(tmp_path):
    """aweme_type 2/68/150 should be detected as gallery even without images key."""
    downloader, api_client = _build_downloader(tmp_path)

    for aweme_type in (2, 68, 150):
        assert downloader._detect_media_type({"aweme_type": aweme_type}) == "gallery"

    assert downloader._detect_media_type({"aweme_type": 4}) == "video"
    assert downloader._detect_media_type({"aweme_type": 0}) == "video"
    assert downloader._detect_media_type({}) == "video"

    asyncio.run(api_client.close())


def test_collect_image_urls_old_format_url_list(tmp_path):
    """Old format: items have url_list directly."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100001",
        "images": [
            {"url_list": ["https://example.com/img1.webp"]},
            {"url_list": ["https://example.com/img2.webp"]},
        ],
    }

    urls = downloader._collect_image_urls(aweme_data)
    assert urls == [
        "https://example.com/img1.webp",
        "https://example.com/img2.webp",
    ]

    asyncio.run(api_client.close())


def test_collect_image_urls_old_format_download_url_list(tmp_path):
    """Old format: items have download_url_list (list) directly."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100002",
        "images": [
            {
                "url_list": ["https://example.com/preview1.webp"],
                "download_url_list": ["https://example.com/download1.webp"],
            },
        ],
    }

    urls = downloader._collect_image_urls(aweme_data)
    # download_url_list should be preferred over url_list
    assert urls == ["https://example.com/download1.webp"]

    asyncio.run(api_client.close())


def test_collect_image_urls_new_format_download_url_preferred(tmp_path):
    """New format: download_url dict is preferred over display_image."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100003",
        "image_post_info": {
            "images": [
                {
                    "download_url": {
                        "url_list": ["https://cdn.example.com/download.webp"]
                    },
                    "display_image": {
                        "url_list": ["https://cdn.example.com/display.webp"]
                    },
                },
            ]
        },
    }

    urls = downloader._collect_image_urls(aweme_data)
    assert urls == ["https://cdn.example.com/download.webp"]

    asyncio.run(api_client.close())


def test_iter_gallery_items_image_list_key(tmp_path):
    """Some responses use image_list instead of images."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100004",
        "image_post_info": {
            "image_list": [
                {"display_image": {"url_list": ["https://example.com/img.webp"]}}
            ]
        },
    }

    items = downloader._iter_gallery_items(aweme_data)
    assert len(items) == 1
    assert items[0]["display_image"]["url_list"][0] == "https://example.com/img.webp"

    asyncio.run(api_client.close())


def test_iter_gallery_items_top_level_image_list(tmp_path):
    """Fallback: top-level image_list key."""
    downloader, api_client = _build_downloader(tmp_path)

    aweme_data = {
        "aweme_id": "100005",
        "image_list": [
            {"url_list": ["https://example.com/top.webp"]}
        ],
    }

    items = downloader._iter_gallery_items(aweme_data)
    assert len(items) == 1

    asyncio.run(api_client.close())
