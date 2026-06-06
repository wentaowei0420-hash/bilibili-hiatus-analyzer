from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import quote, urlencode, urlparse

import requests
from PyQt5.QtCore import QThread, pyqtSignal

from backend.revision import BACKEND_SERVICE, backend_revision
from gui_models import RunConfig


DEFAULT_API_BASE_URL = "http://127.0.0.1:8000"
MIN_BACKEND_API_VERSION = 9
TERMINAL_STATUSES = {"succeeded", "failed", "cancelled"}
ROOT_DIR = Path(__file__).resolve().parent
BACKEND_AUTOSTART_LOG = ROOT_DIR / "runtime" / "logs" / "backend_gui_autostart.log"
_BACKEND_PROCESS: subprocess.Popen | None = None
_ACTIVE_API_BASE_URL: str | None = None


class BackendApiError(RuntimeError):
    pass


def ensure_backend_available(timeout: float = 12.0) -> None:
    client = BackendApiClient(timeout=1.0)
    if _backend_is_compatible(client):
        return

    target_url = client.base_url
    try:
        client.health()
        target_url = _local_base_url_with_free_port(target_url)
    except BackendApiError:
        pass

    client = BackendApiClient(base_url=target_url, timeout=1.0)
    _start_local_backend(client.base_url)
    deadline = time.time() + timeout
    last_error: Exception | None = None
    while time.time() < deadline:
        if _backend_is_compatible(client):
            return
        if _BACKEND_PROCESS is not None and _BACKEND_PROCESS.poll() is not None:
            break
        time.sleep(0.4)

    log_hint = f"后端自启动日志：{BACKEND_AUTOSTART_LOG}"
    if _BACKEND_PROCESS is not None and _BACKEND_PROCESS.poll() is not None:
        raise BackendApiError(f"后端进程启动后退出，无法连接 API。{log_hint}") from last_error
    raise BackendApiError(f"后端未在 {timeout:.0f} 秒内就绪。{log_hint}") from last_error


def _start_local_backend(base_url: str) -> None:
    global _ACTIVE_API_BASE_URL
    global _BACKEND_PROCESS
    if _BACKEND_PROCESS is not None and _BACKEND_PROCESS.poll() is None:
        _ACTIVE_API_BASE_URL = base_url
        return

    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise BackendApiError(f"当前 API 地址不是本机地址，GUI 不会自动启动远端后端：{base_url}")

    port = parsed.port or (443 if parsed.scheme == "https" else 8000)
    env = os.environ.copy()
    env["HIATUS_API_HOST"] = host if host != "::1" else "127.0.0.1"
    env["HIATUS_API_PORT"] = str(port)

    BACKEND_AUTOSTART_LOG.parent.mkdir(parents=True, exist_ok=True)
    log_file = BACKEND_AUTOSTART_LOG.open("a", encoding="utf-8")
    log_file.write(f"\n[{time.strftime('%Y-%m-%d %H:%M:%S')}] GUI autostart backend on {host}:{port}\n")
    log_file.flush()

    creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    _BACKEND_PROCESS = subprocess.Popen(
        [sys.executable, "-m", "backend"],
        cwd=str(ROOT_DIR),
        stdin=subprocess.DEVNULL,
        stdout=log_file,
        stderr=subprocess.STDOUT,
        env=env,
        creationflags=creationflags,
    )
    _ACTIVE_API_BASE_URL = f"http://127.0.0.1:{port}"


def _backend_is_compatible(client: "BackendApiClient") -> bool:
    try:
        health = client.health()
    except BackendApiError:
        return False
    return _health_payload_is_compatible(health)


def _health_payload_is_compatible(health: dict[str, Any]) -> bool:
    return (
        health.get("status") == "ok"
        and health.get("service") == BACKEND_SERVICE
        and _api_version_at_least(health.get("api_version"), MIN_BACKEND_API_VERSION)
        and str(health.get("revision") or "") == backend_revision()
    )


def _api_version_at_least(version: Any, minimum: int) -> bool:
    try:
        return int(version) >= int(minimum)
    except (TypeError, ValueError):
        return False


def _local_base_url_with_free_port(base_url: str) -> str:
    parsed = urlparse(base_url)
    host = parsed.hostname or "127.0.0.1"
    if host not in {"127.0.0.1", "localhost", "::1"}:
        return base_url
    return f"http://127.0.0.1:{_find_free_port()}"


def _find_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


class BackendApiClient:
    def __init__(self, base_url: Optional[str] = None, timeout: float = 20.0) -> None:
        self.base_url = (
            base_url
            or os.getenv("HIATUS_GUI_API_URL")
            or _ACTIVE_API_BASE_URL
            or DEFAULT_API_BASE_URL
        ).rstrip("/")
        self.timeout = timeout

    def request(self, method: str, path: str, **kwargs) -> Any:
        url = f"{self.base_url}{path}"
        try:
            response = requests.request(method, url, timeout=self.timeout, **kwargs)
            response.raise_for_status()
        except requests.RequestException as exc:
            body = ""
            if getattr(exc, "response", None) is not None:
                body = f" body={exc.response.text[:500]}"
            raise BackendApiError(f"Backend API request failed: {url} ({exc}){body}") from exc
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

    def gui_metadata(self) -> dict[str, Any]:
        return self.request("GET", "/api/gui/metadata")

    def douyin_stats(self, high_like_threshold: int) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/douyin/stats?{urlencode({'high_like_threshold': int(high_like_threshold)})}",
        )

    def douyin_video_count_stats(self, min_video_count: int) -> dict[str, Any]:
        return self.request(
            "GET",
            f"/api/douyin/video-count-stats?{urlencode({'min_video_count': int(min_video_count)})}",
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

    def exclude_creator_from_ladder(self, uploader_id: str, reason: str = "天梯榜取消资格") -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/douyin/creator-ladder-exclusion",
            json={"uploader_id": uploader_id, "reason": reason},
        )

    def dismiss_tian_can_creator(
        self,
        uploader_id: str,
        reason: str = "天参榜取消提示",
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/douyin/tian-can-dismiss",
            json={"uploader_id": uploader_id, "reason": reason},
        )

    def unfollow_tian_can_creator(
        self,
        uploader_id: str,
        homepage_url: str,
        reason: str = "天参榜取消关注",
    ) -> dict[str, Any]:
        return self.request(
            "POST",
            "/api/douyin/tian-can-unfollow",
            json={
                "uploader_id": uploader_id,
                "homepage_url": homepage_url,
                "reason": reason,
            },
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
        "douyin_unfollow": "douyin_unfollow",
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
        "douyin_browser_name": config.douyin_browser_name,
        "douyin_full_fetch_retry_on_mismatch": bool(
            config.douyin_full_fetch_retry_on_mismatch
        ),
        "uid_limit": config.uid_limit if config.uid_limit_enabled else None,
        "high_like_threshold": config.high_like_threshold,
        "unfollow_list_path": str(config.unfollow_list_path),
        "bilibili_runtime_settings": {"values": dict(config.bilibili_runtime_settings or {})},
        "douyin_runtime_settings": {"values": dict(config.douyin_runtime_settings or {})},
        "fetch_order_settings": dict(config.fetch_order_settings or {}),
    }


def rating_refresh_payload() -> dict[str, Any]:
    return {"kind": "douyin_rating_refresh", "action": "fetch"}


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


class DouyinLikedVideoCacheThread(BackendJobThread):
    def __init__(self, parent=None):
        super().__init__(payload=liked_video_cache_payload(), parent=parent)


class ApiCallThread(QThread):
    completed = pyqtSignal(bool, object, str)

    def __init__(self, operation: str, *args, timeout: float = 60.0, parent=None, **kwargs) -> None:
        super().__init__(parent)
        self.operation = operation
        self.args = args
        self.kwargs = kwargs
        self.timeout = timeout

    def run(self) -> None:
        try:
            client = BackendApiClient(timeout=self.timeout)
            result = getattr(client, self.operation)(*self.args, **self.kwargs)
            self.completed.emit(True, result, "")
        except Exception as exc:
            self.completed.emit(False, None, str(exc))


class BilibiliCookieCheckThread(QThread):
    checked = pyqtSignal(bool, str)

    def run(self) -> None:
        try:
            result = BackendApiClient(timeout=120.0).check_bilibili_cookie()
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
