import asyncio
import argparse
import csv
import json
import logging
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from config import ConfigLoader
from auth import CookieManager
from storage import Database, FileManager
from control import QueueManager, RateLimiter, RetryHandler
from core import DouyinAPIClient, URLParser, DownloaderFactory
from cli.progress_display import ProgressDisplay
from utils.logger import setup_logger, set_console_log_level

logger = setup_logger('CLI')
display = ProgressDisplay()

HIGH_LIKE_CSV = "douyin_cached_high_like_videos.csv"
HIGH_LIKE_FAILED_CSV = "douyin_cached_high_like_videos_failed.csv"


def _as_bool(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return bool(value)


def _dedupe_urls(urls: list[str]) -> list[str]:
    deduped = []
    seen = set()
    for url in urls:
        normalized = str(url or "").strip()
        if not normalized or normalized in seen:
            continue
        seen.add(normalized)
        deduped.append(normalized)
    return deduped


def _load_urls_from_file(file_path: str) -> list[str]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"URL file not found: {file_path}")

    urls = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        urls.append(line)
    return _dedupe_urls(urls)


def _csv_value(row: dict[str, Any], *names: str) -> str:
    for name in names:
        value = row.get(name)
        if value is not None and str(value).strip():
            return str(value).strip()
    return ""


def _load_high_like_video_rows(file_path: str) -> list[dict[str, str]]:
    path = Path(file_path)
    if not path.exists():
        raise FileNotFoundError(f"High-like video CSV not found: {file_path}")

    rows: list[dict[str, str]] = []
    seen = set()
    with path.open("r", encoding="utf-8-sig", newline="") as csv_file:
        reader = csv.DictReader(csv_file)
        for raw_row in reader:
            row = {str(k or "").strip(): str(v or "").strip() for k, v in raw_row.items()}
            aweme_id = _csv_value(row, "视频ID", "aweme_id", "aweme_id_str", "id")
            video_url = _csv_value(row, "视频链接", "url", "link", "video_url")
            if not video_url and aweme_id:
                video_url = f"https://www.douyin.com/video/{aweme_id}"

            key = aweme_id or video_url
            if not key or key in seen:
                continue

            seen.add(key)
            row["aweme_id"] = aweme_id
            row["video_url"] = video_url
            rows.append(row)

    return rows


def _append_failed_high_like_rows(file_path: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return

    path = Path(file_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    preferred_fields = [
        "failed_time",
        "failure_reason",
        "UP主",
        "视频ID",
        "视频标题",
        "视频链接",
        "点赞数",
        "aweme_id",
        "video_url",
    ]
    extra_fields = []
    for row in rows:
        for key in row.keys():
            if key not in preferred_fields and key not in extra_fields:
                extra_fields.append(key)
    fieldnames = preferred_fields + extra_fields

    write_header = not path.exists() or path.stat().st_size == 0
    with path.open("a", encoding="utf-8-sig", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=fieldnames, extrasaction="ignore")
        if write_header:
            writer.writeheader()
        writer.writerows(rows)


async def download_high_like_csv(
    csv_path: str,
    failed_csv_path: str,
    config: ConfigLoader,
    cookie_manager: CookieManager,
    database: Database = None,
    progress_reporter: ProgressDisplay = None,
):
    rows = _load_high_like_video_rows(csv_path)
    display.print_info(f"Found {len(rows)} cached high-like video(s) to process")

    all_results = []
    failed_rows: list[dict[str, Any]] = []
    skipped_by_db = 0

    for index, row in enumerate(rows, 1):
        url = row.get("video_url") or row.get("视频链接")
        aweme_id = row.get("aweme_id") or row.get("视频ID")
        if not url:
            failed_rows.append(
                {
                    **row,
                    "failed_time": datetime.now().isoformat(timespec="seconds"),
                    "failure_reason": "missing video url",
                }
            )
            continue

        if database is not None and aweme_id and await database.is_downloaded(aweme_id):
            skipped_by_db += 1
            display.print_info(f"Skip downloaded video from database: {aweme_id}")
            continue

        display.start_url(index, len(rows), url)
        result = await download_url(
            url,
            config,
            cookie_manager,
            database,
            progress_reporter=progress_reporter,
        )

        if result and (result.success > 0 or result.skipped > 0):
            all_results.append(result)
            display.complete_url(result)
            continue

        reason = "download returned no result"
        if result:
            reason = (
                f"success={result.success}, failed={result.failed}, skipped={result.skipped}"
            )
        failed_rows.append(
            {
                **row,
                "failed_time": datetime.now().isoformat(timespec="seconds"),
                "failure_reason": reason,
            }
        )
        display.fail_url(reason)

    _append_failed_high_like_rows(failed_csv_path, failed_rows)
    return all_results, failed_rows, skipped_by_db


async def download_url(
    url: str,
    config: ConfigLoader,
    cookie_manager: CookieManager,
    database: Database = None,
    progress_reporter: ProgressDisplay = None,
):
    if progress_reporter:
        progress_reporter.advance_step("初始化", "创建下载组件")
    file_manager = FileManager(config.get('path'))
    rate_limiter = RateLimiter(max_per_second=float(config.get('rate_limit', 2) or 2))
    retry_handler = RetryHandler(max_retries=config.get('retry_times', 3))
    queue_manager = QueueManager(max_workers=int(config.get('thread', 5) or 5))

    original_url = url

    async with DouyinAPIClient(
        cookie_manager.get_cookies(),
        proxy=config.get("proxy"),
    ) as api_client:
        if progress_reporter:
            progress_reporter.advance_step("解析链接", "检查短链并解析 URL")
        if url.startswith('https://v.douyin.com'):
            resolved_url = await api_client.resolve_short_url(url)
            if resolved_url:
                url = resolved_url
            else:
                if progress_reporter:
                    progress_reporter.update_step("解析链接", "短链解析失败")
                display.print_error(f"Failed to resolve short URL: {url}")
                return None

        parsed = URLParser.parse(url)
        if not parsed:
            if progress_reporter:
                progress_reporter.update_step("解析链接", "URL 解析失败")
            display.print_error(f"Failed to parse URL: {url}")
            return None

        if not progress_reporter:
            display.print_info(f"URL type: {parsed['type']}")
        if progress_reporter:
            progress_reporter.advance_step("创建下载器", f"URL 类型: {parsed['type']}")

        downloader = DownloaderFactory.create(
            parsed['type'],
            config,
            api_client,
            file_manager,
            cookie_manager,
            database,
            rate_limiter,
            retry_handler,
            queue_manager,
            progress_reporter=progress_reporter,
        )

        if not downloader:
            if progress_reporter:
                progress_reporter.update_step("创建下载器", "未找到匹配下载器")
            display.print_error(f"No downloader found for type: {parsed['type']}")
            return None

        if progress_reporter:
            progress_reporter.advance_step("执行下载", "开始拉取与下载资源")
        result = await downloader.download(parsed)

        if progress_reporter:
            progress_reporter.advance_step(
                "记录历史",
                "写入数据库历史" if (result and database) else "数据库未启用，跳过",
            )
        if result and database:
            safe_config = {
                k: v for k, v in config.config.items()
                if k not in ("cookies", "cookie", "transcript")
            }
            await database.add_history({
                'url': original_url,
                'url_type': parsed['type'],
                'total_count': result.total,
                'success_count': result.success,
                'config': json.dumps(safe_config, ensure_ascii=False),
            })

        if progress_reporter:
            if result:
                progress_reporter.advance_step(
                    "收尾",
                    f"成功 {result.success} / 失败 {result.failed} / 跳过 {result.skipped}",
                )
            else:
                progress_reporter.advance_step("收尾", "无可统计结果")

        return result


async def main_async(args):
    display.show_banner()

    if args.config:
        config_path = args.config
    else:
        config_path = 'config.yml'

    if not Path(config_path).exists():
        display.print_error(f"Config file not found: {config_path}")
        return

    config = ConfigLoader(config_path)

    cli_urls = []
    if args.url:
        cli_urls = args.url if isinstance(args.url, list) else [args.url]

    if getattr(args, "url_file", None):
        file_urls = _load_urls_from_file(args.url_file)
        merged_urls = _dedupe_urls(file_urls + cli_urls)
        config.update(link=merged_urls)
        config.save(config_path)
        display.print_success(
            f"Imported {len(file_urls)} URL(s) from file into config: {args.url_file}"
        )
    elif cli_urls:
        merged_urls = _dedupe_urls(config.get('link', []) + cli_urls)
        config.update(link=merged_urls)
        config.save(config_path)

    if getattr(args, "download_high_like", False):
        high_like_csv = getattr(args, "high_like_csv", None) or HIGH_LIKE_CSV
        high_like_rows = _load_high_like_video_rows(high_like_csv)
        config.update(
            link=[row["video_url"] for row in high_like_rows if row.get("video_url")]
        )

    if args.path:
        config.update(path=args.path)

    if args.thread:
        config.update(thread=args.thread)

    if not config.validate():
        display.print_error("Invalid configuration: missing required fields")
        return

    cookies = config.get_cookies()
    cookie_manager = CookieManager()
    cookie_manager.set_cookies(cookies)

    if not cookie_manager.validate_cookies():
        display.print_warning("Cookies may be invalid or incomplete")

    database = None
    if config.get('database'):
        db_path = config.get('database_path', 'dy_downloader.db') or 'dy_downloader.db'
        database = Database(db_path=str(db_path))
        await database.initialize()
        display.print_success("Database initialized")

    urls = config.get_links()
    if getattr(args, "download_high_like", False):
        display.print_info(f"Found {len(urls)} cached high-like URL(s) to process")
    else:
        display.print_info(f"Found {len(urls)} URL(s) to process")

    all_results = []
    failed_high_like_rows = []
    skipped_by_db = 0
    progress_config = config.get("progress", {}) or {}
    quiet_by_config = _as_bool(progress_config.get("quiet_logs", True), default=True)
    quiet_progress_logs = quiet_by_config and not (args.verbose or args.show_warnings)
    if quiet_progress_logs:
        # Progress 运行期间若有大量错误日志会触发 rich 反复重绘，导致屏幕出现重复块。
        # 默认静默控制台日志，下载完成后再恢复。
        set_console_log_level(logging.CRITICAL)

    if getattr(args, "download_high_like", False):
        display.start_download_session(len(urls))
        try:
            all_results, failed_high_like_rows, skipped_by_db = await download_high_like_csv(
                getattr(args, "high_like_csv", None) or HIGH_LIKE_CSV,
                getattr(args, "high_like_failed_csv", None) or HIGH_LIKE_FAILED_CSV,
                config,
                cookie_manager,
                database,
                progress_reporter=display,
            )
        finally:
            display.stop_download_session()
            if database is not None:
                await database.close()
            if quiet_progress_logs:
                set_console_log_level(logging.ERROR)

        from core.downloader_base import DownloadResult
        total_result = DownloadResult()
        total_result.total = len(urls)
        total_result.failed = len(failed_high_like_rows)
        total_result.skipped = skipped_by_db
        for r in all_results:
            total_result.success += r.success
            total_result.skipped += r.skipped

        display.print_success("\n=== Overall Summary ===")
        display.show_result(total_result)
        if failed_high_like_rows:
            failed_csv = getattr(args, "high_like_failed_csv", None) or HIGH_LIKE_FAILED_CSV
            display.print_warning(f"Failed rows written to: {failed_csv}")
        return

    display.start_download_session(len(urls))
    try:
        for i, url in enumerate(urls, 1):
            display.start_url(i, len(urls), url)

            result = await download_url(
                url,
                config,
                cookie_manager,
                database,
                progress_reporter=display,
            )
            if result:
                all_results.append(result)
                display.complete_url(result)
            else:
                display.fail_url("下载失败或链接无效")
    finally:
        display.stop_download_session()
        if database is not None:
            await database.close()
        if quiet_progress_logs:
            set_console_log_level(logging.ERROR)

    if all_results:
        from core.downloader_base import DownloadResult
        total_result = DownloadResult()
        for r in all_results:
            total_result.total += r.total
            total_result.success += r.success
            total_result.failed += r.failed
            total_result.skipped += r.skipped

        display.print_success("\n=== Overall Summary ===")
        display.show_result(total_result)


def main():
    parser = argparse.ArgumentParser(description='Douyin Downloader - 抖音批量下载工具')
    parser.add_argument('-u', '--url', action='append', help='Download URL(s)')
    parser.add_argument('--url-file', help='Read URLs from a txt file and write them into config')
    parser.add_argument(
        '--download-high-like',
        action='store_true',
        help=f'Download videos from {HIGH_LIKE_CSV}',
    )
    parser.add_argument(
        '--high-like-csv',
        default=HIGH_LIKE_CSV,
        help=f'Cached high-like video CSV path (default: {HIGH_LIKE_CSV})',
    )
    parser.add_argument(
        '--high-like-failed-csv',
        default=HIGH_LIKE_FAILED_CSV,
        help=f'Failed high-like video CSV path (default: {HIGH_LIKE_FAILED_CSV})',
    )
    parser.add_argument('-c', '--config', help='Config file path (default: config.yml)')
    parser.add_argument('-p', '--path', help='Save path')
    parser.add_argument('-t', '--thread', type=int, help='Thread count')
    parser.add_argument('--show-warnings', action='store_true', help='Show warning logs in console')
    parser.add_argument('-v', '--verbose', action='store_true', help='Enable verbose console logs')
    try:
        from __init__ import __version__
    except ImportError:
        __version__ = "2.0.0"
    parser.add_argument('--version', action='version', version=__version__)

    args = parser.parse_args()

    if args.verbose:
        set_console_log_level(logging.INFO)
    elif args.show_warnings:
        set_console_log_level(logging.WARNING)
    else:
        set_console_log_level(logging.ERROR)

    try:
        asyncio.run(main_async(args))
    except KeyboardInterrupt:
        display.print_warning("\nDownload interrupted by user")
        sys.exit(0)
    except Exception as e:
        display.print_error(f"Fatal error: {e}")
        logger.exception("Fatal error occurred")
        sys.exit(1)


if __name__ == '__main__':
    main()
