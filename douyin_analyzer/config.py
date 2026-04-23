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
    browser_backend: str
    browser_name: str
    browser_binary_path: Path
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
    export_store_db: Path
    export_main_table: str
    export_analysis_table: str
    export_uid_analysis_table: str
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
    request_rate_limit_per_second: float
    retry_backoff_base_seconds: float
    retry_backoff_max_seconds: float
    conservative_mode_trigger_count: int
    conservative_mode_duration_seconds: float
    conservative_mode_rate_multiplier: float
    conservative_mode_fallback_max_ids: int
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
    video_detail_api_pattern: str
    video_browser_fallback_max_ids: int
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
    analysis_sheet_title: str
    analysis_sheet_index: int
    file_hiatus: Path
    file_duration: Path
    file_merged_output: Path
    export_store_db: Path
    export_main_table: str
    export_analysis_table: str
    export_uid_analysis_table: str
    db_path: Path
    upload_state_json: Path
    analysis_upload_state_json: Path
    history_retention_days: int


def _root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def _resolve_browser_binary(browser_name: str) -> Path:
    browser = (browser_name or "edge").strip().lower()
    candidates = {
        "edge": [
            Path(r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"),
            Path(r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"),
        ],
        "chrome": [
            Path(r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            Path(r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe"),
        ],
    }
    fallback_order = candidates.get(browser, []) + candidates["edge"] + candidates["chrome"]
    seen = set()
    for candidate in fallback_order:
        if str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if candidate.exists():
            return candidate
    return fallback_order[0] if fallback_order else Path()


def _default_browser_user_data_path(runtime_dir: Path, browser_name: str) -> Path:
    browser = (browser_name or "edge").strip().lower()
    preferred = runtime_dir / f"{browser}_data"
    legacy_chrome = runtime_dir / "chrome_data"
    if preferred.exists():
        return preferred
    if browser == "edge" and legacy_chrome.exists():
        return legacy_chrome
    return preferred


def load_analyzer_config(fetch_mode_override=None, recent_video_limit_override=None) -> DouyinAnalyzerConfig:
    root_dir = _root_dir()
    runtime_dir = root_dir / "runtime"
    log_dir = runtime_dir / "logs"
    data_dir = root_dir / "data" / "douyin"
    output_dir = data_dir / "output"
    state_dir = data_dir / "state"
    fetch_mode = (fetch_mode_override or os.getenv("DOUYIN_FETCH_MODE", "monitor")).strip().lower()
    try:
        recent_video_limit = (
            int(recent_video_limit_override)
            if recent_video_limit_override is not None
            else int(os.getenv("DOUYIN_RECENT_VIDEO_LIMIT", "10"))
        )
    except (TypeError, ValueError):
        recent_video_limit = 10
    if recent_video_limit <= 0:
        recent_video_limit = 1
    browser_name = os.getenv("DOUYIN_BROWSER_NAME", "edge").strip().lower() or "edge"
    return DouyinAnalyzerConfig(
        root_dir=root_dir,
        log_dir=log_dir,
        browser_backend=os.getenv("DOUYIN_BROWSER_BACKEND", "drission").strip().lower() or "drission",
        browser_name=browser_name,
        browser_binary_path=_resolve_path_env(
            "DOUYIN_BROWSER_BINARY_PATH",
            _resolve_browser_binary(browser_name),
        ),
        browser_user_data_path=Path(
            os.getenv(
                "DOUYIN_BROWSER_USER_DATA_PATH",
                str(_default_browser_user_data_path(runtime_dir, browser_name)),
            )
        ),
        home_url="https://www.douyin.com/",
        self_user_url="https://www.douyin.com/user/self",
        following_api_pattern=os.getenv(
            "DOUYIN_FOLLOWING_API_PATTERN",
            "following/list",
        ),
        post_api_pattern=os.getenv("DOUYIN_POST_API_PATTERN", "aweme/v1/web/aweme/post/"),
        output_csv=output_dir / "douyin_hiatus_ranking.csv",
        all_videos_csv=output_dir / "douyin_all_videos.csv",
        video_duration_analysis_csv=output_dir / "douyin_video_duration_analysis.csv",
        video_duration_report_md=output_dir / "douyin_video_duration_report.md",
        cache_inventory_csv=output_dir / "douyin_cache_inventory.csv",
        export_store_db=state_dir / "douyin_export_store.db",
        export_main_table="main_sheet_current",
        export_analysis_table="analysis_sheet_current",
        export_uid_analysis_table="uid_analysis_sheet_current",
        followings_cache_json=state_dir / "douyin_followings_cache.json",
        followings_cache_dir=state_dir / "cache" / "followings",
        progress_json=state_dir / "douyin_progress.json",
        progress_dir=state_dir / "cache" / "progress",
        fetch_mode=fetch_mode,
        recent_video_limit=recent_video_limit,
        page_load_delay=float(os.getenv("DOUYIN_PAGE_LOAD_DELAY", "1.2")),
        packet_timeout=float(os.getenv("DOUYIN_PACKET_TIMEOUT", "4.0")),
        scroll_pause=float(os.getenv("DOUYIN_SCROLL_PAUSE", "0.6")),
        empty_round_limit=int(os.getenv("DOUYIN_EMPTY_ROUND_LIMIT", "2")),
        user_request_interval=float(os.getenv("DOUYIN_USER_REQUEST_INTERVAL", "1.2")),
        request_rate_limit_per_second=float(
            os.getenv("DOUYIN_REQUEST_RATE_LIMIT_PER_SECOND", "2.0")
        ),
        retry_backoff_base_seconds=float(
            os.getenv("DOUYIN_RETRY_BACKOFF_BASE_SECONDS", "2.0")
        ),
        retry_backoff_max_seconds=float(
            os.getenv("DOUYIN_RETRY_BACKOFF_MAX_SECONDS", "30.0")
        ),
        conservative_mode_trigger_count=int(
            os.getenv("DOUYIN_CONSERVATIVE_MODE_TRIGGER_COUNT", "3")
        ),
        conservative_mode_duration_seconds=float(
            os.getenv("DOUYIN_CONSERVATIVE_MODE_DURATION_SECONDS", "300")
        ),
        conservative_mode_rate_multiplier=float(
            os.getenv("DOUYIN_CONSERVATIVE_MODE_RATE_MULTIPLIER", "2.0")
        ),
        conservative_mode_fallback_max_ids=int(
            os.getenv("DOUYIN_CONSERVATIVE_MODE_FALLBACK_MAX_IDS", "6")
        ),
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
        video_detail_api_pattern=os.getenv(
            "DOUYIN_VIDEO_DETAIL_API_PATTERN",
            "aweme/v1/web/aweme/detail/",
        ),
        video_browser_fallback_max_ids=int(
            os.getenv("DOUYIN_VIDEO_BROWSER_FALLBACK_MAX_IDS", "12")
        ),
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
        analysis_sheet_title=os.getenv("DOUYIN_FEISHU_ANALYSIS_SHEET_TITLE", "抖音分析表"),
        analysis_sheet_index=int(os.getenv("DOUYIN_FEISHU_ANALYSIS_SHEET_INDEX", "3")),
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
        export_store_db=state_dir / "douyin_export_store.db",
        export_main_table="main_sheet_current",
        export_analysis_table="analysis_sheet_current",
        export_uid_analysis_table="uid_analysis_sheet_current",
        db_path=_resolve_path_env("DOUYIN_DB_PATH", history_dir / "douyin_history.db"),
        upload_state_json=Path(
            os.getenv(
                "DOUYIN_FEISHU_UPLOAD_STATE_PATH",
                str(state_dir / "douyin_feishu_upload_state.json"),
            )
        ),
        analysis_upload_state_json=Path(
            os.getenv(
                "DOUYIN_FEISHU_ANALYSIS_UPLOAD_STATE_PATH",
                str(state_dir / "douyin_feishu_analysis_upload_state.json"),
            )
        ),
        history_retention_days=int(os.getenv("DOUYIN_HISTORY_RETENTION_DAYS", "30")),
    )
