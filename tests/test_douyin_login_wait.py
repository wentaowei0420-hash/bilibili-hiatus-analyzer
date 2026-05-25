import sys
from types import SimpleNamespace
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from douyin_analyzer.browser_client import DouyinBrowserClient, DouyinLoginExpiredError
from douyin_analyzer.playwright_browser_client import PlaywrightDouyinBrowserClient


class _FakeDrissionPage:
    def __init__(self, needs_login):
        self._needs_login = needs_login

    def ele(self, *_args, **_kwargs):
        return self._needs_login


class _FakePlaywrightLocator:
    def __init__(self, visible):
        self.first = self
        self._visible = visible

    def is_visible(self, **_kwargs):
        return self._visible


class _FakePlaywrightPage:
    def __init__(self, needs_login):
        self._needs_login = needs_login

    def locator(self, *_args, **_kwargs):
        return _FakePlaywrightLocator(self._needs_login)


def _base_config():
    return SimpleNamespace(home_url="https://www.douyin.com/", page_load_delay=0)


def test_drission_ensure_login_waits_without_stdin():
    client = DouyinBrowserClient(_base_config())
    client._open_page = lambda *_args, **_kwargs: _FakeDrissionPage(needs_login=True)
    client._print_login_persistence_diagnostic = lambda *_args, **_kwargs: None
    client._page_has_login_dialog = lambda: True
    wait_calls = []
    client._wait_until_login_dialog_gone = lambda timeout_seconds=180: wait_calls.append(timeout_seconds) or True

    client.ensure_login()

    assert wait_calls == [180]


def test_drission_ensure_login_raises_when_login_not_completed():
    client = DouyinBrowserClient(_base_config())
    client._open_page = lambda *_args, **_kwargs: _FakeDrissionPage(needs_login=True)
    client._print_login_persistence_diagnostic = lambda *_args, **_kwargs: None
    client._page_has_login_dialog = lambda: True
    client._wait_until_login_dialog_gone = lambda timeout_seconds=180: False

    with pytest.raises(DouyinLoginExpiredError):
        client.ensure_login()


def test_playwright_ensure_login_waits_without_stdin():
    client = PlaywrightDouyinBrowserClient(_base_config())
    client._open_page = lambda *_args, **_kwargs: _FakePlaywrightPage(needs_login=True)
    client._print_login_persistence_diagnostic = lambda *_args, **_kwargs: None
    client._page_has_login_dialog = lambda: True
    wait_calls = []
    client._wait_until_login_dialog_gone = lambda timeout_seconds=180: wait_calls.append(timeout_seconds) or True

    client.ensure_login()

    assert wait_calls == [180]
