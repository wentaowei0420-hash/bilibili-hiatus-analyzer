from __future__ import annotations

from datetime import datetime
from enum import Enum
import sys
from typing import Any, Optional, get_type_hints

try:
    from pydantic import BaseModel, Field
except ImportError:
    _MISSING = object()

    class _FieldDefault:
        def __init__(self, default: Any = _MISSING, default_factory: Any = None) -> None:
            self.default = default
            self.default_factory = default_factory

        def make(self) -> Any:
            if self.default_factory is not None:
                return self.default_factory()
            if self.default is _MISSING:
                return None
            return self.default

    def Field(default: Any = _MISSING, default_factory: Any = None, **_kwargs: Any) -> Any:
        return _FieldDefault(default=default, default_factory=default_factory)

    class BaseModel:
        def __init__(self, **data: Any) -> None:
            module_globals = sys.modules[type(self).__module__].__dict__
            annotations = get_type_hints(type(self), globalns=module_globals, localns=module_globals)

            for name, annotation in annotations.items():
                if name in data:
                    value = data.pop(name)
                else:
                    default = getattr(type(self), name, _MISSING)
                    value = default.make() if isinstance(default, _FieldDefault) else default
                    if value is _MISSING:
                        value = None
                setattr(self, name, self._coerce_value(annotation, value))

            for name, value in data.items():
                setattr(self, name, value)

        @staticmethod
        def _coerce_value(annotation: Any, value: Any) -> Any:
            try:
                if isinstance(annotation, type) and issubclass(annotation, Enum) and not isinstance(value, annotation):
                    return annotation(value)
                if isinstance(annotation, type) and issubclass(annotation, BaseModel) and isinstance(value, dict):
                    return annotation(**value)
            except TypeError:
                pass
            return value

        def dict(self) -> dict[str, Any]:
            return dict(self.__dict__)

        def model_dump(self) -> dict[str, Any]:
            return self.dict()


class JobKind(str, Enum):
    BILIBILI_ANALYSIS = "bilibili_analysis"
    DOUYIN_ANALYSIS = "douyin_analysis"
    BOTH_ANALYSIS = "both_analysis"
    BILIBILI_UPLOAD = "bilibili_upload"
    DOUYIN_UPLOAD = "douyin_upload"
    BILIBILI_UID_FETCH = "bilibili_uid_fetch"
    DOUYIN_UID_FETCH = "douyin_uid_fetch"
    DOUYIN_UNFOLLOW = "douyin_unfollow"
    DOUYIN_VIDEO_SCORE = "douyin_video_score"
    DOUYIN_CREATOR_SCORE = "douyin_creator_score"
    DOUYIN_RATING_REFRESH = "douyin_rating_refresh"
    DOUYIN_COMPACT_EXPORT = "douyin_compact_export"
    DOUYIN_LIKED_VIDEO_CACHE = "douyin_liked_video_cache"


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
    douyin_full_fetch_retry_on_mismatch: bool = True
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
