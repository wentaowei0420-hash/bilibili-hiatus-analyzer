from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import unquote

from config import ConfigLoader
from core import DouyinAPIClient


STATUS_ORDER = {"ok": 0, "warning": 1, "error": 2}
STATUS_LABELS = {
    "ok": "正常",
    "warning": "注意",
    "error": "异常",
}

FOUNDATION_COOKIE_KEYS = ("ttwid", "odin_tt", "passport_csrf_token")
LOGIN_COOKIE_KEYS = ("sessionid", "sessionid_ss", "sid_guard", "sid_tt")


@dataclass
class CookieCheckResult:
    status: str = "ok"
    summary: str = "Cookie 检测完成"
    diagnostics: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    sample_aweme_id: str = ""

    @property
    def status_label(self) -> str:
        return STATUS_LABELS.get(self.status, self.status)

    def add(self, status: str, message: str, recommendation: str = "") -> None:
        if STATUS_ORDER.get(status, 0) > STATUS_ORDER.get(self.status, 0):
            self.status = status
        self.diagnostics.append(message)
        if recommendation and recommendation not in self.recommendations:
            self.recommendations.append(recommendation)

    def to_message(self) -> str:
        lines = [f"检测结果：{self.status_label}", self.summary]
        if self.sample_aweme_id:
            lines.append(f"测试视频：{self.sample_aweme_id}")
        if self.diagnostics:
            lines.append("")
            lines.append("诊断明细：")
            lines.extend(f"- {item}" for item in self.diagnostics)
        if self.recommendations:
            lines.append("")
            lines.append("建议处理：")
            lines.extend(f"- {item}" for item in self.recommendations)
        return "\n".join(lines)


async def check_douyin_cookie_status(
    config: ConfigLoader,
    *,
    sample_aweme_id: str = "",
) -> CookieCheckResult:
    result = CookieCheckResult()
    cookies = config.get_cookies()

    if not cookies:
        result.summary = "未读取到任何 Cookie"
        result.add(
            "error",
            "config.yml 中没有可用 cookies，下载详情接口大概率会失败。",
            "重新登录抖音后同步 Cookie 到 config.yml。",
        )
        return result

    result.add("ok", f"已读取 Cookie：{len(cookies)} 个字段。")
    _check_cookie_shape(cookies, result)
    _check_sid_guard_expiration(cookies.get("sid_guard"), result)

    sample_aweme_id = sample_aweme_id or first_aweme_id_from_config(config)
    result.sample_aweme_id = sample_aweme_id
    if not sample_aweme_id:
        result.add(
            "warning",
            "没有找到可用于详情接口检测的视频 ID，只完成了本地 Cookie 字段检查。",
            "刷新统计后再执行 Cookie 检测，或在 config.yml 的 link 中保留一个抖音视频链接。",
        )
        _finalize_summary(result)
        return result

    await _probe_detail_api(config, cookies, sample_aweme_id, result)
    _finalize_summary(result)
    return result


def first_aweme_id_from_config(config: ConfigLoader) -> str:
    for link in config.get_links():
        aweme_id = extract_aweme_id(link)
        if aweme_id:
            return aweme_id
    return ""


def extract_aweme_id(value: Any) -> str:
    text = str(value or "")
    match = re.search(r"(?<!\d)(\d{15,20})(?!\d)", text)
    return match.group(1) if match else ""


def _check_cookie_shape(cookies: dict[str, str], result: CookieCheckResult) -> None:
    missing_foundation = [key for key in FOUNDATION_COOKIE_KEYS if not cookies.get(key)]
    if missing_foundation:
        result.add(
            "warning",
            f"缺少基础 Cookie 字段：{', '.join(missing_foundation)}。",
            "重新从已登录浏览器复制完整 Cookie，避免只复制了部分字段。",
        )
    else:
        result.add("ok", "基础 Cookie 字段完整。")

    has_login_cookie = any(cookies.get(key) for key in LOGIN_COOKIE_KEYS)
    if not has_login_cookie:
        result.add(
            "warning",
            "未发现 sessionid/sid_guard 等登录态 Cookie，可能是未登录或登录 Cookie 未同步。",
            "在浏览器保持抖音登录后重新同步 Cookie。",
        )
    else:
        result.add("ok", "检测到登录态 Cookie 字段。")

    if not cookies.get("msToken"):
        result.add(
            "warning",
            "缺少 msToken，程序会尝试自动生成，但直连接口稳定性可能下降。",
            "如果持续 Empty response，可重新同步浏览器 Cookie 或启用浏览器兜底。",
        )


def _check_sid_guard_expiration(value: Any, result: CookieCheckResult) -> None:
    expires_at = _parse_sid_guard_expires_at(value)
    if not value:
        return
    if not expires_at:
        result.add(
            "warning",
            "sid_guard 存在但无法解析过期时间。",
            "如果下载异常，建议重新同步 Cookie。",
        )
        return

    now = datetime.now(timezone.utc)
    if expires_at <= now:
        result.add(
            "error",
            f"sid_guard 已过期：{expires_at.astimezone().strftime('%Y-%m-%d %H:%M:%S')}。",
            "重新登录抖音并同步 Cookie。",
        )
        return

    remaining_days = (expires_at - now).days
    if remaining_days <= 7:
        result.add(
            "warning",
            f"sid_guard 即将过期，剩余约 {remaining_days} 天。",
            "建议提前刷新 Cookie，避免批量下载中断。",
        )
    else:
        result.add("ok", f"sid_guard 未过期，剩余约 {remaining_days} 天。")


def _parse_sid_guard_expires_at(value: Any) -> datetime | None:
    text = unquote(str(value or ""))
    parts = text.split("|")
    for part in reversed(parts):
        part = part.strip()
        if "," not in part or "GMT" not in part:
            continue
        try:
            parsed = parsedate_to_datetime(part)
        except (TypeError, ValueError):
            continue
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    return None


async def _probe_detail_api(
    config: ConfigLoader,
    cookies: dict[str, str],
    aweme_id: str,
    result: CookieCheckResult,
) -> None:
    async with DouyinAPIClient(cookies, proxy=config.get("proxy")) as api_client:
        detail = await api_client.get_video_detail(aweme_id, suppress_error=True)
        if detail:
            result.add("ok", "抖音详情接口可正常返回视频详情，当前 Cookie 可用于直连下载。")
            return

        error = api_client.last_error or "未返回详情"
        lowered = error.lower()
        if "empty response body" in lowered:
            result.add(
                "error",
                f"详情接口返回空响应：{error}。这通常表示 Cookie/登录态异常、签名被拦截或账号/环境触发风控。",
                "刷新 Cookie；如果仍失败，开启“启用浏览器兜底”让下载器用浏览器登录态补取详情。",
            )
        elif "http 401" in lowered or "http 403" in lowered:
            result.add(
                "error",
                f"详情接口被拒绝：{error}。",
                "重新登录抖音并同步 Cookie，确认账号没有掉线或被要求验证。",
            )
        elif "http 429" in lowered:
            result.add(
                "error",
                f"详情接口触发频率限制：{error}。",
                "暂停一段时间，降低下载频率，必要时更换网络环境。",
            )
        else:
            result.add(
                "warning",
                f"详情接口未能返回视频详情：{error}。",
                "先刷新 Cookie；若仍失败，开启浏览器兜底或下载前抽样检测。",
            )


def _finalize_summary(result: CookieCheckResult) -> None:
    if result.status == "ok":
        result.summary = "Cookie 本地字段和详情接口检测均通过。"
    elif result.status == "warning":
        result.summary = "Cookie 可读取，但存在可能影响稳定性的风险。"
    else:
        result.summary = "Cookie 或抖音接口状态异常，当前直连下载可能失败。"
