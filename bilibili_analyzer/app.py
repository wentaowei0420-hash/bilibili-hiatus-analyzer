import traceback

from .analyzer import BilibiliHiatusAnalyzer
from .bilibili_api import BilibiliApi
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config
from .feishu_uploader import FeishuUploader
from .http_client import BilibiliHttpClient
from .logging_utils import setup_logging, smart_print as print


def run_analysis(trigger_upload=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "bilibili_app")

    client = BilibiliHttpClient(config)
    api = BilibiliApi(config, client)
    cache_store = CacheStore(config)
    analyzer = BilibiliHiatusAnalyzer(config, api, cache_store)
    results = analyzer.analyze_hiatus()

    if trigger_upload and results is not None:
        print("☁️  B站分析完成，开始同步飞书...")
        run_feishu_upload()

    return results


def run_feishu_upload(prune_missing=True):
    config = load_feishu_config()
    setup_logging(config.log_dir, "feishu_upload")
    uploader = FeishuUploader(config)
    uploader.run(prune_missing=prune_missing)


def main():
    try:
        run_analysis(trigger_upload=True)
    except KeyboardInterrupt:
        print("\n\n⚠️  程序被用户中断")
    except Exception as exc:
        print(f"\n❌ 程序运行出错: {exc}")
        traceback.print_exc()


def upload_main():
    try:
        run_feishu_upload()
    except KeyboardInterrupt:
        print("\n\n⚠️  程序被用户中断")
    except Exception as exc:
        print(f"\n❌ 程序运行出错: {exc}")
        traceback.print_exc()
