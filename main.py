import os
from pathlib import Path

from bilibili_analyzer.logging_utils import create_summary_panel, create_table, get_console
from douyin_analyzer.config import load_analyzer_config as load_douyin_config


DOUYIN_FETCH_MODE = "monitor"
# 可选值: "counts", "monitor", "delta", "full"

ROOT_DIR = Path(__file__).resolve().parent
DOUYIN_UNFOLLOW_LIST_PATH = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"
BILIBILI_UID_FETCH_LIST_PATH = ROOT_DIR / "data" / "bilibili" / "ops" / "bilibili_uid_fetch_list.txt"
DOUYIN_UID_FETCH_LIST_PATH = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_uid_fetch_list.txt"


def get_douyin_runtime_labels():
    douyin_config = load_douyin_config(fetch_mode_override=DOUYIN_FETCH_MODE)
    backend = (douyin_config.browser_backend or "drission").strip().lower()
    backend_label = "Playwright" if backend == "playwright" else "DrissionPage"
    browser_binary = (
        douyin_config.browser_binary_path.name
        if douyin_config.browser_binary_path
        else "auto"
    )
    browser_label = f"{douyin_config.browser_name} ({browser_binary})"
    return douyin_config, backend_label, browser_label


def show_platform_menu():
    console = get_console()
    douyin_config, backend_label, browser_label = get_douyin_runtime_labels()
    console.print()
    console.print(
        create_summary_panel(
            "运行入口",
            [
                "统一入口支持 B站、抖音，以及飞书同步分离控制。",
                f"当前抖音抓取模式: {DOUYIN_FETCH_MODE}",
                f"当前抖音浏览器后端: {backend_label} [{douyin_config.browser_backend}]",
                f"当前抖音浏览器目标: {browser_label}",
                "模式 1/2/3 只同步前面的主数据表。",
                "模式 5/6 只同步后面的分析表。",
                "模式 7 从本地缓存导出抖音高赞视频 CSV。",
            ],
            border_style="cyan",
        )
    )

    table = create_table(
        "平台选择",
        [
            ("编号", "right", "bold"),
            ("操作", "left"),
            ("目标表", "left"),
            ("说明", "left"),
        ],
    )
    table.add_row("1", "B站 + 抖音", "主数据表", "依次执行两个平台的普通流程")
    table.add_row("2", "仅 B站", "B站数据表", "执行 B站普通流程")
    table.add_row("3", "仅 抖音", "抖音数据表", "执行抖音普通流程")
    table.add_row("4", "抖音取消关注", "-", "读取 txt 名单并取消关注")
    table.add_row("5", "B站 UID 全量抓取", "B站分析表", "按 txt 名单抓指定 UID，并只上传 UID 分析结果")
    table.add_row("6", "抖音 UID 全量抓取", "抖音分析表", "按 txt 名单抓指定 UID，并只上传 UID 分析结果")
    table.add_row("7", "导出抖音高赞视频", "本地 CSV", "从缓存筛选点赞数大于 10000 的唯一视频")
    console.print(table)
    console.print()


def show_action_menu():
    console = get_console()
    table = create_table(
        "动作选择",
        [
            ("编号", "right", "bold"),
            ("动作", "left"),
            ("说明", "left"),
        ],
    )
    table.add_row("1", "仅抓取", "只更新本地结果，不上传飞书")
    table.add_row("2", "抓取并上传", "先抓取，再同步飞书主数据表")
    table.add_row("3", "仅上传", "不重新抓取，只上传本地已有主数据结果")
    console.print(table)
    console.print()


def show_douyin_backend_menu():
    console = get_console()
    table = create_table(
        "抖音浏览器后端选择",
        [
            ("编号", "right", "bold"),
            ("后端", "left"),
            ("说明", "left"),
        ],
    )
    table.add_row("1", "DrissionPage", "兼容当前项目默认链路，适合日常稳定使用")
    table.add_row("2", "Playwright", "页面控制和兜底能力更强，适合需要增强抓取时使用")
    console.print(table)
    console.print()


def show_run_panel(title: str, lines, border_style: str = "green"):
    console = get_console()
    console.print()
    console.print(create_summary_panel(title, lines, border_style=border_style))
    console.print()


def prompt_platform_choice():
    show_platform_menu()
    while True:
        choice = input("请输入平台编号 (1/2/3/4/5/6/7): ").strip()
        if choice in {"1", "2", "3", "4", "5", "6", "7"}:
            return choice
        get_console().print(
            create_summary_panel(
                "输入无效",
                ["请输入 1、2、3、4、5、6 或 7。"],
                border_style="red",
            )
        )


def prompt_action_choice():
    show_action_menu()
    while True:
        choice = input("请输入动作编号 (1/2/3): ").strip()
        if choice in {"1", "2", "3"}:
            return choice
        get_console().print(
            create_summary_panel(
                "输入无效",
                ["请输入 1、2 或 3。"],
                border_style="red",
            )
        )


def prompt_douyin_backend_choice():
    current_backend = os.getenv("DOUYIN_BROWSER_BACKEND", "").strip().lower() or "drission"
    show_douyin_backend_menu()
    prompt = f"请输入抖音浏览器后端编号 (1/2，直接回车保持当前: {current_backend}): "
    while True:
        choice = input(prompt).strip()
        if choice == "":
            return current_backend
        if choice == "1":
            return "drission"
        if choice == "2":
            return "playwright"
        get_console().print(
            create_summary_panel(
                "输入无效",
                ["请输入 1、2，或直接回车保持当前后端。"],
                border_style="red",
            )
        )


def prompt_uid_fetch_limit():
    prompt = "请输入本次要抓取的 UID 数量（输入数字，或输入 all/直接回车抓取全部）: "
    while True:
        raw_value = input(prompt).strip().lower()
        if raw_value in {"", "all", "全部"}:
            return None
        try:
            limit = int(raw_value)
        except ValueError:
            get_console().print(
                create_summary_panel(
                    "输入无效",
                    ["请输入正整数，或输入 all/直接回车表示抓取全部。"],
                    border_style="red",
                )
            )
            continue
        if limit > 0:
            return limit
        get_console().print(
            create_summary_panel(
                "输入无效",
                ["抓取数量必须大于 0。"],
                border_style="red",
            )
        )


def apply_douyin_runtime_backend(backend: str):
    normalized = (backend or "drission").strip().lower() or "drission"
    os.environ["DOUYIN_BROWSER_BACKEND"] = normalized
    _, backend_label, browser_label = get_douyin_runtime_labels()
    show_run_panel(
        "已选择抖音浏览器后端",
        [
            f"后端: {backend_label} [{normalized}]",
            f"浏览器目标: {browser_label}",
            "本次选择只影响当前运行，不会修改 .env 默认配置。",
        ],
        border_style="cyan",
    )


def run_bilibili(action):
    from bilibili_analyzer.app import run_analysis, run_feishu_upload

    if action == "1":
        return run_analysis(trigger_upload=False)
    if action == "2":
        return run_analysis(trigger_upload=True)
    return run_feishu_upload()


def run_douyin(action):
    from douyin_analyzer.app import run_analysis, run_feishu_upload

    if action == "1":
        return run_analysis(trigger_upload=False, fetch_mode_override=DOUYIN_FETCH_MODE)
    if action == "2":
        return run_analysis(trigger_upload=True, fetch_mode_override=DOUYIN_FETCH_MODE)
    return run_feishu_upload()


def run_douyin_unfollow():
    from douyin_analyzer.app import run_unfollow

    run_unfollow(DOUYIN_UNFOLLOW_LIST_PATH)


def run_bilibili_uid_fetch(max_targets=None):
    from bilibili_analyzer.app import run_fetch_uid_videos

    run_fetch_uid_videos(BILIBILI_UID_FETCH_LIST_PATH, max_targets=max_targets)


def run_douyin_uid_fetch(max_targets=None):
    from douyin_analyzer.app import run_fetch_uid_videos

    run_fetch_uid_videos(DOUYIN_UID_FETCH_LIST_PATH, max_targets=max_targets)


def run_douyin_high_like_export():
    from douyin_analyzer.app import run_export_high_like_videos_from_cache

    run_export_high_like_videos_from_cache(threshold=10000)


def main():
    platform_choice = prompt_platform_choice()

    if platform_choice in {"1", "3", "4", "6"}:
        apply_douyin_runtime_backend(prompt_douyin_backend_choice())

    _, backend_label, browser_label = get_douyin_runtime_labels()

    if platform_choice == "4":
        show_run_panel(
            "执行抖音取消关注",
            [
                f"名单文件: {DOUYIN_UNFOLLOW_LIST_PATH}",
                f"浏览器后端: {backend_label}",
                f"浏览器目标: {browser_label}",
                "程序会逐个打开主页，若当前已关注则尝试取消关注。",
            ],
            border_style="yellow",
        )
        run_douyin_unfollow()
        return

    if platform_choice == "5":
        uid_fetch_limit = prompt_uid_fetch_limit()
        show_run_panel(
            "执行 B站 UID 全量抓取",
            [
                f"名单文件: {BILIBILI_UID_FETCH_LIST_PATH}",
                f"本次抓取数量: {'全部' if uid_fetch_limit is None else uid_fetch_limit}",
                "该模式会在本地生成 UID 视频明细和分析结果。",
                "抓取结束后，只同步 B站分析表。",
            ],
            border_style="yellow",
        )
        run_bilibili_uid_fetch(max_targets=uid_fetch_limit)
        return

    if platform_choice == "6":
        uid_fetch_limit = prompt_uid_fetch_limit()
        show_run_panel(
            "执行抖音 UID 全量抓取",
            [
                f"名单文件: {DOUYIN_UID_FETCH_LIST_PATH}",
                f"本次抓取数量: {'全部' if uid_fetch_limit is None else uid_fetch_limit}",
                f"浏览器后端: {backend_label}",
                f"浏览器目标: {browser_label}",
                "该模式会在本地生成 UID 视频明细和分析结果。",
                "抓取结束后，只同步抖音分析表。",
            ],
            border_style="yellow",
        )
        run_douyin_uid_fetch(max_targets=uid_fetch_limit)
        return

    if platform_choice == "7":
        show_run_panel(
            "导出抖音缓存高赞视频",
            [
                "数据来源: 本地抖音进度缓存",
                "筛选条件: 点赞数 > 10000",
                "去重规则: 优先按视频 ID 去重，缺失时按视频链接去重。",
            ],
            border_style="yellow",
        )
        run_douyin_high_like_export()
        return

    action_choice = prompt_action_choice()

    if platform_choice == "1":
        if action_choice == "3":
            show_run_panel("上传本地 B站 主数据表", ["不重新抓取，只同步前面的 B站数据表。"])
            run_bilibili(action_choice)
            show_run_panel("上传本地 抖音 主数据表", ["不重新抓取，只同步前面的抖音数据表。"])
            run_douyin(action_choice)
        else:
            show_run_panel("执行 B站", ["普通模式。若启用上传，只同步前面的 B站数据表。"])
            run_bilibili(action_choice)
            show_run_panel(
                "执行抖音",
                [
                    f"普通模式，当前抓取模式: {DOUYIN_FETCH_MODE}",
                    f"浏览器后端: {backend_label}",
                    f"浏览器目标: {browser_label}",
                    "若启用上传，只同步前面的抖音数据表。",
                ],
            )
            run_douyin(action_choice)
        return

    if platform_choice == "2":
        if action_choice == "3":
            show_run_panel("上传本地 B站 主数据表", ["不重新抓取，只同步 B站数据表。"])
        else:
            show_run_panel("执行 B站", ["普通模式。若启用上传，只同步 B站数据表。"])
        run_bilibili(action_choice)
        return

    if action_choice == "3":
        show_run_panel("上传本地 抖音 主数据表", ["不重新抓取，只同步抖音数据表。"])
    else:
        show_run_panel(
            "执行抖音",
            [
                f"普通模式，当前抓取模式: {DOUYIN_FETCH_MODE}",
                f"浏览器后端: {backend_label}",
                f"浏览器目标: {browser_label}",
                "若启用上传，只同步抖音数据表。",
            ],
        )
    run_douyin(action_choice)


if __name__ == "__main__":
    main()
