import yaml

from gui_modules.cookie_login_sync import (
    cookies_from_browser_payload,
    filter_douyin_cookies,
    required_missing_keys,
    sync_douyin_cookies_via_browser,
    update_config_cookies,
)


def test_cookies_from_browser_payload_filters_douyin_domain():
    raw = [
        {"name": "ttwid", "value": "tt", "domain": ".douyin.com"},
        {"name": "sessionid", "value": "sid", "domain": "www.douyin.com"},
        {"name": "other", "value": "x", "domain": "example.com"},
    ]

    cookies = cookies_from_browser_payload(raw)

    assert cookies == {"ttwid": "tt", "sessionid": "sid"}


def test_filter_douyin_cookies_keeps_login_and_security_fields():
    cookies = filter_douyin_cookies(
        {
            "ttwid": "tt",
            "sessionid": "sid",
            "__security_mc_1": "sec",
            "random_cookie": "ignored",
        }
    )

    assert cookies == {
        "ttwid": "tt",
        "sessionid": "sid",
        "__security_mc_1": "sec",
    }


def test_required_missing_keys_accepts_sid_guard_as_login():
    assert required_missing_keys(
        {
            "ttwid": "tt",
            "odin_tt": "odin",
            "passport_csrf_token": "csrf",
            "sid_guard": "guard",
        }
    ) == []


def test_update_config_cookies_writes_yaml(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text("path: ./Downloaded/\n", encoding="utf-8")

    update_config_cookies(str(config_path), {"ttwid": "tt", "sessionid": "sid"})

    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["cookies"]["ttwid"] == "tt"
    assert data["cookies"]["sessionid"] == "sid"


def test_sync_douyin_cookies_via_browser_uses_page_factory(tmp_path):
    config_path = tmp_path / "config.yml"
    config_path.write_text(
        yaml.safe_dump(
            {
                "path": "./Downloaded/",
                "browser_fallback": {
                    "user_data_path": str(tmp_path / "profile"),
                },
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )

    class _FakePage:
        def __init__(self):
            self.url = ""

        def get(self, url):
            self.url = url

        def cookies(self, **_kwargs):
            return [
                {"name": "ttwid", "value": "tt", "domain": ".douyin.com"},
                {"name": "odin_tt", "value": "odin", "domain": ".douyin.com"},
                {
                    "name": "passport_csrf_token",
                    "value": "csrf",
                    "domain": ".douyin.com",
                },
                {"name": "sessionid", "value": "sid", "domain": ".douyin.com"},
                {"name": "msToken", "value": "ms", "domain": ".douyin.com"},
            ]

    result = sync_douyin_cookies_via_browser(
        str(config_path),
        timeout_seconds=5,
        poll_interval_seconds=0.2,
        page_factory=lambda **_kwargs: _FakePage(),
        sleep_func=lambda _seconds: None,
    )

    assert result.success is True
    assert result.cookie_count == 5
    data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert data["cookies"]["sessionid"] == "sid"
    assert data["cookies"]["msToken"] == "ms"
