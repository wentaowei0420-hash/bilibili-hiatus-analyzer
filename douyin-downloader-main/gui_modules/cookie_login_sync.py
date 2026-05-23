from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from config import ConfigLoader
from utils.cookie_utils import sanitize_cookies


DOUYIN_HOME = "https://www.douyin.com/"
LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss", "sid_guard", "sid_tt")
FOUNDATION_COOKIE_KEYS = ("ttwid", "odin_tt", "passport_csrf_token")
SUGGESTED_COOKIE_KEYS = {
    "msToken",
    "ttwid",
    "odin_tt",
    "passport_csrf_token",
    "sid_guard",
    "sessionid",
    "sessionid_ss",
    "sid_tt",
    "__ac_nonce",
    "__ac_signature",
    "UIFID",
    "UIFID_TEMP",
    "d_ticket",
    "x-web-secsdk-uid",
    "__security_server_data_status",
    "s_v_web_id",
}
SUGGESTED_COOKIE_PREFIXES = (
    "__security_mc_",
    "bd_ticket_guard_",
    "_bd_ticket_crypt_",
)


@dataclass
class CookieLoginSyncResult:
    success: bool
    status: str
    message: str
    cookie_count: int = 0
    missing_keys: list[str] = field(default_factory=list)
    config_path: str = ""
    user_data_path: str = ""

    def to_message(self) -> str:
        lines = [self.message]
        if self.cookie_count:
            lines.append(f"同步 Cookie 字段数：{self.cookie_count}")
        if self.missing_keys:
            lines.append(f"仍缺少字段：{', '.join(self.missing_keys)}")
        if self.user_data_path:
            lines.append(f"浏览器配置目录：{self.user_data_path}")
        if self.config_path:
            lines.append(f"已写入配置：{self.config_path}")
        return "\n".join(lines)


def sync_douyin_cookies_via_browser(
    config_path: str,
    *,
    timeout_seconds: int = 300,
    poll_interval_seconds: float = 2.0,
    page_factory: Callable[..., Any] | None = None,
    sleep_func: Callable[[float], None] = time.sleep,
) -> CookieLoginSyncResult:
    config = ConfigLoader(config_path)
    browser_cfg = config.get("browser_fallback", {}) or {}
    if not isinstance(browser_cfg, dict):
        browser_cfg = {}

    user_data_path = resolve_user_data_path(browser_cfg)
    browser_binary_path = str(browser_cfg.get("browser_binary_path") or "").strip()
    page = None
    try:
        page = (page_factory or create_browser_page)(
            user_data_path=user_data_path,
            browser_binary_path=browser_binary_path,
        )
        page.get(DOUYIN_HOME)
        cookies = wait_for_login_cookies(
            page,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval_seconds,
            sleep_func=sleep_func,
        )
    except TimeoutError:
        return CookieLoginSyncResult(
            success=False,
            status="timeout",
            message="等待登录超时：没有检测到完整登录态 Cookie。请在弹出的抖音浏览器窗口完成登录后重试。",
            user_data_path=user_data_path,
            config_path=str(Path(config_path).resolve()),
        )
    except Exception as exc:
        return CookieLoginSyncResult(
            success=False,
            status="error",
            message=f"浏览器登录同步 Cookie 失败：{exc}",
            user_data_path=user_data_path,
            config_path=str(Path(config_path).resolve()),
        )

    picked = filter_douyin_cookies(cookies)
    missing = required_missing_keys(picked)
    update_config_cookies(config_path, picked)

    if missing:
        return CookieLoginSyncResult(
            success=False,
            status="warning",
            message="已同步 Cookie，但字段不完整，直连下载可能仍不稳定。",
            cookie_count=len(picked),
            missing_keys=missing,
            user_data_path=user_data_path,
            config_path=str(Path(config_path).resolve()),
        )

    return CookieLoginSyncResult(
        success=True,
        status="ok",
        message="浏览器登录 Cookie 已同步完成。",
        cookie_count=len(picked),
        user_data_path=user_data_path,
        config_path=str(Path(config_path).resolve()),
    )


def create_browser_page(*, user_data_path: str, browser_binary_path: str):
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except Exception as exc:
        raise RuntimeError("DrissionPage 未安装，无法打开浏览器登录同步 Cookie") from exc

    options = ChromiumOptions()
    if browser_binary_path:
        try:
            options.set_browser_path(browser_binary_path)
        except Exception:
            pass
    if user_data_path:
        Path(user_data_path).mkdir(parents=True, exist_ok=True)
        options.set_user_data_path(user_data_path)
    options.set_argument("--mute-audio")
    options.set_argument("--disable-blink-features=AutomationControlled")
    return ChromiumPage(options)


def wait_for_login_cookies(
    page: Any,
    *,
    timeout_seconds: int,
    poll_interval_seconds: float,
    sleep_func: Callable[[float], None] = time.sleep,
) -> dict[str, str]:
    deadline = time.monotonic() + max(5, int(timeout_seconds or 300))
    last_cookies: dict[str, str] = {}
    while time.monotonic() < deadline:
        last_cookies = extract_douyin_cookies(page)
        if has_login_cookies(last_cookies):
            return last_cookies
        sleep_func(max(0.2, float(poll_interval_seconds or 2.0)))
    if last_cookies and any(last_cookies.get(key) for key in FOUNDATION_COOKIE_KEYS):
        return last_cookies
    raise TimeoutError("等待登录 Cookie 超时")


def extract_douyin_cookies(page: Any) -> dict[str, str]:
    raw_cookies = None
    for kwargs in (
        {"all_domains": True, "all_info": True},
        {"all_domains": True},
        {},
    ):
        try:
            raw_cookies = page.cookies(**kwargs)
            break
        except TypeError:
            continue

    return cookies_from_browser_payload(raw_cookies)


def cookies_from_browser_payload(raw_cookies: Any) -> dict[str, str]:
    collected: dict[str, str] = {}
    if isinstance(raw_cookies, dict):
        for key, value in raw_cookies.items():
            collected[str(key)] = str(value or "")
        return sanitize_cookies(collected)

    if not isinstance(raw_cookies, list):
        return {}

    for item in raw_cookies:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or item.get("Domain") or "")
        if domain and "douyin.com" not in domain:
            continue
        name = item.get("name") or item.get("Name")
        value = item.get("value") or item.get("Value") or ""
        if name:
            collected[str(name)] = str(value)
    return sanitize_cookies(collected)


def filter_douyin_cookies(cookies: dict[str, str]) -> dict[str, str]:
    cookies = sanitize_cookies(cookies)
    picked: dict[str, str] = {}
    for key, value in cookies.items():
        if key in SUGGESTED_COOKIE_KEYS:
            picked[key] = value
            continue
        if any(key.startswith(prefix) for prefix in SUGGESTED_COOKIE_PREFIXES):
            picked[key] = value
    return picked or cookies


def has_login_cookies(cookies: dict[str, str]) -> bool:
    return any(cookies.get(key) for key in LOGIN_COOKIE_KEYS)


def required_missing_keys(cookies: dict[str, str]) -> list[str]:
    missing = [key for key in FOUNDATION_COOKIE_KEYS if not cookies.get(key)]
    if not has_login_cookies(cookies):
        missing.append("sessionid/sid_guard")
    return missing


def update_config_cookies(config_path: str, cookies: dict[str, str]) -> None:
    config = ConfigLoader(config_path)
    config.update(cookies=sanitize_cookies(cookies))
    config.save(config_path)


def resolve_user_data_path(browser_cfg: dict[str, Any]) -> str:
    configured = str(browser_cfg.get("user_data_path") or "").strip()
    if configured:
        return configured
    project_root = Path(__file__).resolve().parents[1]
    return str(project_root.parent / "runtime" / "downloader_edge_data")
