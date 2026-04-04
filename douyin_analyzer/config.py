import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


load_dotenv()


def _get_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _resolve_path_env(name: str, default_path: Path, *, must_exist: bool = False) -> Path:
    raw_value = os.getenv(name)
    if not raw_value:
        return default_path

    candidate = Path(raw_value)
    if must_exist:
        return candidate if candidate.exists() else default_path
    if candidate.exists() or candidate.parent.exists():
        return candidate
    return default_path


@dataclass(frozen=True)
class DouyinAnalyzerConfig:
    root_dir: Path
    log_dir: Path
    browser_user_data_path: Path
    home_url: str
    self_user_url: str
    following_api_pattern: str
    post_api_pattern: str
    output_csv: Path
    all_videos_csv: Path
    video_duration_analysis_csv: Path
    video_duration_report_md: Path
    cache_inventory_csv: Path
    followings_cache_json: Path
    followings_cache_dir: Path
    progress_json: Path
    progress_dir: Path
    fetch_mode: str
    recent_video_limit: int
    page_load_delay: float
    packet_timeout: float
    scroll_pause: float
    empty_round_limit: int
    user_request_interval: float
    refresh_batch_size: int
    refresh_batch_cooldown: float
    browser_restart_interval_users: int
    video_page_load_delay: float
    video_packet_timeout: float
    video_scroll_pause: float
    video_empty_round_limit: int
    video_scroll_steps_per_round: int
    video_scroll_distance: int
    video_page_retry_count: int
    service_error_retry_wait: float
    service_error_long_cooldown: float
    service_error_global_cooldown: float
    rate_limit_retry_wait: float
    rate_limit_long_cooldown: float
    rate_limit_global_cooldown: float
    progress_save_interval_users: int
    intermediate_upload_interval_users: int
    followings_cache_max_age_hours: int
    precise_cache_max_age_hours: int
    progress_trim_video_limit: int
    enable_video_duration_analysis: bool
    unfollow_interval_seconds: float
    unfollow_batch_size: int
    unfollow_batch_cooldown: float
    unfollow_restart_interval: int
    unfollow_failure_cooldown: float


@dataclass(frozen=True)
class DouyinFeishuConfig:
    root_dir: Path
    log_dir: Path
    app_id: str
    app_secret: str
    spreadsheet_token: str
    sheet_title: str
    sheet_index: int
    file_hiatus: Path
    file_duration: Path
    file_merged_output: Path
    db_path: Path
    upload_state_json: Path
    history_retention_days: int


def _root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def load_analyzer_config(fetch_mode_override=None) -> DouyinAnalyzerConfig:
    root_dir = _root_dir()
    runtime_dir = root_dir / "runtime"
    log_dir = runtime_dir / "logs"
    data_dir = root_dir / "data" / "douyin"
    output_dir = data_dir / "output"
    state_dir = data_dir / "state"
    fetch_mode = (fetch_mode_override or os.getenv("DOUYIN_FETCH_MODE", "monitor")).strip().lower()
    return DouyinAnalyzerConfig(
        root_dir=root_dir,
        log_dir=log_dir,
        browser_user_data_path=Path(
            os.getenv(
                "DOUYIN_BROWSER_USER_DATA_PATH",
                str(runtime_dir / "chrome_data"),
            )
        ),
        home_url="https://www.douyin.com/",
        self_user_url="https://www.douyin.com/user/self",
        following_api_pattern=os.getenv("DOUYIN_FOLLOWING_API_PATTERN", "following/list"),
        post_api_pattern=os.getenv("DOUYIN_POST_API_PATTERN", "aweme/v1/web/aweme/post/"),
        output_csv=output_dir / "douyin_hiatus_ranking.csv",
        all_videos_csv=output_dir / "douyin_all_videos.csv",
        video_duration_analysis_csv=output_dir / "douyin_video_duration_analysis.csv",
        video_duration_report_md=output_dir / "douyin_video_duration_report.md",
        cache_inventory_csv=output_dir / "douyin_cache_inventory.csv",
        followings_cache_json=state_dir / "douyin_followings_cache.json",
        followings_cache_dir=state_dir / "cache" / "followings",
        progress_json=state_dir / "douyin_progress.json",
        progress_dir=state_dir / "cache" / "progress",
        fetch_mode=fetch_mode,
        recent_video_limit=int(os.getenv("DOUYIN_RECENT_VIDEO_LIMIT", "10")),
        page_load_delay=float(os.getenv("DOUYIN_PAGE_LOAD_DELAY", "1.2")),
        packet_timeout=float(os.getenv("DOUYIN_PACKET_TIMEOUT", "4.0")),
        scroll_pause=float(os.getenv("DOUYIN_SCROLL_PAUSE", "0.6")),
        empty_round_limit=int(os.getenv("DOUYIN_EMPTY_ROUND_LIMIT", "2")),
        user_request_interval=float(os.getenv("DOUYIN_USER_REQUEST_INTERVAL", "1.2")),
        refresh_batch_size=int(os.getenv("DOUYIN_REFRESH_BATCH_SIZE", "10")),
        refresh_batch_cooldown=float(os.getenv("DOUYIN_REFRESH_BATCH_COOLDOWN", "20")),
        browser_restart_interval_users=int(
            os.getenv("DOUYIN_BROWSER_RESTART_INTERVAL_USERS", "20")
        ),
        video_page_load_delay=float(os.getenv("DOUYIN_VIDEO_PAGE_LOAD_DELAY", "0.8")),
        video_packet_timeout=float(os.getenv("DOUYIN_VIDEO_PACKET_TIMEOUT", "1.8")),
        video_scroll_pause=float(os.getenv("DOUYIN_VIDEO_SCROLL_PAUSE", "0.2")),
        video_empty_round_limit=int(os.getenv("DOUYIN_VIDEO_EMPTY_ROUND_LIMIT", "3")),
        video_scroll_steps_per_round=int(
            os.getenv("DOUYIN_VIDEO_SCROLL_STEPS_PER_ROUND", "2")
        ),
        video_scroll_distance=int(os.getenv("DOUYIN_VIDEO_SCROLL_DISTANCE", "1800")),
        video_page_retry_count=int(os.getenv("DOUYIN_VIDEO_PAGE_RETRY_COUNT", "3")),
        service_error_retry_wait=float(os.getenv("DOUYIN_SERVICE_ERROR_RETRY_WAIT", "12")),
        service_error_long_cooldown=float(
            os.getenv("DOUYIN_SERVICE_ERROR_LONG_COOLDOWN", "40")
        ),
        service_error_global_cooldown=float(
            os.getenv("DOUYIN_SERVICE_ERROR_GLOBAL_COOLDOWN", "90")
        ),
        rate_limit_retry_wait=float(os.getenv("DOUYIN_RATE_LIMIT_RETRY_WAIT", "20")),
        rate_limit_long_cooldown=float(
            os.getenv("DOUYIN_RATE_LIMIT_LONG_COOLDOWN", "90")
        ),
        rate_limit_global_cooldown=float(
            os.getenv("DOUYIN_RATE_LIMIT_GLOBAL_COOLDOWN", "180")
        ),
        progress_save_interval_users=int(
            os.getenv("DOUYIN_PROGRESS_SAVE_INTERVAL_USERS", "20")
        ),
        intermediate_upload_interval_users=int(
            os.getenv("DOUYIN_INTERMEDIATE_UPLOAD_INTERVAL_USERS", "30")
        ),
        followings_cache_max_age_hours=int(
            os.getenv("DOUYIN_FOLLOWINGS_CACHE_MAX_AGE_HOURS", "50")
        ),
        precise_cache_max_age_hours=int(os.getenv("DOUYIN_CACHE_MAX_AGE_HOURS", "432")),
        progress_trim_video_limit=int(os.getenv("DOUYIN_PROGRESS_TRIM_VIDEO_LIMIT", "50")),
        enable_video_duration_analysis=_get_bool(
            "DOUYIN_ENABLE_VIDEO_DURATION_ANALYSIS", True
        ),
        unfollow_interval_seconds=float(
            os.getenv("DOUYIN_UNFOLLOW_INTERVAL_SECONDS", "1.5")
        ),
        unfollow_batch_size=int(os.getenv("DOUYIN_UNFOLLOW_BATCH_SIZE", "8")),
        unfollow_batch_cooldown=float(os.getenv("DOUYIN_UNFOLLOW_BATCH_COOLDOWN", "8")),
        unfollow_restart_interval=int(os.getenv("DOUYIN_UNFOLLOW_RESTART_INTERVAL", "15")),
        unfollow_failure_cooldown=float(
            os.getenv("DOUYIN_UNFOLLOW_FAILURE_COOLDOWN", "15")
        ),
    )


def load_feishu_config() -> DouyinFeishuConfig:
    root_dir = _root_dir()
    runtime_dir = root_dir / "runtime"
    log_dir = runtime_dir / "logs"
    data_dir = root_dir / "data" / "douyin"
    output_dir = data_dir / "output"
    state_dir = data_dir / "state"
    history_dir = root_dir / "data" / "history"
    return DouyinFeishuConfig(
        root_dir=root_dir,
        log_dir=log_dir,
        app_id=os.getenv("DOUYIN_FEISHU_APP_ID", os.getenv("FEISHU_APP_ID", "")),
        app_secret=os.getenv("DOUYIN_FEISHU_APP_SECRET", os.getenv("FEISHU_APP_SECRET", "")),
        spreadsheet_token=os.getenv(
            "DOUYIN_FEISHU_SPREADSHEET_TOKEN",
            os.getenv("FEISHU_SPREADSHEET_TOKEN", ""),
        ),
        sheet_title=os.getenv("DOUYIN_FEISHU_SHEET_TITLE", "抖音数据表"),
        sheet_index=int(os.getenv("DOUYIN_FEISHU_SHEET_INDEX", "1")),
        file_hiatus=_resolve_path_env(
            "DOUYIN_FILE_HIATUS_PATH",
            output_dir / "douyin_hiatus_ranking.csv",
            must_exist=True,
        ),
        file_duration=_resolve_path_env(
            "DOUYIN_FILE_DURATION_PATH",
            output_dir / "douyin_video_duration_analysis.csv",
            must_exist=True,
        ),
        file_merged_output=_resolve_path_env(
            "DOUYIN_FILE_MERGED_OUTPUT_PATH",
            output_dir / "merged_douyin_data.csv",
        ),
        db_path=_resolve_path_env("DOUYIN_DB_PATH", history_dir / "douyin_history.db"),
        upload_state_json=Path(
            os.getenv(
                "DOUYIN_FEISHU_UPLOAD_STATE_PATH",
                str(state_dir / "douyin_feishu_upload_state.json"),
            )
        ),
        history_retention_days=int(os.getenv("DOUYIN_HISTORY_RETENTION_DAYS", "30")),
    )
