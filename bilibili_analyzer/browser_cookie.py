from __future__ import annotations

import base64
import json
import os
import shutil
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from dotenv import set_key


BILIBILI_HOME = "https://www.bilibili.com/"
LOGIN_COOKIE_KEYS = ("SESSDATA", "DedeUserID")
IMPORTANT_COOKIE_KEYS = {
    "SESSDATA",
    "bili_jct",
    "DedeUserID",
    "DedeUserID__ckMd5",
    "sid",
    "buvid3",
    "buvid4",
    "buvid_fp",
    "b_nut",
    "b_lsid",
    "_uuid",
}


@dataclass(frozen=True)
class BilibiliCookieSyncResult:
    ok: bool
    message: str
    cookie: str = ""
    source: str = ""
    cookie_count: int = 0
    env_path: str = ""


def auto_refresh_bilibili_cookie(root_dir: Path | None = None) -> BilibiliCookieSyncResult:
    root = root_dir or Path(__file__).resolve().parent.parent
    env_path = root / ".env"

    cookie_result = load_bilibili_cookie_from_browser(root)
    if not cookie_result.ok:
        return BilibiliCookieSyncResult(
            ok=False,
            message=cookie_result.message,
            env_path=str(env_path),
        )

    save_bilibili_cookie_to_env(cookie_result.cookie, env_path)
    os.environ["BILIBILI_COOKIE"] = cookie_result.cookie
    return BilibiliCookieSyncResult(
        ok=True,
        message=f"已从浏览器自动同步 B站 Cookie：{cookie_result.source}",
        cookie=cookie_result.cookie,
        source=cookie_result.source,
        cookie_count=cookie_result.cookie_count,
        env_path=str(env_path),
    )


def load_bilibili_cookie_from_browser(root_dir: Path | None = None) -> BilibiliCookieSyncResult:
    errors: list[str] = []
    for candidate in iter_chromium_cookie_sources():
        try:
            cookies = read_chromium_bilibili_cookies(candidate)
        except Exception as exc:
            errors.append(f"{candidate.label}: {exc}")
            continue
        if has_login_cookie(cookies):
            return BilibiliCookieSyncResult(
                ok=True,
                message="已读取浏览器 Cookie",
                cookie=format_cookie_header(cookies),
                source=candidate.label,
                cookie_count=len(cookies),
            )

    try:
        browser_result = capture_bilibili_cookie_via_browser(root_dir=root_dir)
    except Exception as exc:
        errors.append(f"DrissionPage: {exc}")
        browser_result = BilibiliCookieSyncResult(ok=False, message=str(exc))
    if browser_result.ok:
        return browser_result

    detail = "；".join(errors[:3])
    if detail:
        return BilibiliCookieSyncResult(
            ok=False,
            message=f"未能从本机浏览器读取到已登录的 B站 Cookie。{detail}",
        )
    return BilibiliCookieSyncResult(
        ok=False,
        message="未能从本机浏览器读取到已登录的 B站 Cookie，请先在 Chrome/Edge 登录 B站后重试。",
    )


def save_bilibili_cookie_to_env(cookie: str, env_path: Path) -> None:
    env_path.parent.mkdir(parents=True, exist_ok=True)
    if not env_path.exists():
        env_path.write_text("", encoding="utf-8")
    set_key(str(env_path), "BILIBILI_COOKIE", cookie, quote_mode="always")


@dataclass(frozen=True)
class ChromiumCookieSource:
    label: str
    user_data_dir: Path
    profile_dir: Path
    cookie_db: Path


def iter_chromium_cookie_sources() -> Iterable[ChromiumCookieSource]:
    configured_db = os.getenv("BILIBILI_BROWSER_COOKIE_DB", "").strip()
    if configured_db:
        db_path = Path(configured_db).expanduser()
        yield ChromiumCookieSource(
            label=f"自定义 Cookie 数据库 ({db_path})",
            user_data_dir=db_path.parent.parent,
            profile_dir=db_path.parent,
            cookie_db=db_path,
        )

    configured_profile = os.getenv("BILIBILI_BROWSER_USER_DATA_PATH", "").strip()
    if configured_profile:
        profile_path = Path(configured_profile).expanduser()
        if (profile_path / "Local State").exists():
            yield from _sources_from_user_data_dir("自定义 Chromium", profile_path)
        else:
            yield from _sources_from_profile_dir("自定义 Chromium", profile_path)

    for label, user_data_dir in default_edge_user_data_dirs():
        yield from _sources_from_user_data_dir(label, user_data_dir)


def default_edge_user_data_dirs() -> list[tuple[str, Path]]:
    home = Path.home()
    local_app_data_raw = os.getenv("LOCALAPPDATA") or ""
    candidates: list[tuple[str, Path]] = []

    if os.name == "nt" and local_app_data_raw:
        local_app_data = Path(local_app_data_raw)
        candidates.append(("Microsoft Edge", local_app_data / "Microsoft" / "Edge" / "User Data"))
    elif os.name == "posix" and sys_platform() == "darwin":
        candidates.append(("Microsoft Edge", home / "Library" / "Application Support" / "Microsoft Edge"))
    else:
        candidates.append(("Microsoft Edge", home / ".config" / "microsoft-edge"))
    return candidates


def sys_platform() -> str:
    import sys

    return sys.platform


def _sources_from_user_data_dir(label: str, user_data_dir: Path) -> Iterable[ChromiumCookieSource]:
    if not user_data_dir.exists():
        return

    profile_names = profile_names_from_local_state(user_data_dir / "Local State")
    for profile_name in profile_names:
        profile_dir = user_data_dir / profile_name
        yield from _sources_from_profile_dir(f"{label}/{profile_name}", profile_dir, user_data_dir)


def _sources_from_profile_dir(
    label: str,
    profile_dir: Path,
    user_data_dir: Path | None = None,
) -> Iterable[ChromiumCookieSource]:
    user_data = user_data_dir or profile_dir.parent
    for relative in ("Network/Cookies", "Cookies"):
        cookie_db = profile_dir / relative
        if cookie_db.exists():
            yield ChromiumCookieSource(
                label=label,
                user_data_dir=user_data,
                profile_dir=profile_dir,
                cookie_db=cookie_db,
            )


def profile_names_from_local_state(local_state_path: Path) -> list[str]:
    fallback = ["Default", "Profile 1", "Profile 2", "Profile 3", "Profile 4"]
    if not local_state_path.exists():
        return fallback
    try:
        data = json.loads(local_state_path.read_text(encoding="utf-8"))
        info_cache = ((data.get("profile") or {}).get("info_cache") or {})
        names = list(info_cache.keys())
        if "Default" not in names:
            names.insert(0, "Default")
        return names or fallback
    except Exception:
        return fallback


def read_chromium_bilibili_cookies(source: ChromiumCookieSource) -> dict[str, str]:
    key = chromium_encryption_key(source.user_data_dir / "Local State")
    now = chrome_time_now()
    cookies: dict[str, str] = {}

    with tempfile.TemporaryDirectory(prefix="bilibili_cookie_") as temp_dir:
        snapshot = Path(temp_dir) / "Cookies.sqlite"
        shutil.copy2(source.cookie_db, snapshot)
        with sqlite3.connect(snapshot) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """
                SELECT host_key, name, value, encrypted_value, expires_utc
                FROM cookies
                WHERE host_key LIKE ?
                """,
                ("%bilibili.com",),
            ).fetchall()

    for row in rows:
        expires_utc = int(row["expires_utc"] or 0)
        if expires_utc and expires_utc < now:
            continue
        name = str(row["name"] or "").strip()
        if not valid_cookie_name(name):
            continue
        value = str(row["value"] or "")
        if not value:
            value = decrypt_chromium_cookie(bytes(row["encrypted_value"] or b""), key)
        if value:
            cookies[name] = value
    return prioritize_cookies(cookies)


def chromium_encryption_key(local_state_path: Path) -> bytes | None:
    if not local_state_path.exists():
        return None
    data = json.loads(local_state_path.read_text(encoding="utf-8"))
    encrypted_key = ((data.get("os_crypt") or {}).get("encrypted_key") or "").strip()
    if not encrypted_key:
        return None
    raw = base64.b64decode(encrypted_key)
    if raw.startswith(b"DPAPI"):
        raw = raw[5:]
    return dpapi_decrypt(raw)


def decrypt_chromium_cookie(encrypted_value: bytes, key: bytes | None) -> str:
    if not encrypted_value:
        return ""
    if encrypted_value.startswith((b"v10", b"v11")) and key:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        nonce = encrypted_value[3:15]
        payload = encrypted_value[15:]
        return AESGCM(key).decrypt(nonce, payload, None).decode("utf-8")
    if encrypted_value.startswith(b"v20"):
        return ""
    return dpapi_decrypt(encrypted_value).decode("utf-8", errors="ignore")


def dpapi_decrypt(payload: bytes) -> bytes:
    try:
        import win32crypt

        return win32crypt.CryptUnprotectData(payload, None, None, None, 0)[1]
    except ImportError:
        return dpapi_decrypt_ctypes(payload)


def dpapi_decrypt_ctypes(payload: bytes) -> bytes:
    import ctypes
    from ctypes import wintypes

    class DATA_BLOB(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32

    in_buffer = ctypes.create_string_buffer(payload, len(payload))
    in_blob = DATA_BLOB(len(payload), in_buffer)
    out_blob = DATA_BLOB()
    if not crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    ):
        raise OSError("DPAPI 解密失败")
    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def capture_bilibili_cookie_via_browser(root_dir: Path | None = None) -> BilibiliCookieSyncResult:
    try:
        from DrissionPage import ChromiumOptions, ChromiumPage
    except Exception as exc:
        raise RuntimeError("DrissionPage 未安装，无法打开浏览器同步 B站 Cookie") from exc

    root = root_dir or Path(__file__).resolve().parent.parent
    user_data_path = Path(
        os.getenv("BILIBILI_AUTO_BROWSER_PROFILE")
        or root / "runtime" / "bilibili_browser_profile"
    )
    timeout_seconds = int(os.getenv("BILIBILI_AUTO_COOKIE_TIMEOUT", "90"))
    poll_interval = float(os.getenv("BILIBILI_AUTO_COOKIE_POLL_INTERVAL", "2"))
    browser_binary_path = os.getenv("BILIBILI_BROWSER_BINARY_PATH", "").strip()
    if not browser_binary_path:
        browser_binary_path = find_edge_binary()

    options = ChromiumOptions()
    if browser_binary_path:
        try:
            options.set_browser_path(browser_binary_path)
        except Exception:
            pass
    user_data_path.mkdir(parents=True, exist_ok=True)
    options.set_user_data_path(str(user_data_path))
    options.set_argument("--mute-audio")
    options.set_argument("--disable-blink-features=AutomationControlled")

    page = None
    try:
        page = ChromiumPage(options)
        page.get(BILIBILI_HOME)
        cookies = wait_for_bilibili_login_cookies(
            page,
            timeout_seconds=timeout_seconds,
            poll_interval_seconds=poll_interval,
        )
        return BilibiliCookieSyncResult(
            ok=True,
            message="已通过浏览器窗口同步 B站 Cookie",
            cookie=format_cookie_header(cookies),
            source=f"浏览器窗口 ({user_data_path})",
            cookie_count=len(cookies),
        )
    finally:
        close_browser_page(page)


def find_edge_binary() -> str:
    if os.name == "nt":
        candidates = [
            Path(os.getenv("PROGRAMFILES(X86)") or "") / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.getenv("PROGRAMFILES") or "") / "Microsoft" / "Edge" / "Application" / "msedge.exe",
            Path(os.getenv("LOCALAPPDATA") or "") / "Microsoft" / "Edge" / "Application" / "msedge.exe",
        ]
    elif sys_platform() == "darwin":
        candidates = [Path("/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge")]
    else:
        candidates = [Path("/usr/bin/microsoft-edge"), Path("/usr/bin/microsoft-edge-stable")]
    for candidate in candidates:
        if str(candidate) and candidate.exists():
            return str(candidate)
    return ""


def close_browser_page(page: Any) -> None:
    if page is None:
        return
    for method_name in ("quit", "close"):
        method = getattr(page, method_name, None)
        if not callable(method):
            continue
        try:
            method()
            return
        except Exception:
            continue


def wait_for_bilibili_login_cookies(
    page: Any,
    *,
    timeout_seconds: int,
    poll_interval_seconds: float,
) -> dict[str, str]:
    deadline = time.monotonic() + max(5, int(timeout_seconds or 90))
    last_cookies: dict[str, str] = {}
    while time.monotonic() < deadline:
        last_cookies = cookies_from_browser_payload(read_page_cookies(page))
        if has_login_cookie(last_cookies):
            return prioritize_cookies(last_cookies)
        time.sleep(max(0.2, float(poll_interval_seconds or 2.0)))
    raise TimeoutError("等待 B站登录 Cookie 超时")


def read_page_cookies(page: Any) -> Any:
    for kwargs in (
        {"all_domains": True, "all_info": True},
        {"all_domains": True},
        {},
    ):
        try:
            return page.cookies(**kwargs)
        except TypeError:
            continue
    return None


def cookies_from_browser_payload(raw_cookies: Any) -> dict[str, str]:
    collected: dict[str, str] = {}
    if isinstance(raw_cookies, dict):
        for name, value in raw_cookies.items():
            if valid_cookie_name(str(name)):
                collected[str(name)] = str(value or "")
        return prioritize_cookies(collected)

    if not isinstance(raw_cookies, list):
        return {}

    for item in raw_cookies:
        if not isinstance(item, dict):
            continue
        domain = str(item.get("domain") or item.get("Domain") or "")
        if domain and "bilibili.com" not in domain:
            continue
        name = str(item.get("name") or item.get("Name") or "").strip()
        value = str(item.get("value") or item.get("Value") or "")
        if valid_cookie_name(name) and value:
            collected[name] = value
    return prioritize_cookies(collected)


def has_login_cookie(cookies: dict[str, str]) -> bool:
    return all(cookies.get(key) for key in LOGIN_COOKIE_KEYS)


def prioritize_cookies(cookies: dict[str, str]) -> dict[str, str]:
    cleaned = {name: value for name, value in cookies.items() if valid_cookie_name(name) and value}
    ordered: dict[str, str] = {}
    for key in IMPORTANT_COOKIE_KEYS:
        if key in cleaned:
            ordered[key] = cleaned.pop(key)
    for key in sorted(cleaned):
        ordered[key] = cleaned[key]
    return ordered


def format_cookie_header(cookies: dict[str, str]) -> str:
    return "; ".join(f"{name}={value}" for name, value in cookies.items() if valid_cookie_name(name))


def valid_cookie_name(name: str) -> bool:
    return bool(name) and "=" not in name and ";" not in name and "\n" not in name and "\r" not in name


def chrome_time_now() -> int:
    return int((time.time() + 11644473600) * 1_000_000)
