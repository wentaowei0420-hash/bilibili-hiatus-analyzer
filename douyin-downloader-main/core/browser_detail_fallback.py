from __future__ import annotations

import atexit
import json
import threading
import time
from pathlib import Path
from typing import Any, Dict, Optional

from utils.logger import setup_logger

logger = setup_logger("BrowserDetailFallback")


class BrowserVideoUnavailableError(RuntimeError):
    """Raised when the browser page confirms that the target video is gone."""


class BrowserDetailFallback:
    def __init__(self):
        self._page = None
        self._profile_key: tuple[str, str] | None = None
        self._lock = threading.Lock()

    def fetch(
        self,
        aweme_id: str,
        *,
        user_data_path: str = "",
        browser_binary_path: str = "",
        timeout_seconds: int = 25,
        page_load_delay: float = 2.5,
    ) -> Optional[Dict[str, Any]]:
        aweme_id = str(aweme_id or "").strip()
        if not aweme_id:
            return None

        with self._lock:
            try:
                page = self._ensure_page(
                    user_data_path=user_data_path,
                    browser_binary_path=browser_binary_path,
                )
                return self._fetch_with_page(
                    page,
                    aweme_id,
                    timeout_seconds=max(5, int(timeout_seconds or 25)),
                    page_load_delay=max(0.0, float(page_load_delay or 0)),
                )
            except BrowserVideoUnavailableError:
                logger.warning("Browser detail fallback confirmed video unavailable: %s", aweme_id)
                raise
            except Exception as exc:
                logger.warning("Browser detail fallback failed for %s: %s", aweme_id, exc)
                return None

    def close(self) -> None:
        with self._lock:
            self._close_page_unlocked()

    def _ensure_page(self, *, user_data_path: str, browser_binary_path: str):
        profile_key = (str(user_data_path or ""), str(browser_binary_path or ""))
        if self._page is not None and self._profile_key == profile_key:
            return self._page

        self._close_page_unlocked()
        try:
            from DrissionPage import ChromiumOptions, ChromiumPage
        except Exception as exc:
            raise RuntimeError("DrissionPage is not installed") from exc

        co = ChromiumOptions()
        if browser_binary_path:
            try:
                co.set_browser_path(str(browser_binary_path))
            except Exception:
                logger.debug("Ignoring invalid browser_binary_path=%s", browser_binary_path)
        co.set_argument("--mute-audio")
        co.set_argument("--disable-blink-features=AutomationControlled")
        if user_data_path:
            Path(user_data_path).mkdir(parents=True, exist_ok=True)
            co.set_user_data_path(str(user_data_path))

        self._page = ChromiumPage(co)
        self._profile_key = profile_key
        return self._page

    def _close_page_unlocked(self) -> None:
        if self._page is None:
            return
        try:
            self._page.quit()
        except Exception:
            pass
        self._page = None

    def _fetch_with_page(
        self,
        page,
        aweme_id: str,
        *,
        timeout_seconds: int,
        page_load_delay: float,
    ) -> Optional[Dict[str, Any]]:
        patterns = ["aweme/v1/web/aweme/detail/", "aweme/v1/web/aweme/post/"]
        try:
            page.listen.start(patterns)
            page.get(f"https://www.douyin.com/video/{aweme_id}")
            if page_load_delay:
                time.sleep(page_load_delay)
            if self._page_indicates_video_unavailable(page):
                raise BrowserVideoUnavailableError(
                    f"Video unavailable in browser page: {aweme_id}"
                )
            packets = self._drain_packets(page, timeout=timeout_seconds)
            for packet in packets:
                detail = self._extract_matching_detail(packet, aweme_id)
                if detail:
                    return detail
            if self._page_indicates_video_unavailable(page):
                raise BrowserVideoUnavailableError(
                    f"Video unavailable in browser page: {aweme_id}"
                )
        finally:
            try:
                page.listen.stop()
            except Exception:
                pass
        return None

    @staticmethod
    def _page_indicates_video_unavailable(page) -> bool:
        markers = (
            "你要观看的视频不存在",
            "视频不存在",
            "作品不存在",
            "该视频不存在",
            "视频已删除",
            "作品已删除",
        )
        text_parts = []
        for attr in ("title", "html"):
            try:
                value = getattr(page, attr)
                if callable(value):
                    value = value()
                if value:
                    text_parts.append(str(value))
            except Exception:
                continue
        text = "\n".join(text_parts)
        return any(marker in text for marker in markers)

    @staticmethod
    def _drain_packets(page, *, timeout: int) -> list[Any]:
        packets: list[Any] = []
        for step in page.listen.steps(timeout=timeout, gap=1):
            if step is False:
                break
            if isinstance(step, list):
                packets.extend(item for item in step if item)
            elif step:
                packets.append(step)
        return packets

    @classmethod
    def _extract_matching_detail(cls, packet: Any, aweme_id: str) -> Optional[Dict[str, Any]]:
        data = cls._extract_packet_body(packet)
        for detail in cls._extract_awemes_from_body(data):
            if str(detail.get("aweme_id") or "") == str(aweme_id):
                return detail
        return None

    @staticmethod
    def _extract_packet_body(packet: Any) -> Dict[str, Any]:
        try:
            body = packet.response.body
        except Exception:
            return {}
        if isinstance(body, dict):
            return body
        if isinstance(body, str):
            try:
                data = json.loads(body)
                return data if isinstance(data, dict) else {}
            except Exception:
                return {}
        return {}

    @classmethod
    def _extract_awemes_from_body(cls, data: Any) -> list[Dict[str, Any]]:
        if not isinstance(data, dict):
            return []
        aweme_list = data.get("aweme_list")
        if isinstance(aweme_list, list):
            return [item for item in aweme_list if isinstance(item, dict)]

        for key in ("aweme_detail", "detail", "item", "aweme"):
            candidate = data.get(key)
            if isinstance(candidate, dict) and candidate.get("aweme_id"):
                return [candidate]

        inner_data = data.get("data")
        if isinstance(inner_data, dict):
            return cls._extract_awemes_from_body(inner_data)
        return []


_SHARED_FALLBACK = BrowserDetailFallback()
atexit.register(_SHARED_FALLBACK.close)


def get_shared_browser_detail_fallback() -> BrowserDetailFallback:
    return _SHARED_FALLBACK
