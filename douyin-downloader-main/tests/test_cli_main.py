import importlib
from types import SimpleNamespace

import pytest

main_module = importlib.import_module("cli.main")


class _FakeCookieManager:
    def get_cookies(self):
        return {"msToken": "token-1"}


class _FakeAPIClient:
    def __init__(self, _cookies, proxy=None):
        self.proxy = proxy
        self.resolved_urls = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def resolve_short_url(self, short_url: str):
        self.resolved_urls.append(short_url)
        return "https://www.douyin.com/video/7604129988555574538"


class _FakeDownloader:
    async def download(self, parsed):
        return SimpleNamespace(total=1, success=1, failed=0, skipped=0, parsed=parsed)


def test_load_urls_from_file_ignores_blank_lines_and_comments(tmp_path):
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        """
# comment
https://www.douyin.com/video/1

https://www.douyin.com/video/2
https://www.douyin.com/video/1
""",
        encoding="utf-8",
    )

    assert main_module._load_urls_from_file(str(url_file)) == [
        "https://www.douyin.com/video/1",
        "https://www.douyin.com/video/2",
    ]


def test_load_high_like_video_rows_reads_chinese_headers(tmp_path):
    csv_file = tmp_path / "high_like.csv"
    csv_file.write_text(
        """UP主,视频ID,视频标题,视频链接,点赞数
作者,7612969279038927601,标题,https://www.douyin.com/video/7612969279038927601,2245647
作者,7612969279038927601,重复,https://www.douyin.com/video/7612969279038927601,2245647
""",
        encoding="utf-8-sig",
    )

    rows = main_module._load_high_like_video_rows(str(csv_file))

    assert len(rows) == 1
    assert rows[0]["aweme_id"] == "7612969279038927601"
    assert rows[0]["video_url"] == "https://www.douyin.com/video/7612969279038927601"


def test_append_failed_high_like_rows_writes_csv(tmp_path):
    failed_csv = tmp_path / "failed.csv"

    main_module._append_failed_high_like_rows(
        str(failed_csv),
        [
            {
                "failed_time": "2026-04-28T02:30:00",
                "failure_reason": "failed",
                "UP主": "作者",
                "视频ID": "123",
                "视频链接": "https://www.douyin.com/video/123",
            }
        ],
    )

    content = failed_csv.read_text(encoding="utf-8-sig")
    assert "failure_reason" in content
    assert "https://www.douyin.com/video/123" in content


@pytest.mark.asyncio
async def test_download_url_resolves_short_link_before_parsing(monkeypatch, tmp_path):
    config = main_module.ConfigLoader()
    config.update(path=str(tmp_path))

    parsed_inputs = []

    def _fake_parse(url: str):
        parsed_inputs.append(url)
        return {"type": "video", "aweme_id": "7604129988555574538"}

    fake_downloader = _FakeDownloader()

    monkeypatch.setattr(main_module, "DouyinAPIClient", _FakeAPIClient)
    monkeypatch.setattr(main_module.URLParser, "parse", _fake_parse)
    monkeypatch.setattr(
        main_module.DownloaderFactory,
        "create",
        lambda *_args, **_kwargs: fake_downloader,
    )

    result = await main_module.download_url(
        "https://v.douyin.com/short-link/",
        config,
        _FakeCookieManager(),
        database=None,
        progress_reporter=None,
    )

    assert result is not None
    assert result.success == 1
    assert parsed_inputs == ["https://www.douyin.com/video/7604129988555574538"]


@pytest.mark.asyncio
async def test_download_url_passes_proxy_to_api_client(monkeypatch, tmp_path):
    config = main_module.ConfigLoader()
    config.update(path=str(tmp_path), proxy="http://127.0.0.1:8899")

    captured = {}

    class _ProxyAPIClient(_FakeAPIClient):
        def __init__(self, cookies, proxy=None):
            captured["cookies"] = cookies
            captured["proxy"] = proxy
            super().__init__(cookies, proxy=proxy)

    monkeypatch.setattr(main_module, "DouyinAPIClient", _ProxyAPIClient)
    monkeypatch.setattr(
        main_module.URLParser,
        "parse",
        lambda _url: {"type": "video", "aweme_id": "7604129988555574538"},
    )
    monkeypatch.setattr(
        main_module.DownloaderFactory,
        "create",
        lambda *_args, **_kwargs: _FakeDownloader(),
    )

    result = await main_module.download_url(
        "https://www.douyin.com/video/7604129988555574538",
        config,
        _FakeCookieManager(),
        database=None,
        progress_reporter=None,
    )

    assert result is not None
    assert result.success == 1
    assert captured["proxy"] == "http://127.0.0.1:8899"


@pytest.mark.asyncio
async def test_main_async_imports_urls_from_file_and_updates_config(monkeypatch, tmp_path):
    config_file = tmp_path / "config.yml"
    config_file.write_text(
        """
link:
  - https://www.douyin.com/video/old
path: ./Downloaded/
database: false
cookies:
  msToken: token-1
""",
        encoding="utf-8",
    )
    url_file = tmp_path / "urls.txt"
    url_file.write_text(
        """
https://www.douyin.com/video/11
https://www.douyin.com/video/22
""",
        encoding="utf-8",
    )

    called_urls = []

    async def _fake_download_url(url, config, cookie_manager, database=None, progress_reporter=None):
        called_urls.append(url)
        return SimpleNamespace(total=1, success=1, failed=0, skipped=0)

    monkeypatch.setattr(main_module, "download_url", _fake_download_url)

    args = SimpleNamespace(
        config=str(config_file),
        url=None,
        url_file=str(url_file),
        path=None,
        thread=None,
        verbose=False,
        show_warnings=False,
    )

    await main_module.main_async(args)

    reloaded = main_module.ConfigLoader(str(config_file))
    assert reloaded.get_links() == [
        "https://www.douyin.com/video/11",
        "https://www.douyin.com/video/22",
    ]
    assert called_urls == reloaded.get_links()
