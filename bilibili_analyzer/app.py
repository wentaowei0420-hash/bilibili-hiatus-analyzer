import traceback

from .analyzer import BilibiliHiatusAnalyzer
from .bilibili_api import BilibiliApi
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config
from .feishu_uploader import FeishuUploader
from .http_client import BilibiliHttpClient
from .logging_utils import create_summary_panel, get_console, setup_logging


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
