from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .config_defaults import get_config_defaults
from . import gui_data
from .gui_schema import get_gui_metadata
from .health_checks import check_bilibili_cookie_status
from .job_manager import JobManager
from .job_models import JobCreateRequest, JobEventResponse, JobSummary


ROOT_DIR = Path(__file__).resolve().parent.parent
FRONTEND_DIR = ROOT_DIR / "frontend"

app = FastAPI(
    title="Bilibili/Douyin Hiatus Analyzer API",
    version="1.0.0",
    description="Task API for analyzer jobs. The downloader subproject is not touched.",
)
job_manager = JobManager(max_workers=1)

if FRONTEND_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(FRONTEND_DIR)), name="static")


@app.get("/")
def index() -> FileResponse:
    index_path = FRONTEND_DIR / "index.html"
    if not index_path.exists():
        raise HTTPException(status_code=404, detail="Frontend is not available.")
    return FileResponse(index_path)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "hiatus-backend", "api_version": "4"}


@app.get("/api/capabilities")
def capabilities() -> dict[str, object]:
    return {
        "platforms": ["bilibili", "douyin"],
        "queue": {"max_workers": 1},
        "analysis_options": {
            "persist_outputs": "Set false on fetch jobs to return results without writing analyzer export files.",
        },
        "job_kinds": [
            "bilibili_analysis",
            "douyin_analysis",
            "both_analysis",
            "bilibili_upload",
            "douyin_upload",
            "bilibili_uid_fetch",
            "douyin_uid_fetch",
            "douyin_unfollow",
            "douyin_prune_non_followed_cache",
            "douyin_video_score",
            "douyin_creator_score",
            "douyin_rating_refresh",
            "douyin_compact_export",
            "douyin_data_sync",
            "douyin_liked_video_cache",
        ],
        "note": "douyin-downloader-main is intentionally isolated from this API refactor.",
    }


@app.get("/api/config/defaults")
def config_defaults() -> dict[str, object]:
    return get_config_defaults()


@app.get("/api/gui/metadata")
def gui_metadata() -> dict[str, object]:
    return get_gui_metadata()


@app.get("/api/bilibili/cookie-status")
def bilibili_cookie_status() -> dict[str, object]:
    return check_bilibili_cookie_status()


@app.get("/api/douyin/stats")
def douyin_stats(
    high_like_threshold: int = Query(default=10000, ge=0),
) -> dict[str, object]:
    return gui_data.get_douyin_stats(high_like_threshold)


@app.get("/api/douyin/rating-overview")
def douyin_rating_overview(search_uid: str = "") -> dict[str, object]:
    return gui_data.get_rating_overview(search_uid)


@app.get("/api/douyin/creator-detail/{uploader_id}")
def douyin_creator_detail(uploader_id: str) -> dict[str, object]:
    return gui_data.get_creator_detail(uploader_id)


@app.post("/api/douyin/creator-manual-grade")
def douyin_creator_manual_grade(payload: dict[str, object]) -> dict[str, object]:
    return gui_data.save_creator_manual_grade(
        str(payload.get("uploader_id") or ""),
        str(payload.get("grade") or ""),
        str(payload.get("note") or ""),
    )


@app.post("/api/douyin/creator-ladder-exclusion")
def douyin_creator_ladder_exclusion(payload: dict[str, object]) -> dict[str, object]:
    return gui_data.exclude_creator_from_ladder(
        str(payload.get("uploader_id") or ""),
        str(payload.get("reason") or "天梯榜取消资格"),
    )


@app.get("/api/douyin/status-reset")
def douyin_status_reset(
    threshold: int = Query(default=30, ge=1),
) -> dict[str, object]:
    return gui_data.get_status_reset_candidates(threshold)


@app.post("/api/douyin/status-reset")
def douyin_status_reset_apply(payload: dict[str, object]) -> dict[str, object]:
    return gui_data.reset_full_status(list(payload.get("uids") or []))


@app.get("/api/douyin/archive")
def douyin_archive(
    threshold: int = Query(default=100, ge=1),
) -> dict[str, object]:
    return gui_data.get_archive_state(threshold)


@app.post("/api/douyin/archive")
def douyin_archive_apply(payload: dict[str, object]) -> dict[str, object]:
    threshold = int(payload.get("threshold") or 100)
    if payload.get("all"):
        return gui_data.archive_all_candidates(threshold)
    return gui_data.archive_creators_by_uid(list(payload.get("uids") or []), threshold)


@app.post("/api/douyin/archive/restore")
def douyin_archive_restore(payload: dict[str, object]) -> dict[str, object]:
    return gui_data.restore_archived_creators(list(payload.get("uids") or []))


@app.post("/api/jobs", response_model=JobSummary)
def create_job(request: JobCreateRequest) -> JobSummary:
    return job_manager.create_job(request)


@app.get("/api/jobs", response_model=list[JobSummary])
def list_jobs() -> list[JobSummary]:
    return job_manager.list_jobs()


@app.get("/api/jobs/{job_id}", response_model=JobSummary)
def get_job(job_id: str) -> JobSummary:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job


@app.get("/api/jobs/{job_id}/events", response_model=JobEventResponse)
def get_job_events(
    job_id: str,
    offset: int = Query(default=0, ge=0),
) -> JobEventResponse:
    job = job_manager.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    next_offset, lines = job_manager.read_logs(job_id, offset=offset)
    return JobEventResponse(job_id=job_id, next_offset=next_offset, lines=lines)


@app.post("/api/jobs/{job_id}/cancel", response_model=JobSummary)
def cancel_job(job_id: str) -> JobSummary:
    job = job_manager.cancel_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Job not found.")
    return job
