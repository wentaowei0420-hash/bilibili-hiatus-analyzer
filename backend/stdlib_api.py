from __future__ import annotations

import json
import os
from datetime import date, datetime
from enum import Enum
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from . import gui_data
from .config_defaults import get_config_defaults
from .gui_schema import get_gui_metadata
from .health_checks import check_bilibili_cookie_status
from .job_manager import JobManager
from .job_models import JobCreateRequest


job_manager = JobManager(max_workers=1)


def run(host: str | None = None, port: int | None = None) -> None:
    server = ThreadingHTTPServer(
        (host or os.getenv("HIATUS_API_HOST", "127.0.0.1"), port or int(os.getenv("HIATUS_API_PORT", "8000"))),
        _Handler,
    )
    print(f"Stdlib backend API running on http://{server.server_address[0]}:{server.server_address[1]}", flush=True)
    server.serve_forever()


class _Handler(BaseHTTPRequestHandler):
    server_version = "HiatusStdlibAPI/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        try:
            if path == "/api/health":
                self._json({"status": "ok", "service": "hiatus-backend", "api_version": "4"})
            elif path == "/api/capabilities":
                self._json(_capabilities())
            elif path == "/api/config/defaults":
                self._json(get_config_defaults())
            elif path == "/api/gui/metadata":
                self._json(get_gui_metadata())
            elif path == "/api/bilibili/cookie-status":
                self._json(check_bilibili_cookie_status())
            elif path == "/api/douyin/stats":
                self._json(gui_data.get_douyin_stats(_int_query(query, "high_like_threshold", 10000)))
            elif path == "/api/douyin/rating-overview":
                self._json(gui_data.get_rating_overview(_str_query(query, "search_uid", "")))
            elif path.startswith("/api/douyin/creator-detail/"):
                self._json(gui_data.get_creator_detail(unquote(path.rsplit("/", 1)[-1])))
            elif path == "/api/douyin/status-reset":
                self._json(gui_data.get_status_reset_candidates(_int_query(query, "threshold", 30)))
            elif path == "/api/douyin/archive":
                self._json(gui_data.get_archive_state(_int_query(query, "threshold", 100)))
            elif path == "/api/jobs":
                self._json(job_manager.list_jobs())
            elif path.startswith("/api/jobs/") and path.endswith("/events"):
                job_id = path.split("/")[3]
                job = job_manager.get_job(job_id)
                if not job:
                    self._error(404, "Job not found.")
                    return
                next_offset, lines = job_manager.read_logs(job_id, offset=_int_query(query, "offset", 0))
                self._json({"job_id": job_id, "next_offset": next_offset, "lines": lines})
            elif path.startswith("/api/jobs/"):
                job_id = path.rsplit("/", 1)[-1]
                job = job_manager.get_job(job_id)
                self._json(job) if job else self._error(404, "Job not found.")
            else:
                self._error(404, "Not found.")
        except Exception as exc:
            self._error(500, str(exc))

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        payload = self._read_json()

        try:
            if path == "/api/jobs":
                self._json(job_manager.create_job(JobCreateRequest(**payload)))
            elif path.startswith("/api/jobs/") and path.endswith("/cancel"):
                job_id = path.split("/")[3]
                job = job_manager.cancel_job(job_id)
                self._json(job) if job else self._error(404, "Job not found.")
            elif path == "/api/douyin/creator-manual-grade":
                self._json(
                    gui_data.save_creator_manual_grade(
                        str(payload.get("uploader_id") or ""),
                        str(payload.get("grade") or ""),
                        str(payload.get("note") or ""),
                    )
                )
            elif path == "/api/douyin/creator-ladder-exclusion":
                self._json(
                    gui_data.exclude_creator_from_ladder(
                        str(payload.get("uploader_id") or ""),
                        str(payload.get("reason") or "天梯榜取消资格"),
                    )
                )
            elif path == "/api/douyin/status-reset":
                self._json(gui_data.reset_full_status(list(payload.get("uids") or [])))
            elif path == "/api/douyin/archive":
                threshold = int(payload.get("threshold") or 100)
                if payload.get("all"):
                    self._json(gui_data.archive_all_candidates(threshold))
                else:
                    self._json(gui_data.archive_creators_by_uid(list(payload.get("uids") or []), threshold))
            elif path == "/api/douyin/archive/restore":
                self._json(gui_data.restore_archived_creators(list(payload.get("uids") or [])))
            else:
                self._error(404, "Not found.")
        except Exception as exc:
            self._error(500, str(exc))

    def log_message(self, format: str, *args: Any) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8") or "{}")

    def _json(self, value: Any, status: int = 200) -> None:
        body = json.dumps(_to_jsonable(value), ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _error(self, status: int, detail: str) -> None:
        self._json({"detail": detail}, status=status)


def _to_jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        return _to_jsonable(value.model_dump())
    if hasattr(value, "dict") and value.__class__.__module__.startswith("backend."):
        return _to_jsonable(value.dict())
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_to_jsonable(item) for item in value]
    return value


def _int_query(query: dict[str, list[str]], key: str, default: int) -> int:
    try:
        return int((query.get(key) or [default])[0])
    except (TypeError, ValueError):
        return default


def _str_query(query: dict[str, list[str]], key: str, default: str) -> str:
    return str((query.get(key) or [default])[0])


def _capabilities() -> dict[str, object]:
    return {
        "platforms": ["bilibili", "douyin"],
        "queue": {"max_workers": 1},
        "job_kinds": [
            "bilibili_analysis",
            "douyin_analysis",
            "both_analysis",
            "bilibili_uid_fetch",
            "douyin_uid_fetch",
            "douyin_unfollow",
            "douyin_prune_non_followed_cache",
            "douyin_high_like_export",
            "douyin_video_score",
            "douyin_creator_score",
            "douyin_rating_refresh",
            "douyin_compact_export",
            "douyin_data_sync",
            "douyin_liked_video_cache",
        ],
    }
