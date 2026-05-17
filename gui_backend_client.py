from __future__ import annotations

import os
import time
from typing import Any, Optional
from urllib.parse import quote, urlencode

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from gui_models import RunConfig


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}


class BackendApiError(RuntimeError):
    pass


class BackendApiClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 20.0) -> None:
        self.base_url = (base_url or os.getenv("HIATUS_GUI_API_URL") or DEFAULT_API_BASE_URL).rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            raise BackendApiError(f"Backend API request failed: {url} ({exc})") from exc
        if not response.content:
            return None
        return response.json()

    def health(self) -> dict[str, Any]:
        return self.request("GET", "/api/health")

    def create_job(self, payload: dict[str, Any]) -> dict[str, Any]:
        return self.request("POST", "/api/jobs", json=payload)

    def get_job(self, job_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/jobs/{job_id}")

    def read_events(self, job_id: str, offset: int) -> dict[str, Any]:
        return self.request("GET", f"/api/jobs/{job_id}/events?offset={offset}")

    def cancel_job(self, job_id: str) -> dict[str, Any]:
        return self.request("POST", f"/api/jobs/{job_id}/cancel")

    def check_bilibili_cookie(self) -> dict[str, Any]:
        return self.request("GET", "/api/bilibili/cookie-status")

    def config_defaults(self) -> dict[str, Any]:
        return self.request("GET", "/api/config/defaults")

    def douyin_stats(self, high_like_threshold: int) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/douyin/stats?{urlencode({'high_like_threshold': int(high_like_threshold)})}",
        )

    def rating_overview(self, search_uid: str = "") -> dict[str, Any]:
        return self.request("GET", f"/api/douyin/rating-overview?{urlencode({'search_uid': search_uid})}")

    def creator_detail(self, uploader_id: str) -> dict[str, Any]:
        return self.request("GET", f"/api/douyin/creator-detail/{quote(uploader_id, safe='')}")

    def save_creator_manual_grade(self, uploader_id: str, grade: str, note: str) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/douyin/creator-manual-grade",
            json={"uploader_id": uploader_id, "grade": grade, "note": note},
        )

    def status_reset_candidates(self, threshold: int) -> dict[str, Any]:
        return self.request("GET", f"/api/douyin/status-reset?{urlencode({'threshold': int(threshold)})}")

    def reset_full_status(self, uids: list[str]) -> dict[str, Any]:
        return self.request("POST", "/api/douyin/status-reset", json={"uids": uids})

    def archive_state(self, threshold: int) -> dict[str, Any]:
        return self.request("GET", f"/api/douyin/archive?{urlencode({'threshold': int(threshold)})}")

    def archive_douyin_creators(
        self,
        *,
        uids: list[str] | None = None,
        threshold: int = 100,
        all_candidates: bool = False,
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/douyin/archive",
            json={"uids": uids or [], "threshold": threshold, "all": all_candidates},
        )

    def restore_archived_creators(self, uids: list[str]) -> dict[str, Any]:
        return self.request("POST", "/api/douyin/archive/restore", json={"uids": uids})


def payload_from_config(config: RunConfig) -> dict[str, Any]:
    kind_by_platform = {
        "bilibili": "bilibili_analysis",
        "douyin": "douyin_analysis",
        "both": "both_analysis",
        "douyin_unfollow": "douyin_unfollow",
        "douyin_non_followed_cleanup": "douyin_prune_non_followed_cache",
        "bilibili_uid": "bilibili_uid_fetch",
        "douyin_uid": "douyin_uid_fetch",
        "douyin_high_like": "douyin_high_like_export",
        "douyin_video_score": "douyin_video_score",
        "douyin_creator_score": "douyin_creator_score",
        "douyin_compact_export": "douyin_compact_export",
    }
    kind = kind_by_platform.get(config.platform)
    if not kind:
        raise BackendApiError(f"Unknown GUI platform mode: {config.platform}")

    return {
        "kind": kind,
        "action": config.action,
        "bilibili_mode": config.bilibili_mode,
        "douyin_fetch_mode": config.douyin_fetch_mode,
        "douyin_backend": config.douyin_backend,
        "monitor_video_limit": config.monitor_video_limit,
        "uid_limit": config.uid_limit if config.uid_limit_enabled else None,
        "high_like_threshold": config.high_like_threshold,
        "unfollow_list_path": str(config.unfollow_list_path),
        "bilibili_uid_list_path": str(config.bilibili_uid_list_path),
        "douyin_uid_list_path": str(config.douyin_uid_list_path),
        "bilibili_runtime_settings": {"values": dict(config.bilibili_runtime_settings or {})},
        "douyin_runtime_settings": {"values": dict(config.douyin_runtime_settings or {})},
        "fetch_order_settings": dict(config.fetch_order_settings or {}),
    }


def rating_refresh_payload() -> dict[str, Any]:
    return {"kind": "douyin_rating_refresh", "action": "fetch"}


def data_sync_payload() -> dict[str, Any]:
    return {"kind": "douyin_data_sync", "action": "fetch"}


def liked_video_cache_payload() -> dict[str, Any]:
    return {"kind": "douyin_liked_video_cache", "action": "fetch"}


class BackendJobThread(QThread):
    log_line = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(
        self,
        *,
        config: Optional[RunConfig] = None,
        payload: Optional[dict[str, Any]] = None,
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.config = config
        self.payload = payload
        self._cancel_requested = False
        self._cancel_sent = False
        self._job_id: Optional[str] = None

    def request_cancel(self) -> None:
        self._cancel_requested = True

    def run(self) -> None:
        client = BackendApiClient()
        offset = 0
        try:
            payload = self.payload or payload_from_config(self.config)
            job = client.create_job(payload)
            self._job_id = job["id"]
            self.log_line.emit(f"Backend job created: {self._job_id}")

            while True:
                if self._cancel_requested and not self._cancel_sent:
                    client.cancel_job(self._job_id)
                    self._cancel_sent = True
                    self.log_line.emit("Cancel requested through backend API.")

                event_data = client.read_events(self._job_id, offset)
                offset = int(event_data.get("next_offset", offset))
                for line in event_data.get("lines", []):
                    self.log_line.emit(str(line))

                job = client.get_job(self._job_id)
                status = str(job.get("status") or "")
                if status in TERMINAL_STATUSES:
                    event_data = client.read_events(self._job_id, offset)
                    for line in event_data.get("lines", []):
                        self.log_line.emit(str(line))
                    message = _job_done_message(job)
                    self.done.emit(status in {"succeeded", "cancelled"}, message)
                    return

                time.sleep(1.0)
        except Exception as exc:
            self.done.emit(False, str(exc))


class RunnerThread(BackendJobThread):
    def __init__(self, config: RunConfig):
        super().__init__(config=config)


class RatingRefreshThread(BackendJobThread):
    def __init__(self, parent=None):
        super().__init__(payload=rating_refresh_payload(), parent=parent)


class DouyinDataSyncThread(BackendJobThread):
    def __init__(self, parent=None):
        super().__init__(payload=data_sync_payload(), parent=parent)


class DouyinLikedVideoCacheThread(BackendJobThread):
    def __init__(self, parent=None):
        super().__init__(payload=liked_video_cache_payload(), parent=parent)


class BilibiliCookieCheckThread(QThread):
    checked = pyqtSignal(bool, str)

    def run(self) -> None:
        try:
            result = BackendApiClient().check_bilibili_cookie()
            self.checked.emit(bool(result.get("ok")), str(result.get("message") or ""))
        except Exception as exc:
            self.checked.emit(False, f"检测失败：{exc}")


def _job_done_message(job: dict[str, Any]) -> str:
    status = str(job.get("status") or "")
    message = str(job.get("message") or "")
    error = job.get("error")
    result = job.get("result")
    if status == "failed":
        return str(error or message or "任务执行失败")
    if status == "cancelled":
        return message or "已终止运行，后端已保存当前可用数据"
    if isinstance(result, dict) and result.get("message"):
        return str(result["message"])
    return message or "任务执行完成"
