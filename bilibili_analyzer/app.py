import traceback

from .analyzer import BilibiliHiatusAnalyzer
from .bilibili_api import BilibiliApi
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config
from .feishu_uploader import FeishuUploader
from .http_client import BilibiliHttpClient
from .logging_utils import create_summary_panel, get_console, setup_logging
from .rating.store import rating_store_db_path


def run_analysis(trigger_upload=True, max_followings=None, reporter=None, export_outputs=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "bilibili_app")
    export_outputs = bool(export_outputs or trigger_upload)

    client = BilibiliHttpClient(config)
    api = BilibiliApi(config, client)
    cache_store = CacheStore(config)
    analyzer = BilibiliHiatusAnalyzer(
        config,
        api,
        cache_store,
        max_followings=max_followings,
        reporter=reporter,
        export_outputs=export_outputs,
    )
    results = analyzer.analyze_hiatus()

    if trigger_upload and results is not None:
        get_console().print(
            create_summary_panel(
                "Bilibili Main Sheet Sync",
                ["Analysis finished. Main data sheet will be synced now."],
                border_style="cyan",
            )
        )
        run_feishu_upload(prune_missing=True)

    return results


def run_feishu_upload(prune_missing=True):
    config = load_feishu_config()
    setup_logging(config.log_dir, "bilibili_feishu_upload")
    uploader = FeishuUploader(config)
    uploader.run(prune_missing=prune_missing)
    return True


def main():
    try:
        run_analysis(trigger_upload=True)
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Execution was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Bilibili Error", [str(exc)], border_style="red"))
        traceback.print_exc()


def upload_main():
    try:
        run_feishu_upload()
    except KeyboardInterrupt:
        get_console().print(create_summary_panel("Interrupted", ["Upload was cancelled by user."], border_style="yellow"))
    except Exception as exc:
        get_console().print(create_summary_panel("Upload Error", [str(exc)], border_style="red"))
        traceback.print_exc()


def run_score_videos_from_cache():
    config = load_analyzer_config()
    setup_logging(config.log_dir, "bilibili_video_scoring")
    from .rating.video_scoring import run_bilibili_video_scoring

    return run_bilibili_video_scoring(config)


def run_score_creators_from_cache():
    config = load_analyzer_config()
    setup_logging(config.log_dir, "bilibili_creator_scoring")
    if _sqlite_table_count(rating_store_db_path(config), "video_score_current") <= 0:
        from .rating.video_scoring import run_bilibili_video_scoring

        run_bilibili_video_scoring(config)
    from .rating.creator_scoring import run_bilibili_creator_scoring

    return run_bilibili_creator_scoring(config)


def _sqlite_table_count(db_path, table_name):
    import sqlite3
    from pathlib import Path

    db_path = Path(db_path)
    if not db_path.exists():
        return 0
    with sqlite3.connect(db_path) as conn:
        exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        if not exists:
            return 0
        return int(conn.execute(f'SELECT COUNT(*) FROM "{table_name}"').fetchone()[0] or 0)
