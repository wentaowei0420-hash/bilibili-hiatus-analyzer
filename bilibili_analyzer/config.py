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
class AnalyzerConfig:
    root_dir: Path
    log_dir: Path
    cookie: str
    followings_api: str
    relation_stat_api: str
    following_tags_api: str
    space_dynamic_api: str
    space_wbi_arc_search_api: str
    video_view_api: str
    nav_api: str
    output_csv: Path
    progress_json: Path
    all_videos_csv: Path
    video_duration_analysis_csv: Path
    video_duration_report_md: Path
    video_duration_progress_json: Path
    video_duration_progress_dir: Path
    analysis_mode: str
    enable_video_duration_analysis: bool
    enable_real_video_like_fetch: bool
    enable_cached_video_like_backfill: bool
    max_workers: int
    video_analysis_workers: int
    video_stat_workers: int
    video_stat_recent_limit: int
    video_stat_batch_size: int
    video_stat_batch_cooldown: int
    video_stat_max_requests_per_run: int
    request_delay: int
    max_request_delay: int
    network_retry_limit: int
    precise_cache_max_age_hours: int
    video_duration_cache_max_age_hours: int
    video_analysis_start_delay: int
    batch_size: int
    batch_cooldown: int
    long_rate_limit_cooldown: int
    rate_limit_retry_before_long_cooldown: int
    max_rate_limit_retries: int
    failed_retry_cooldown: int
    max_failed_retry_rounds: int
    max_dynamic_pages: int
    video_list_page_size: int
    video_analysis_batch_size: int
    video_analysis_batch_cooldown: int

    @property
    def headers(self) -> dict:
        return {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/118.0.0.0 Safari/537.36"
            ),
            "Accept": "application/json, text/plain, */*",
            "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
            "Origin": "https://www.bilibili.com",
            "Referer": "https://www.bilibili.com",
            "Connection": "keep-alive",
            "Cookie": self.cookie,
        }


@dataclass(frozen=True)
class FeishuConfig:
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


def load_analyzer_config() -> AnalyzerConfig:
    root_dir = _root_dir()
    runtime_dir = root_dir / "runtime"
    log_dir = runtime_dir / "logs"
    data_dir = root_dir / "data" / "bilibili"
    output_dir = data_dir / "output"
    state_dir = data_dir / "state"
    return AnalyzerConfig(
        root_dir=root_dir,
        log_dir=log_dir,
        cookie=os.getenv("BILIBILI_COOKIE", ""),
        followings_api="https://api.bilibili.com/x/relation/followings",
        relation_stat_api="https://api.bilibili.com/x/relation/stat",
        following_tags_api="https://api.bilibili.com/x/relation/tags",
        space_dynamic_api="https://api.bilibili.com/x/polymer/web-dynamic/v1/feed/space",
        space_wbi_arc_search_api="https://api.bilibili.com/x/space/wbi/arc/search",
        video_view_api="https://api.bilibili.com/x/web-interface/view",
        nav_api="https://api.bilibili.com/x/web-interface/nav",
        output_csv=output_dir / "bilibili_hiatus_ranking.csv",
        progress_json=state_dir / "bilibili_hiatus_progress.json",
        all_videos_csv=output_dir / "bilibili_all_videos.csv",
        video_duration_analysis_csv=output_dir / "bilibili_video_duration_analysis.csv",
        video_duration_report_md=output_dir / "bilibili_video_duration_report.md",
        video_duration_progress_json=state_dir / "bilibili_video_duration_progress.json",
        video_duration_progress_dir=state_dir / "cache" / "video_duration_progress",
        analysis_mode=os.getenv("ANALYSIS_MODE", "precise"),
        enable_video_duration_analysis=_get_bool("ENABLE_VIDEO_DURATION_ANALYSIS", True),
        enable_real_video_like_fetch=_get_bool("ENABLE_REAL_VIDEO_LIKE_FETCH", True),
        enable_cached_video_like_backfill=_get_bool("ENABLE_CACHED_VIDEO_LIKE_BACKFILL", False),
        max_workers=int(os.getenv("MAX_WORKERS", "3")),
        video_analysis_workers=int(os.getenv("VIDEO_ANALYSIS_WORKERS", "1")),
        video_stat_workers=int(os.getenv("VIDEO_STAT_WORKERS", "2")),
        video_stat_recent_limit=int(os.getenv("VIDEO_STAT_RECENT_LIMIT", "20")),
        video_stat_batch_size=int(os.getenv("VIDEO_STAT_BATCH_SIZE", "4")),
        video_stat_batch_cooldown=int(os.getenv("VIDEO_STAT_BATCH_COOLDOWN", "8")),
        video_stat_max_requests_per_run=int(os.getenv("VIDEO_STAT_MAX_REQUESTS_PER_RUN", "80")),
        request_delay=int(os.getenv("REQUEST_DELAY", "2")),
        max_request_delay=int(os.getenv("MAX_REQUEST_DELAY", "20")),
        network_retry_limit=int(os.getenv("NETWORK_RETRY_LIMIT", "3")),
        precise_cache_max_age_hours=int(os.getenv("PRECISE_CACHE_MAX_AGE_HOURS", "72")),
        video_duration_cache_max_age_hours=int(
            os.getenv("VIDEO_DURATION_CACHE_MAX_AGE_HOURS", "72")
        ),
        video_analysis_start_delay=int(os.getenv("VIDEO_ANALYSIS_START_DELAY", "8")),
        batch_size=int(os.getenv("BATCH_SIZE", "25")),
        batch_cooldown=int(os.getenv("BATCH_COOLDOWN", "10")),
        long_rate_limit_cooldown=int(os.getenv("LONG_RATE_LIMIT_COOLDOWN", "90")),
        rate_limit_retry_before_long_cooldown=int(
            os.getenv("RATE_LIMIT_RETRY_BEFORE_LONG_COOLDOWN", "3")
        ),
        max_rate_limit_retries=int(os.getenv("MAX_RATE_LIMIT_RETRIES", "5")),
        failed_retry_cooldown=int(os.getenv("FAILED_RETRY_COOLDOWN", "180")),
        max_failed_retry_rounds=int(os.getenv("MAX_FAILED_RETRY_ROUNDS", "2")),
        max_dynamic_pages=int(os.getenv("MAX_DYNAMIC_PAGES", "8")),
        video_list_page_size=int(os.getenv("VIDEO_LIST_PAGE_SIZE", "50")),
        video_analysis_batch_size=int(os.getenv("VIDEO_ANALYSIS_BATCH_SIZE", "5")),
        video_analysis_batch_cooldown=int(os.getenv("VIDEO_ANALYSIS_BATCH_COOLDOWN", "12")),
    )


def load_feishu_config() -> FeishuConfig:
    root_dir = _root_dir()
    runtime_dir = root_dir / "runtime"
    log_dir = runtime_dir / "logs"
    data_dir = root_dir / "data" / "bilibili"
    output_dir = data_dir / "output"
    state_dir = data_dir / "state"
    history_dir = root_dir / "data" / "history"
    return FeishuConfig(
        root_dir=root_dir,
        log_dir=log_dir,
        app_id=os.getenv("FEISHU_APP_ID", ""),
        app_secret=os.getenv("FEISHU_APP_SECRET", ""),
        spreadsheet_token=os.getenv("FEISHU_SPREADSHEET_TOKEN", ""),
        sheet_title=os.getenv("FEISHU_BILIBILI_SHEET_TITLE", "B站数据表"),
        sheet_index=int(os.getenv("FEISHU_BILIBILI_SHEET_INDEX", "0")),
        file_hiatus=_resolve_path_env(
            "FILE_HIATUS_PATH",
            output_dir / "bilibili_hiatus_ranking.csv",
            must_exist=True,
        ),
        file_duration=_resolve_path_env(
            "FILE_DURATION_PATH",
            output_dir / "bilibili_video_duration_analysis.csv",
            must_exist=True,
        ),
        file_merged_output=_resolve_path_env(
            "FILE_MERGED_OUTPUT_PATH",
            output_dir / "merged_bilibili_data.csv",
        ),
        db_path=_resolve_path_env("DB_PATH", history_dir / "bilibili_history.db"),
        upload_state_json=Path(
            os.getenv(
                "FEISHU_UPLOAD_STATE_PATH",
                str(state_dir / "bilibili_feishu_upload_state.json"),
            )
        ),
        history_retention_days=int(os.getenv("BILIBILI_HISTORY_RETENTION_DAYS", "30")),
    )
