from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

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
    return {"status": "ok"}


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
            "douyin_high_like_export",
            "douyin_video_score",
            "douyin_creator_score",
            "douyin_compact_export",
            "douyin_data_sync",
        ],
        "note": "douyin-downloader-main is intentionally isolated from this API refactor.",
    }


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
