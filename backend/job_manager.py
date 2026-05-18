from __future__ import annotations

import threading
import uuid
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional

from common.runtime_control import OperationCancelled, request_stop

from .job_models import JobCreateRequest, JobStatus, JobSummary
from .task_runner import TaskContext, run_job


MAX_LOG_LINES = 5000


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


@dataclass
class ManagedJob:
    request: JobCreateRequest
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    status: JobStatus = JobStatus.QUEUED
    title: str = ""
    created_at: datetime = field(default_factory=datetime.now)
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current: int = 0
    total: Optional[int] = None
    message: str = ""
    error: Optional[str] = None
    result: Optional[Any] = None
    logs: deque[str] = field(default_factory=lambda: deque(maxlen=MAX_LOG_LINES))
    cancel_requested: bool = False
    future: Optional[Future] = None

    def to_summary(self) -> JobSummary:
        return JobSummary(
            id=self.id,
            kind=self.request.kind,
            status=self.status,
            title=self.title or _enum_value(self.request.kind),
            created_at=self.created_at,
            started_at=self.started_at,
            finished_at=self.finished_at,
            current=self.current,
            total=self.total,
            message=self.message,
            error=self.error,
            result=self.result,
            log_count=len(self.logs),
        )


class JobManager:
    def __init__(self, max_workers: int = 1) -> None:
        self._executor = ThreadPoolExecutor(max_workers=max_workers)
        self._jobs: dict[str, ManagedJob] = {}
        self._lock = threading.RLock()

    def create_job(self, request: JobCreateRequest) -> JobSummary:
        job = ManagedJob(request=request, title=_enum_value(request.kind))
        with self._lock:
            self._jobs[job.id] = job
            job.future = self._executor.submit(self._run_managed_job, job.id)
        return job.to_summary()

    def list_jobs(self) -> list[JobSummary]:
        with self._lock:
            jobs = sorted(self._jobs.values(), key=lambda item: item.created_at, reverse=True)
            return [job.to_summary() for job in jobs]

    def get_job(self, job_id: str) -> Optional[JobSummary]:
        with self._lock:
            job = self._jobs.get(job_id)
            return job.to_summary() if job else None

    def read_logs(self, job_id: str, offset: int = 0) -> tuple[int, list[str]]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return offset, []
            lines = list(job.logs)
            safe_offset = max(0, min(offset, len(lines)))
            return len(lines), lines[safe_offset:]

    def cancel_job(self, job_id: str) -> Optional[JobSummary]:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return None
            job.cancel_requested = True
            job.message = "Cancel requested"
            if job.status in {JobStatus.QUEUED, JobStatus.RUNNING}:
                request_stop()
                if job.future and job.future.cancel():
                    job.status = JobStatus.CANCELLED
                    job.finished_at = datetime.now()
            return job.to_summary()

    def _run_managed_job(self, job_id: str) -> None:
        with self._lock:
            job = self._jobs[job_id]
            if job.cancel_requested:
                job.status = JobStatus.CANCELLED
                job.finished_at = datetime.now()
                return
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now()
            job.message = "Running"

        context = TaskContext(
            emit=lambda line: self._append_log(job_id, line),
            update=lambda **values: self._update_job(job_id, **values),
        )

        try:
            result = run_job(job.request, context)
        except OperationCancelled as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = JobStatus.CANCELLED
                job.error = str(exc)
                job.message = "Cancelled"
                job.finished_at = datetime.now()
            return
        except Exception as exc:
            with self._lock:
                job = self._jobs[job_id]
                job.status = JobStatus.FAILED
                job.error = str(exc)
                job.message = "Failed"
                job.finished_at = datetime.now()
            return

        with self._lock:
            job = self._jobs[job_id]
            job.status = JobStatus.SUCCEEDED
            job.result = result
            job.message = "Succeeded"
            job.finished_at = datetime.now()

    def _append_log(self, job_id: str, line: str) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job:
                job.logs.append(line)

    def _update_job(self, job_id: str, **values: Any) -> None:
        with self._lock:
            job = self._jobs.get(job_id)
            if not job:
                return
            for key, value in values.items():
                if hasattr(job, key):
                    setattr(job, key, value)
