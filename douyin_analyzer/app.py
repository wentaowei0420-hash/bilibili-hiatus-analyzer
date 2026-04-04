import traceback
from pathlib import Path

from bilibili_analyzer.feishu_uploader import FeishuUploader
from bilibili_analyzer.logging_utils import setup_logging, smart_print as print

from .analyzer import DouyinHiatusAnalyzer
from .browser_client import DouyinBrowserClient
from .cache import CacheStore
from .config import load_analyzer_config, load_feishu_config


def load_unfollow_targets(list_path):
    path = Path(list_path)
    if not path.exists():
        print(f"⚠️  未找到取消关注名单文件: {path}")
        return []

    targets = []
    with path.open("r", encoding="utf-8") as unfollow_file:
        for line in unfollow_file:
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            targets.append(text)
    return targets


def remove_unfollow_target(list_path, homepage):
    path = Path(list_path)
    if not path.exists():
        return

    normalized_homepage = DouyinBrowserClient.normalize_homepage_url(homepage)
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except Exception:
        return

    updated_lines = []
    removed = False
    for line in lines:
        raw = line.strip()
        if not raw or raw.startswith("#"):
            updated_lines.append(line)
            continue

        normalized_line = DouyinBrowserClient.normalize_homepage_url(raw)
        if not removed and normalized_line == normalized_homepage:
            removed = True
            continue
        updated_lines.append(line)

    if removed:
        path.write_text("\n".join(updated_lines) + ("\n" if updated_lines else ""), encoding="utf-8")
        print(f"-> 已从取消关注名单中移除: {homepage}")


def run_partial_feishu_upload(processed_count):
    run_feishu_upload(prune_missing=False)


def run_cached_feishu_preupload(fetch_mode_override=None):
    config = load_analyzer_config(fetch_mode_override=fetch_mode_override)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client=None,
        cache_store=cache_store,
        upload_callback=None,
    )
    if not analyzer.export_cached_snapshot():
        print("ℹ️  当前没有可用于预上传的抖音缓存数据。")
        return False

    print("☁️  已先导出本地抖音缓存快照，准备优先同步到飞书...")
    run_feishu_upload(prune_missing=False)
    return True


def run_analysis(trigger_upload=True, fetch_mode_override=None):
    config = load_analyzer_config(fetch_mode_override=fetch_mode_override)
    setup_logging(config.log_dir, "douyin_app")

    browser_client = DouyinBrowserClient(config)
    cache_store = CacheStore(config)
    analyzer = DouyinHiatusAnalyzer(
        config,
        browser_client,
        cache_store,
        upload_callback=run_partial_feishu_upload if trigger_upload else None,
    )

    if trigger_upload:
        try:
            run_cached_feishu_preupload(fetch_mode_override=fetch_mode_override)
        except Exception as exc:
            print(f"⚠️  启动前缓存快照上传失败，本轮分析仍会继续: {exc}")

    try:
        results = analyzer.analyze_hiatus()
    finally:
        browser_client.close()

    if trigger_upload and results is not None:
        print("☁️  抖音分析完成，开始同步飞书...")
        run_feishu_upload(prune_missing=True)

    return results


def run_feishu_upload(prune_missing=True):
    config = load_feishu_config()
    setup_logging(config.log_dir, "douyin_feishu_upload")
    uploader = FeishuUploader(config)
    uploader.run(prune_missing=prune_missing)


def run_unfollow(list_path):
    config = load_analyzer_config(fetch_mode_override="counts")
    setup_logging(config.log_dir, "douyin_unfollow")

    targets = load_unfollow_targets(list_path)
    if not targets:
        print("ℹ️  取消关注名单为空，本次不执行任何操作。")
        return []

    browser_client = DouyinBrowserClient(config)
    try:
        browser_client.ensure_login()
        results = browser_client.unfollow_users_by_homepages(
            targets,
            on_unfollowed=lambda homepage: remove_unfollow_target(list_path, homepage),
        )
    finally:
        browser_client.close()

    unfollowed = sum(1 for item in results if item.get("status") == "unfollowed")
    skipped = sum(1 for item in results if item.get("status") == "skipped")
    failed = sum(1 for item in results if item.get("status") not in {"unfollowed", "skipped"})

    print("\n" + "=" * 60)
    print("抖音取消关注执行完成")
    print("=" * 60)
    print(f"总目标数: {len(results)}")
    print(f"成功取消关注: {unfollowed}")
    print(f"原本未关注/已跳过: {skipped}")
    print(f"失败或未识别: {failed}")
    return results


def main(fetch_mode_override=None):
    try:
        run_analysis(trigger_upload=True, fetch_mode_override=fetch_mode_override)
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
