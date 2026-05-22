import asyncio
from pathlib import Path
from typing import Any, Dict, Optional

from core.browser_detail_fallback import get_shared_browser_detail_fallback
from core.downloader_base import BaseDownloader, DownloadResult
from utils.logger import setup_logger

logger = setup_logger('VideoDownloader')


class VideoDownloader(BaseDownloader):
    async def download(self, parsed_url: Dict[str, Any]) -> DownloadResult:
        result = DownloadResult()

        aweme_id = parsed_url.get('aweme_id')
        if not aweme_id:
            logger.error("No aweme_id found in parsed URL")
            return result

        result.total = 1
        self._progress_set_item_total(1, "单视频下载")
        self._progress_update_step("下载作品", "单视频资源下载中")

        if not await self._should_download(aweme_id):
            logger.info("Video %s already downloaded, skipping", aweme_id)
            result.skipped += 1
            self._progress_advance_item("skipped", str(aweme_id))
            return result

        await self.rate_limiter.acquire()

        aweme_data = await self._fetch_aweme_data(aweme_id)
        if not aweme_data:
            logger.error("Failed to get video detail: %s", aweme_id)
            result.failed += 1
            self._progress_advance_item("failed", str(aweme_id))
            return result

        success = await self._download_aweme(aweme_data)
        if success:
            result.success += 1
            self._progress_advance_item("success", str(aweme_id))
        else:
            result.failed += 1
            self._progress_advance_item("failed", str(aweme_id))

        return result

    async def _download_aweme(self, aweme_data: Dict[str, Any]) -> bool:
        author = aweme_data.get('author', {})
        author_name = author.get('nickname', 'unknown')
        return await self._download_aweme_assets(aweme_data, author_name)

    async def _fetch_aweme_data(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        aweme_data = await self.api_client.get_video_detail(aweme_id)
        if aweme_data:
            return aweme_data

        if not self._browser_fallback_enabled():
            return None

        logger.warning(
            "Direct detail API failed for %s (%s), trying browser fallback",
            aweme_id,
            getattr(self.api_client, "last_error", "") or "unknown error",
        )
        self._progress_update_step("浏览器兜底", f"打开视频页补取详情：{aweme_id}")
        return await asyncio.to_thread(self._fetch_aweme_data_via_browser, aweme_id)

    def _browser_fallback_enabled(self) -> bool:
        browser_cfg = self.config.get("browser_fallback", {}) or {}
        if not isinstance(browser_cfg, dict):
            return False
        value = browser_cfg.get("enabled", True)
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on"}
        return bool(value)

    def _fetch_aweme_data_via_browser(self, aweme_id: str) -> Optional[Dict[str, Any]]:
        browser_cfg = self.config.get("browser_fallback", {}) or {}
        if not isinstance(browser_cfg, dict):
            browser_cfg = {}
        fallback = get_shared_browser_detail_fallback()
        return fallback.fetch(
            aweme_id,
            user_data_path=self._browser_user_data_path(browser_cfg),
            browser_binary_path=str(browser_cfg.get("browser_binary_path") or ""),
            timeout_seconds=int(browser_cfg.get("detail_timeout_seconds", 25) or 25),
            page_load_delay=float(browser_cfg.get("detail_page_load_delay", 2.5) or 0),
        )

    @staticmethod
    def _browser_user_data_path(browser_cfg: Dict[str, Any]) -> str:
        configured = str(browser_cfg.get("user_data_path") or "").strip()
        if configured:
            return configured

        # Keep the downloader fallback browser profile separate from the main
        # analyzer. FULL mode may already own runtime/edge_data, and opening the
        # same Chromium profile twice can make DrissionPage miss detail packets.
        workspace_root = Path(__file__).resolve().parents[2]
        return str(workspace_root / "runtime" / "downloader_edge_data")
