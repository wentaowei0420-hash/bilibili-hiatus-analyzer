from __future__ import annotations

from dataclasses import replace
from typing import Any

import requests


def check_bilibili_cookie_status() -> dict[str, Any]:
    from bilibili_analyzer.config import load_analyzer_config

    config = load_analyzer_config()
    current = _check_cookie_with_config(config)
    if current["ok"]:
        return current

    try:
        from bilibili_analyzer.browser_cookie import auto_refresh_bilibili_cookie
    except Exception as exc:
        return {
            "ok": False,
            "message": f"{current['message']}；Edge 自动获取模块加载失败：{exc}",
        }

    sync_result = auto_refresh_bilibili_cookie(config.root_dir)
    if not sync_result.ok:
        return {
            "ok": False,
            "message": f"{current['message']}；Edge 自动获取失败：{sync_result.message}",
        }

    refreshed_config = replace(config, cookie=sync_result.cookie)
    refreshed = _check_cookie_with_config(refreshed_config)
    if refreshed["ok"]:
        refreshed["message"] = (
            f"{refreshed['message']}；已从 Edge 自动更新 Cookie"
        )
        return refreshed

    return {
        "ok": False,
        "message": (
            f"已从 Edge 读取 Cookie 并写入 .env，但复检仍未登录："
            f"{refreshed['message']}"
        ),
    }


def _check_cookie_with_config(config: Any) -> dict[str, Any]:
    if not (config.cookie or "").strip():
        return {"ok": False, "message": "未配置 BILIBILI_COOKIE"}

    try:
        response = requests.get(config.nav_api, headers=config.headers, timeout=20)
        response.raise_for_status()
        payload = response.json() if response.content else {}
    except Exception as exc:
        return {"ok": False, "message": f"检测失败：{exc}"}

    if payload.get("code") == 0:
        user = payload.get("data", {}) or {}
        uname = user.get("uname") or "未知用户"
        mid = user.get("mid") or ""
        return {"ok": True, "message": f"已登录：{uname} (mid={mid})"}

    message = payload.get("message") or payload.get("msg") or "账号未登录"
    return {"ok": False, "message": f"未登录：{message}"}
