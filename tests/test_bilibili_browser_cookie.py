from pathlib import Path

from bilibili_analyzer.browser_cookie import (
    BilibiliCookieSyncResult,
    auto_refresh_bilibili_cookie,
    close_browser_page,
    cookies_from_browser_payload,
    format_cookie_header,
    has_login_cookie,
    save_bilibili_cookie_to_env,
)


def test_cookies_from_browser_payload_filters_bilibili_domain():
    cookies = cookies_from_browser_payload(
        [
            {"domain": ".bilibili.com", "name": "SESSDATA", "value": "sess"},
            {"domain": ".bilibili.com", "name": "DedeUserID", "value": "123"},
            {"domain": ".example.com", "name": "ignored", "value": "x"},
        ]
    )

    assert cookies["SESSDATA"] == "sess"
    assert cookies["DedeUserID"] == "123"
    assert "ignored" not in cookies
    assert has_login_cookie(cookies) is True


def test_format_cookie_header_keeps_name_value_pairs():
    header = format_cookie_header({"SESSDATA": "sess", "DedeUserID": "123"})

    assert "SESSDATA=sess" in header
    assert "DedeUserID=123" in header
    assert "; " in header


def test_save_bilibili_cookie_to_env_writes_value(tmp_path):
    env_path = tmp_path / ".env"

    save_bilibili_cookie_to_env("SESSDATA=sess; DedeUserID=123", env_path)

    text = env_path.read_text(encoding="utf-8")
    assert "BILIBILI_COOKIE=" in text
    assert "SESSDATA=sess; DedeUserID=123" in text


def test_auto_refresh_bilibili_cookie_writes_loaded_cookie(monkeypatch, tmp_path):
    import bilibili_analyzer.browser_cookie as browser_cookie

    monkeypatch.setattr(
        browser_cookie,
        "load_bilibili_cookie_from_browser",
        lambda _root: BilibiliCookieSyncResult(
            ok=True,
            message="ok",
            cookie="SESSDATA=sess; DedeUserID=123",
            source="Microsoft Edge/Default",
            cookie_count=2,
        ),
    )

    result = auto_refresh_bilibili_cookie(Path(tmp_path))

    assert result.ok is True
    assert result.source == "Microsoft Edge/Default"
    assert "BILIBILI_COOKIE" in (tmp_path / ".env").read_text(encoding="utf-8")


def test_close_browser_page_prefers_quit():
    class FakePage:
        def __init__(self):
            self.calls = []

        def quit(self):
            self.calls.append("quit")

        def close(self):
            self.calls.append("close")

    page = FakePage()

    close_browser_page(page)

    assert page.calls == ["quit"]
