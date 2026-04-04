from pathlib import Path

from bilibili_analyzer.logging_utils import create_summary_panel, create_table, get_console


DOUYIN_FETCH_MODE = "monitor"
# 可选值: "counts", "monitor", "delta", "full"

ROOT_DIR = Path(__file__).resolve().parent
DOUYIN_UNFOLLOW_LIST_PATH = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"


def show_platform_menu():
    console = get_console()
    console.print()
    console.print(
        create_summary_panel(
            "运行入口",
            [
                "统一入口已支持 B站、抖音、飞书上传分离控制。",
                f"当前抖音抓取模式: {DOUYIN_FETCH_MODE}",
            ],
            border_style="cyan",
        )
    )

    table = create_table(
        "平台选择",
        [
            ("编号", "right", "bold"),
            ("操作", "left"),
            ("说明", "left"),
        ],
    )
    table.add_row("1", "B站 + 抖音", "同时处理两个平台")
    table.add_row("2", "仅 B站", "只处理 B站任务")
    table.add_row("3", "仅 抖音", "只处理抖音任务")
    table.add_row("4", "抖音取消关注", "读取名单并执行取消关注")
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
    table.add_row("2", "抓取并上传", "抓取完成后自动同步飞书")
    table.add_row("3", "仅上传", "不重新抓取，只上传本地已有结果")
    console.print(table)
    console.print()


def show_run_panel(title: str, lines, border_style: str = "green"):
    get_console().print()
    get_console().print(create_summary_panel(title, lines, border_style=border_style))
    get_console().print()


def prompt_platform_choice():
    show_platform_menu()
    while True:
        choice = input("请输入平台编号 (1/2/3/4): ").strip()
        if choice in {"1", "2", "3", "4"}:
            return choice
        get_console().print(create_summary_panel("输入无效", ["请输入 1、2、3 或 4。"], border_style="red"))


def prompt_action_choice():
    show_action_menu()
    while True:
        choice = input("请输入动作编号 (1/2/3): ").strip()
        if choice in {"1", "2", "3"}:
            return choice
        get_console().print(create_summary_panel("输入无效", ["请输入 1、2 或 3。"], border_style="red"))


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


def main():
    platform_choice = prompt_platform_choice()

    if platform_choice == "4":
        show_run_panel(
            "执行抖音取消关注",
            [
                f"名单文件: {DOUYIN_UNFOLLOW_LIST_PATH}",
                "将按名单逐个进入主页并尝试取消关注。",
            ],
            border_style="yellow",
        )
        run_douyin_unfollow()
        return

    action_choice = prompt_action_choice()

    if platform_choice == "1":
        if action_choice == "3":
            show_run_panel("上传本地 B站 结果", ["不重新抓取，直接同步本地结果到飞书。"])
            run_bilibili(action_choice)
            show_run_panel("上传本地 抖音 结果", ["不重新抓取，直接同步本地结果到飞书。"])
            run_douyin(action_choice)
        else:
            show_run_panel("开始处理 B站", ["将执行 B站 抓取流程。"])
            run_bilibili(action_choice)
            show_run_panel(
                "开始处理 抖音",
                [f"将执行抖音抓取流程。", f"当前模式: {DOUYIN_FETCH_MODE}"],
            )
            run_douyin(action_choice)
        return

    if platform_choice == "2":
        if action_choice == "3":
            show_run_panel("上传本地 B站 结果", ["不重新抓取，直接同步本地结果到飞书。"])
        else:
            show_run_panel("开始处理 B站", ["将执行 B站 抓取流程。"])
        run_bilibili(action_choice)
        return

    if action_choice == "3":
        show_run_panel("上传本地 抖音 结果", ["不重新抓取，直接同步本地结果到飞书。"])
    else:
        show_run_panel(
            "开始处理 抖音",
            [f"将执行抖音抓取流程。", f"当前模式: {DOUYIN_FETCH_MODE}"],
        )
    run_douyin(action_choice)


if __name__ == "__main__":
    main()
