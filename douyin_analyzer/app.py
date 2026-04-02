import traceback

from bilibili_analyzer.feishu_uploader import FeishuUploader
from bilibili_analyzer.logging_utils import setup_logging, smart_print as print

from .analyzer import DouyinHiatusAnalyzer
from .browser_client import DouyinBrowserClient
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config


def run_analysis(trigger_upload=True):
    config = load_analyzer_config()
    setup_logging(config.log_dir, "douyin_app")

    browser_client = DouyinBrowserClient(config)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(config, browser_client, cache_store)

    try:
        results = analyzer.analyze_hiatus()
    finally:
        browser_client.close()

    if trigger_upload and results is not None:
        print("\n" + "=" * 60)
        print("🚀 抖音数据抓取结束，正在自动执行飞书上传...")
        print("=" * 60)
        run_feishu_upload()

    return results


def run_feishu_upload():
    config = load_feishu_config()
    setup_logging(config.log_dir, "douyin_feishu_upload")
    uploader = FeishuUploader(config)
    uploader.run()


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
