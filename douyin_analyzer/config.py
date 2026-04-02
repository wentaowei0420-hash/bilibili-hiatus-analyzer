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
    progress_json: Path
    page_load_delay: float
    packet_timeout: float
    scroll_pause: float
    empty_round_limit: int
    user_request_interval: float
    precise_cache_max_age_hours: int
    enable_video_duration_analysis: bool


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


def _root_dir() -> Path:
    return Path(__file__).resolve().parent.parent


def load_analyzer_config() -> DouyinAnalyzerConfig:
    root_dir = _root_dir()
    return DouyinAnalyzerConfig(
        root_dir=root_dir,
        log_dir=root_dir / "logs",
        browser_user_data_path=Path(
            os.getenv("DOUYIN_BROWSER_USER_DATA_PATH", str(root_dir / "chrome_data"))
        ),
        home_url="https://www.douyin.com/",
        self_user_url="https://www.douyin.com/user/self",
        following_api_pattern=os.getenv("DOUYIN_FOLLOWING_API_PATTERN", "following/list"),
        post_api_pattern=os.getenv("DOUYIN_POST_API_PATTERN", "aweme/v1/web/aweme/post/"),
        output_csv=root_dir / "douyin_hiatus_ranking.csv",
        all_videos_csv=root_dir / "douyin_all_videos.csv",
        video_duration_analysis_csv=root_dir / "douyin_video_duration_analysis.csv",
        video_duration_report_md=root_dir / "douyin_video_duration_report.md",
        progress_json=root_dir / "douyin_progress.json",
        page_load_delay=float(os.getenv("DOUYIN_PAGE_LOAD_DELAY", "2.0")),
        packet_timeout=float(os.getenv("DOUYIN_PACKET_TIMEOUT", "5.0")),
        scroll_pause=float(os.getenv("DOUYIN_SCROLL_PAUSE", "1.2")),
        empty_round_limit=int(os.getenv("DOUYIN_EMPTY_ROUND_LIMIT", "3")),
        user_request_interval=float(os.getenv("DOUYIN_USER_REQUEST_INTERVAL", "1.5")),
        precise_cache_max_age_hours=int(os.getenv("DOUYIN_CACHE_MAX_AGE_HOURS", "12")),
        enable_video_duration_analysis=_get_bool(
            "DOUYIN_ENABLE_VIDEO_DURATION_ANALYSIS", True
        ),
    )


def load_feishu_config() -> DouyinFeishuConfig:
    root_dir = _root_dir()
    return DouyinFeishuConfig(
        root_dir=root_dir,
        log_dir=root_dir / "logs",
        app_id=os.getenv("DOUYIN_FEISHU_APP_ID", os.getenv("FEISHU_APP_ID", "")),
        app_secret=os.getenv("DOUYIN_FEISHU_APP_SECRET", os.getenv("FEISHU_APP_SECRET", "")),
        spreadsheet_token=os.getenv(
            "DOUYIN_FEISHU_SPREADSHEET_TOKEN",
            os.getenv("FEISHU_SPREADSHEET_TOKEN", ""),
        ),
        sheet_title=os.getenv("DOUYIN_FEISHU_SHEET_TITLE", "抖音数据表"),
        sheet_index=int(os.getenv("DOUYIN_FEISHU_SHEET_INDEX", "1")),
        file_hiatus=Path(
            os.getenv("DOUYIN_FILE_HIATUS_PATH", str(root_dir / "douyin_hiatus_ranking.csv"))
        ),
        file_duration=Path(
            os.getenv(
                "DOUYIN_FILE_DURATION_PATH",
                str(root_dir / "douyin_video_duration_analysis.csv"),
            )
        ),
        file_merged_output=Path(
            os.getenv("DOUYIN_FILE_MERGED_OUTPUT_PATH", str(root_dir / "merged_douyin_data.csv"))
        ),
        db_path=Path(os.getenv("DOUYIN_DB_PATH", str(root_dir / "douyin_history.db"))),
    )
