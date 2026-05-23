import asyncio
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

from config import ConfigLoader
import gui_modules.cookie_check as cookie_check


def _config(cookies=None, links=None):
    config = ConfigLoader()
    config.update(
        cookies=cookies or {},
        link=links or ["https://www.douyin.com/video/7000000000000000001"],
    )
    return config


def _sid_guard(days: int) -> str:
    expires_at = datetime.now(timezone.utc) + timedelta(days=days)
    return f"sid|0|0|{format_datetime(expires_at, usegmt=True)}"


def _valid_cookies():
    return {
        "ttwid": "ttwid",
        "odin_tt": "odin",
        "passport_csrf_token": "csrf",
        "sessionid": "session",
        "sid_guard": _sid_guard(30),
        "msToken": "token",
    }


def test_cookie_check_reports_missing_cookies():
    result = asyncio.run(cookie_check.check_douyin_cookie_status(_config(cookies={})))

    assert result.status == "error"
    assert "未读取到任何 Cookie" in result.summary


def test_cookie_check_passes_when_detail_api_returns_detail(monkeypatch):
    class _FakeClient:
        last_error = ""

        def __init__(self, *_args, **_kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_video_detail(self, _aweme_id, suppress_error=False):
            return {"aweme_id": "7000000000000000001"}

    monkeypatch.setattr(cookie_check, "DouyinAPIClient", _FakeClient)

    result = asyncio.run(
        cookie_check.check_douyin_cookie_status(_config(cookies=_valid_cookies()))
    )

    assert result.status == "ok"
    assert "详情接口" in result.to_message()


def test_cookie_check_flags_empty_detail_response(monkeypatch):
    class _FakeClient:
        def __init__(self, *_args, **_kwargs):
            self.last_error = "Empty response body for /aweme/v1/web/aweme/detail/"

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get_video_detail(self, _aweme_id, suppress_error=False):
            return None

    monkeypatch.setattr(cookie_check, "DouyinAPIClient", _FakeClient)

    result = asyncio.run(
        cookie_check.check_douyin_cookie_status(_config(cookies=_valid_cookies()))
    )

    assert result.status == "error"
    assert "详情接口返回空响应" in result.to_message()
    assert "浏览器兜底" in result.to_message()
