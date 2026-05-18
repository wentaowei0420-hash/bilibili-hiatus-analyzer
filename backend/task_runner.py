from __future__ import annotations

import json
import os
import traceback
from contextlib import contextmanager, redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Iterator, Optional

from common.runtime_control import OperationCancelled, check_stop, clear_stop

from .job_models import AnalysisAction, JobCreateRequest, JobKind
from .reporting import JobReporter


ROOT_DIR = Path(__file__).resolve().parent.parent
DEFAULT_DOUYIN_UNFOLLOW_LIST = (
    ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"
)
DEFAULT_BILIBILI_UID_LIST = (
    ROOT_DIR / "data" / "bilibili" / "ops" / "bilibili_uid_fetch_list.txt"
)
DEFAULT_DOUYIN_UID_LIST = (
    ROOT_DIR / "data" / "douyin" / "ops" / "douyin_uid_fetch_list.txt"
)


BILIBILI_RUNTIME_FIELDS = {
    "video_stat_batch_cooldown": ("VIDEO_STAT_BATCH_COOLDOWN", "int"),
    "request_delay": ("REQUEST_DELAY", "int"),
    "max_request_delay": ("MAX_REQUEST_DELAY", "int"),
    "video_analysis_start_delay": ("VIDEO_ANALYSIS_START_DELAY", "int"),
    "batch_cooldown": ("BATCH_COOLDOWN", "int"),
    "long_rate_limit_cooldown": ("LONG_RATE_LIMIT_COOLDOWN", "int"),
    "rate_limit_retry_before_long_cooldown": (
        "RATE_LIMIT_RETRY_BEFORE_LONG_COOLDOWN",
        "int",
    ),
    "max_rate_limit_retries": ("MAX_RATE_LIMIT_RETRIES", "int"),
    "failed_retry_cooldown": ("FAILED_RETRY_COOLDOWN", "int"),
    "video_analysis_batch_cooldown": ("VIDEO_ANALYSIS_BATCH_COOLDOWN", "int"),
}


DOUYIN_RUNTIME_FIELDS = {
    "page_load_delay": ("DOUYIN_PAGE_LOAD_DELAY", "float"),
    "user_request_interval": ("DOUYIN_USER_REQUEST_INTERVAL", "float"),
    "request_rate_limit_per_second": (
        "DOUYIN_REQUEST_RATE_LIMIT_PER_SECOND",
        "float",
    ),
    "retry_backoff_base_seconds": ("DOUYIN_RETRY_BACKOFF_BASE_SECONDS", "float"),
    "retry_backoff_max_seconds": ("DOUYIN_RETRY_BACKOFF_MAX_SECONDS", "float"),
    "conservative_mode_duration_seconds": (
        "DOUYIN_CONSERVATIVE_MODE_DURATION_SECONDS",
        "float",
    ),
    "refresh_batch_cooldown": ("DOUYIN_REFRESH_BATCH_COOLDOWN", "float"),
    "browser_restart_interval_users": (
        "DOUYIN_BROWSER_RESTART_INTERVAL_USERS",
        "int",
    ),
    "video_page_load_delay": ("DOUYIN_VIDEO_PAGE_LOAD_DELAY", "float"),
    "service_error_retry_wait": ("DOUYIN_SERVICE_ERROR_RETRY_WAIT", "float"),
    "service_error_long_cooldown": ("DOUYIN_SERVICE_ERROR_LONG_COOLDOWN", "float"),
    "service_error_global_cooldown": (
        "DOUYIN_SERVICE_ERROR_GLOBAL_COOLDOWN",
        "float",
    ),
    "rate_limit_retry_wait": ("DOUYIN_RATE_LIMIT_RETRY_WAIT", "float"),
    "rate_limit_long_cooldown": ("DOUYIN_RATE_LIMIT_LONG_COOLDOWN", "float"),
    "rate_limit_global_cooldown": ("DOUYIN_RATE_LIMIT_GLOBAL_COOLDOWN", "float"),
    "progress_save_interval_users": ("DOUYIN_PROGRESS_SAVE_INTERVAL_USERS", "int"),
    "intermediate_upload_interval_users": (
        "DOUYIN_INTERMEDIATE_UPLOAD_INTERVAL_USERS",
        "int",
    ),
    "unfollow_interval_seconds": ("DOUYIN_UNFOLLOW_INTERVAL_SECONDS", "float"),
    "unfollow_batch_cooldown": ("DOUYIN_UNFOLLOW_BATCH_COOLDOWN", "float"),
    "unfollow_restart_interval": ("DOUYIN_UNFOLLOW_RESTART_INTERVAL", "int"),
    "unfollow_failure_cooldown": ("DOUYIN_UNFOLLOW_FAILURE_COOLDOWN", "float"),
    "video_browser_fallback_max_ids": ("DOUYIN_VIDEO_BROWSER_FALLBACK_MAX_IDS", "int"),
}


def _enum_value(value: Any) -> str:
    return str(getattr(value, "value", value))


FETCH_ORDER_FIELDS = {
    "bilibili": {"follower_count", "published_video_count", "average_like_count"},
    "douyin": {
        "follower_count",
        "published_video_count",
        "total_favorited",
        "average_like_count",
    },
}


@dataclass
class TaskContext:
    emit: Callable[[str], None]
    update: Callable[..., None]


class LineWriter:
    def __init__(self, emit: Callable[[str], None]) -> None:
        self.emit = emit
        self.encoding = "utf-8"
        self._buffer = ""

    def write(self, text: str) -> None:
        if not text:
            return
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.emit(line.rstrip("\r"))

    def flush(self) -> None:
        if self._buffer:
            self.emit(self._buffer.rstrip("\r"))
            self._buffer = ""

    def isatty(self) -> bool:
        return False


@contextmanager
def _temporary_env(values: dict[str, str]) -> Iterator[None]:
    previous = {key: os.environ.get(key) for key in values}
    try:
        for key, value in values.items():
            os.environ[key] = value
        yield
    finally:
        for key, old_value in previous.items():
            if old_value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old_value


def _coerce_env_value(value: Any, value_type: str) -> Optional[str]:
    if value is None or value == "":
        return None
    try:
        if value_type == "int":
            return str(int(value))
        if value_type == "float":
            return str(float(value))
    except (TypeError, ValueError):
        return None
    return str(value)


def _resolve_bilibili_mode(mode: str) -> tuple[str, bool]:
    mapping = {
        "precise_full": ("precise", True),
        "precise_main_only": ("precise", False),
        "fallback_full": ("fallback", True),
        "fallback_main_only": ("fallback", False),
    }
    return mapping.get((mode or "precise_full").strip().lower(), ("precise", True))


def _build_runtime_env(request: JobCreateRequest) -> dict[str, str]:
    analysis_mode, enable_video_analysis = _resolve_bilibili_mode(
        request.bilibili_mode
    )
    env = {
        "DOUYIN_BROWSER_BACKEND": (request.douyin_backend or "drission").strip().lower(),
        "ANALYSIS_MODE": analysis_mode,
        "ENABLE_VIDEO_DURATION_ANALYSIS": (
            "true" if enable_video_analysis else "false"
        ),
    }

    for name, (env_name, value_type) in BILIBILI_RUNTIME_FIELDS.items():
        value = _coerce_env_value(
            request.bilibili_runtime_settings.values.get(name), value_type
        )
        if value is not None:
            env[env_name] = value

    for name, (env_name, value_type) in DOUYIN_RUNTIME_FIELDS.items():
        value = _coerce_env_value(
            request.douyin_runtime_settings.values.get(name), value_type
        )
        if value is not None:
            env[env_name] = value

    fetch_order = request.fetch_order_settings
    for platform in ("bilibili", "douyin"):
        settings = getattr(fetch_order, platform, {}) or {}
        field = settings.get("field") or "follower_count"
        if field not in FETCH_ORDER_FIELDS[platform]:
            field = "follower_count"
        desc = (settings.get("direction") or "desc").strip().lower() != "asc"
        prefix = platform.upper()
        env[f"{prefix}_FETCH_ORDER_BY"] = field
        env[f"{prefix}_FETCH_ORDER_DESC"] = "true" if desc else "false"

    return env


def _json_safe(value: Any) -> Any:
    if hasattr(value, "to_dict"):
        return _json_safe(value.to_dict())
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (datetime,)):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item) for item in value]
    try:
        json.dumps(value)
        return value
    except TypeError:
        return repr(value)


def _path_or_default(raw_path: Optional[str], default_path: Path) -> Path:
    if not raw_path:
        return default_path
    return Path(raw_path).expanduser()


def run_job(request: JobCreateRequest, context: TaskContext) -> Any:
    clear_stop()
    env = _build_runtime_env(request)
    writer = LineWriter(context.emit)
    reporter = JobReporter(context.emit, context.update)
    context.update(message=f"Starting {_enum_value(request.kind)}")

    with _temporary_env(env), redirect_stdout(writer), redirect_stderr(writer):
        try:
            result = _dispatch_job(request, context, reporter)
            writer.flush()
            context.update(message=f"Finished {_enum_value(request.kind)}")
            return _json_safe(result)
        except OperationCancelled:
            writer.flush()
            context.update(message="Cancelled")
            raise
        except Exception:
            writer.flush()
            context.emit(traceback.format_exc())
            raise


def _dispatch_job(request: JobCreateRequest, context: TaskContext, reporter) -> Any:
    kind = request.kind
    context.emit(f"Job kind: {_enum_value(kind)}")
    context.emit(f"Action: {_enum_value(request.action)}")
    context.emit(f"Douyin mode: {request.douyin_fetch_mode}")
    context.emit(f"Douyin backend: {request.douyin_backend}")
    context.emit("-" * 60)

    if kind == JobKind.BILIBILI_ANALYSIS:
        return _run_bilibili_main(request, reporter)
    if kind == JobKind.DOUYIN_ANALYSIS:
        return _run_douyin_main(request, reporter)
    if kind == JobKind.BOTH_ANALYSIS:
        first = _run_bilibili_main(request, reporter)
        check_stop()
        second = _run_douyin_main(request, reporter)
        return {"bilibili": _json_safe(first), "douyin": _json_safe(second)}
    if kind == JobKind.BILIBILI_UPLOAD:
        from bilibili_analyzer.app import run_feishu_upload

        return run_feishu_upload()
    if kind == JobKind.DOUYIN_UPLOAD:
        from douyin_analyzer.app import run_feishu_upload

        return run_feishu_upload()
    if kind == JobKind.BILIBILI_UID_FETCH:
        from bilibili_analyzer.app import run_fetch_uid_videos

        return run_fetch_uid_videos(
            _path_or_default(request.bilibili_uid_list_path, DEFAULT_BILIBILI_UID_LIST),
            max_targets=request.uid_limit,
        )
    if kind == JobKind.DOUYIN_UID_FETCH:
        from douyin_analyzer.app import run_fetch_uid_videos

        return run_fetch_uid_videos(
            _path_or_default(request.douyin_uid_list_path, DEFAULT_DOUYIN_UID_LIST),
            max_targets=request.uid_limit,
        )
    if kind == JobKind.DOUYIN_UNFOLLOW:
        from douyin_analyzer.app import run_unfollow

        return run_unfollow(
            _path_or_default(request.unfollow_list_path, DEFAULT_DOUYIN_UNFOLLOW_LIST)
        )
    if kind == JobKind.DOUYIN_PRUNE_NON_FOLLOWED_CACHE:
        from douyin_analyzer.app import run_prune_non_followed_cache

        return run_prune_non_followed_cache()
    if kind == JobKind.DOUYIN_VIDEO_SCORE:
        from douyin_analyzer.app import run_score_videos_from_cache

        return run_score_videos_from_cache()
    if kind == JobKind.DOUYIN_CREATOR_SCORE:
        from douyin_analyzer.app import run_score_creators_from_cache

        return run_score_creators_from_cache()
    if kind == JobKind.DOUYIN_RATING_REFRESH:
        from douyin_analyzer.app import (
            run_score_creators_from_cache,
            run_score_videos_from_cache,
        )

        run_score_videos_from_cache()
        output_path = run_score_creators_from_cache(refresh_inventory=False)
        return {"message": f"评分数据已更新：{output_path}", "output_path": output_path}
    if kind == JobKind.DOUYIN_COMPACT_EXPORT:
        from douyin_analyzer.app import run_export_compact_tables_from_cache

        return run_export_compact_tables_from_cache(
            high_like_threshold=request.high_like_threshold
        )
    if kind == JobKind.DOUYIN_DATA_SYNC:
        from douyin_analyzer.config import load_analyzer_config
        from douyin_analyzer.data_sync import sync_progress_videos_to_state

        config = load_analyzer_config()
        result = sync_progress_videos_to_state(config, rerun_scores=True)
        if isinstance(result, dict):
            result["message"] = _format_douyin_data_sync_message(result)
        return result
    if kind == JobKind.DOUYIN_LIKED_VIDEO_CACHE:
        from douyin_analyzer.app import run_cache_liked_videos_as_s

        result = run_cache_liked_videos_as_s()
        video_count = result.get("video_count", 0) if isinstance(result, dict) else 0
        return {
            "message": (
                f"喜欢视频缓存完成：{video_count} 条视频已写入本地缓存，"
                "并统一设置为 S 级。"
            ),
            "result": result,
        }

    raise ValueError(f"Unsupported job kind: {_enum_value(kind)}")


def _format_douyin_data_sync_message(result: dict[str, Any]) -> str:
    before = result.get("before_diagnostics", {}) or {}
    after = result.get("after_diagnostics", {}) or {}
    return (
        "抖音数据同步完成："
        f"同步UP {result.get('processed_creators', 0)} 位，"
        f"progress视频 {result.get('processed_videos', 0)} 条，"
        f"raw视频 {result.get('raw_videos_processed', 0)} 条；"
        f"解除full重置 {result.get('resolved_full_status_resets', 0)} 位；"
        f"清单缓存数更新 {result.get('inventory_rows_updated', 0)} 行；"
        f"douyin_video_state {result.get('video_state_before', 0)} -> {result.get('video_state_after', 0)}；"
        f"video_score_current {result.get('video_score_before', 0)} -> {result.get('video_score_after', 0)}；"
        f"creator_score_current {result.get('creator_score_before', 0)} -> {result.get('creator_score_after', 0)}；"
        f"当前缓存视频 {after.get('current_progress_videos', 0)} 条；"
        f"当前缓存未评分 {before.get('current_progress_not_scored', 0)} -> "
        f"{after.get('current_progress_not_scored', 0)}；"
        f"评分不在当前缓存 {before.get('score_not_current_progress', 0)} -> "
        f"{after.get('score_not_current_progress', 0)}；"
        f"下载标记孤儿 {before.get('score_download_mark_without_aweme', 0)} -> "
        f"{after.get('score_download_mark_without_aweme', 0)}；"
        f"需人工状态重置 {after.get('full_cache_count_mismatch_gt_30', 0)} 位"
    )


def _run_bilibili_main(request: JobCreateRequest, reporter=None) -> Any:
    from bilibili_analyzer.app import run_analysis, run_feishu_upload

    if request.action == AnalysisAction.FETCH:
        result = run_analysis(
            trigger_upload=False,
            max_followings=request.uid_limit,
            reporter=reporter,
            export_outputs=request.persist_outputs,
        )
        if result is None:
            raise RuntimeError("Bilibili analysis did not complete successfully.")
        return result
    if request.action == AnalysisAction.FETCH_UPLOAD:
        result = run_analysis(
            trigger_upload=True,
            max_followings=request.uid_limit,
            reporter=reporter,
            export_outputs=True,
        )
        if result is None:
            raise RuntimeError("Bilibili analysis did not complete successfully.")
        return result
    if request.action == AnalysisAction.UPLOAD:
        return run_feishu_upload()
    raise ValueError(f"Unsupported Bilibili action: {_enum_value(request.action)}")


def _run_douyin_main(request: JobCreateRequest, reporter=None) -> Any:
    from douyin_analyzer.app import run_analysis, run_feishu_upload

    if request.action == AnalysisAction.FETCH:
        result = run_analysis(
            trigger_upload=False,
            fetch_mode_override=request.douyin_fetch_mode,
            max_followings=request.uid_limit,
            recent_video_limit_override=request.monitor_video_limit,
            reporter=reporter,
            export_outputs=request.persist_outputs,
        )
        if result is None:
            raise RuntimeError("Douyin analysis did not complete successfully.")
        return result
    if request.action == AnalysisAction.FETCH_UPLOAD:
        result = run_analysis(
            trigger_upload=True,
            fetch_mode_override=request.douyin_fetch_mode,
            max_followings=request.uid_limit,
            recent_video_limit_override=request.monitor_video_limit,
            reporter=reporter,
            export_outputs=True,
        )
        if result is None:
            raise RuntimeError("Douyin analysis did not complete successfully.")
        return result
    if request.action == AnalysisAction.UPLOAD:
        return run_feishu_upload()
    raise ValueError(f"Unsupported Douyin action: {_enum_value(request.action)}")
