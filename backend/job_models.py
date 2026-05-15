from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


class JobKind(str, Enum):
    BILIBILI_ANALYSIS = "bilibili_analysis"
    DOUYIN_ANALYSIS = "douyin_analysis"
    BOTH_ANALYSIS = "both_analysis"
    BILIBILI_UPLOAD = "bilibili_upload"
    DOUYIN_UPLOAD = "douyin_upload"
    BILIBILI_UID_FETCH = "bilibili_uid_fetch"
    DOUYIN_UID_FETCH = "douyin_uid_fetch"
    DOUYIN_UNFOLLOW = "douyin_unfollow"
    DOUYIN_PRUNE_NON_FOLLOWED_CACHE = "douyin_prune_non_followed_cache"
    DOUYIN_HIGH_LIKE_EXPORT = "douyin_high_like_export"
    DOUYIN_VIDEO_SCORE = "douyin_video_score"
    DOUYIN_CREATOR_SCORE = "douyin_creator_score"
    DOUYIN_COMPACT_EXPORT = "douyin_compact_export"
    DOUYIN_DATA_SYNC = "douyin_data_sync"


class JobStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AnalysisAction(str, Enum):
    FETCH = "fetch"
    FETCH_UPLOAD = "fetch_upload"
    UPLOAD = "upload"


class RuntimeSettings(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class FetchOrderSettings(BaseModel):
    bilibili: dict[str, str] = Field(
        default_factory=lambda: {"field": "follower_count", "direction": "desc"}
    )
    douyin: dict[str, str] = Field(
        default_factory=lambda: {"field": "follower_count", "direction": "desc"}
    )


class JobCreateRequest(BaseModel):
    kind: JobKind
    action: AnalysisAction = AnalysisAction.FETCH
    bilibili_mode: str = "precise_full"
    douyin_fetch_mode: str = "monitor"
    douyin_backend: str = "drission"
    monitor_video_limit: int = Field(default=10, ge=1)
    uid_limit: Optional[int] = Field(default=None, ge=1)
    persist_outputs: bool = True
    high_like_threshold: int = Field(default=10000, ge=0)
    unfollow_list_path: Optional[str] = None
    bilibili_uid_list_path: Optional[str] = None
    douyin_uid_list_path: Optional[str] = None
    bilibili_runtime_settings: RuntimeSettings = Field(default_factory=RuntimeSettings)
    douyin_runtime_settings: RuntimeSettings = Field(default_factory=RuntimeSettings)
    fetch_order_settings: FetchOrderSettings = Field(default_factory=FetchOrderSettings)


class JobSummary(BaseModel):
    id: str
    kind: JobKind
    status: JobStatus
    title: str
    created_at: datetime
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    current: int = 0
    total: Optional[int] = None
    message: str = ""
    error: Optional[str] = None
    result: Optional[Any] = None
    log_count: int = 0


class JobEventResponse(BaseModel):
    job_id: str
    next_offset: int
    lines: list[str]
