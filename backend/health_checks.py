from __future__ import annotations

from typing import Any

import requests


def check_bilibili_cookie_status() -> dict[str, Any]:
    from bilibili_analyzer.config import load_analyzer_config

    config = load_analyzer_config()
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
