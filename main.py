import os
from pathlib import Path

from bilibili_analyzer.logging_utils import create_summary_panel, create_table, get_console
from douyin_analyzer.config import load_analyzer_config as load_douyin_config

DOUYIN_FETCH_MODE = "monitor"
# Available: "counts", "monitor", "delta", "full"

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
            "Run Entry",
            [
                "One entry now controls Bilibili, Douyin and Feishu sync separately.",
                f"Current Douyin fetch mode: {DOUYIN_FETCH_MODE}",
                f"Current Douyin backend: {backend_label} [{douyin_config.browser_backend}]",
                f"Current Douyin browser: {browser_label}",
                "Modes 1/2/3 sync only the front main sheets.",
                "Modes 5/6 write analysis CSVs and sync only the back analysis sheets.",
            ],
            border_style="cyan",
        )
    )

    table = create_table(
        "Platform Choice",
        [
            ("ID", "right", "bold"),
            ("Action", "left"),
            ("Target Sheet", "left"),
            ("Description", "left"),
        ],
    )
    table.add_row("1", "Bilibili + Douyin", "Main sheets", "Run both normal platform flows")
    table.add_row("2", "Bilibili only", "B站数据表", "Run normal Bilibili flow")
    table.add_row("3", "Douyin only", "抖音数据表", "Run normal Douyin flow")
    table.add_row("4", "Douyin unfollow", "-", "Read a txt list and unfollow targets")
    table.add_row("5", "Bilibili UID full fetch", "B站分析表", "Fetch listed UIDs and upload UID analysis only")
    table.add_row("6", "Douyin UID full fetch", "抖音分析表", "Fetch listed UIDs and upload UID analysis only")
    console.print(table)
    console.print()


def show_action_menu():
    console = get_console()
    table = create_table(
        "Action Choice",
        [
            ("ID", "right", "bold"),
            ("Action", "left"),
            ("Description", "left"),
        ],
    )
    table.add_row("1", "Fetch only", "Update local files only")
    table.add_row("2", "Fetch and upload", "Fetch first, then sync Feishu main sheet")
    table.add_row("3", "Upload only", "Do not fetch, upload existing local main-sheet files")
    console.print(table)
    console.print()


def show_douyin_backend_menu():
    console = get_console()
    table = create_table(
        "Douyin Backend Choice",
        [
            ("ID", "right", "bold"),
            ("Backend", "left"),
            ("Description", "left"),
        ],
    )
    table.add_row("1", "DrissionPage", "More compatible with the current project defaults")
    table.add_row("2", "Playwright", "Stronger page control and browser fallback")
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
        choice = input("Enter platform ID (1/2/3/4/5/6): ").strip()
        if choice in {"1", "2", "3", "4", "5", "6"}:
            return choice
        get_console().print(create_summary_panel("Invalid Input", ["Please enter 1, 2, 3, 4, 5 or 6."], border_style="red"))


def prompt_action_choice():
    show_action_menu()
    while True:
        choice = input("Enter action ID (1/2/3): ").strip()
        if choice in {"1", "2", "3"}:
            return choice
        get_console().print(create_summary_panel("Invalid Input", ["Please enter 1, 2 or 3."], border_style="red"))


def prompt_douyin_backend_choice():
    current_backend = os.getenv("DOUYIN_BROWSER_BACKEND", "").strip().lower() or "drission"
    show_douyin_backend_menu()
    prompt = f"Enter Douyin backend ID (1/2, current: {current_backend}): "
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
                "Invalid Input",
                ["Please enter 1, 2, or press Enter to keep the current backend."],
                border_style="red",
            )
        )


def apply_douyin_runtime_backend(backend: str):
    normalized = (backend or "drission").strip().lower() or "drission"
    os.environ["DOUYIN_BROWSER_BACKEND"] = normalized
    _, backend_label, browser_label = get_douyin_runtime_labels()
    show_run_panel(
        "Douyin Backend Selected",
        [
            f"Backend: {backend_label} [{normalized}]",
            f"Browser target: {browser_label}",
            "This selection applies to the current run without changing .env defaults.",
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


def run_bilibili_uid_fetch():
    from bilibili_analyzer.app import run_fetch_uid_videos

    run_fetch_uid_videos(BILIBILI_UID_FETCH_LIST_PATH)


def run_douyin_uid_fetch():
    from douyin_analyzer.app import run_fetch_uid_videos

    run_fetch_uid_videos(DOUYIN_UID_FETCH_LIST_PATH)


def main():
    platform_choice = prompt_platform_choice()

    if platform_choice in {"1", "3", "4", "6"}:
        apply_douyin_runtime_backend(prompt_douyin_backend_choice())

    _, backend_label, browser_label = get_douyin_runtime_labels()

    if platform_choice == "4":
        show_run_panel(
            "Run Douyin Unfollow",
            [
                f"List file: {DOUYIN_UNFOLLOW_LIST_PATH}",
                f"Browser backend: {backend_label}",
                f"Browser target: {browser_label}",
                "The browser will open each homepage and try to unfollow if currently followed.",
            ],
            border_style="yellow",
        )
        run_douyin_unfollow()
        return

    if platform_choice == "5":
        show_run_panel(
            "Run Bilibili UID Full Fetch",
            [
                f"List file: {BILIBILI_UID_FETCH_LIST_PATH}",
                "This mode writes UID analysis CSV files locally.",
                "After the crawl, only B站分析表 will be synced.",
            ],
            border_style="yellow",
        )
        run_bilibili_uid_fetch()
        return

    if platform_choice == "6":
        show_run_panel(
            "Run Douyin UID Full Fetch",
            [
                f"List file: {DOUYIN_UID_FETCH_LIST_PATH}",
                f"Browser backend: {backend_label}",
                f"Browser target: {browser_label}",
                "This mode writes UID analysis CSV files locally.",
                "After the crawl, only 抖音分析表 will be synced.",
            ],
            border_style="yellow",
        )
        run_douyin_uid_fetch()
        return

    action_choice = prompt_action_choice()

    if platform_choice == "1":
        if action_choice == "3":
            show_run_panel("Upload Local Bilibili Main Sheet", ["No refetch. Only the front Bilibili main sheet will be synced."])
            run_bilibili(action_choice)
            show_run_panel("Upload Local Douyin Main Sheet", ["No refetch. Only the front Douyin main sheet will be synced."])
            run_douyin(action_choice)
        else:
            show_run_panel("Run Bilibili", ["Normal mode. Only the front Bilibili main sheet will be synced if upload is enabled."])
            run_bilibili(action_choice)
            show_run_panel(
                "Run Douyin",
                [
                    f"Normal mode. Current fetch mode: {DOUYIN_FETCH_MODE}",
                    f"Browser backend: {backend_label}",
                    f"Browser target: {browser_label}",
                    "Only the front Douyin main sheet will be synced if upload is enabled.",
                ],
            )
            run_douyin(action_choice)
        return

    if platform_choice == "2":
        if action_choice == "3":
            show_run_panel("Upload Local Bilibili Main Sheet", ["No refetch. Only B站数据表 will be synced."])
        else:
            show_run_panel("Run Bilibili", ["Normal mode. Only B站数据表 will be synced if upload is enabled."])
        run_bilibili(action_choice)
        return

    if action_choice == "3":
        show_run_panel("Upload Local Douyin Main Sheet", ["No refetch. Only 抖音数据表 will be synced."])
    else:
        show_run_panel(
            "Run Douyin",
            [
                f"Normal mode. Current fetch mode: {DOUYIN_FETCH_MODE}",
                f"Browser backend: {backend_label}",
                f"Browser target: {browser_label}",
                "Only 抖音数据表 will be synced if upload is enabled.",
            ],
        )
    run_douyin(action_choice)


if __name__ == "__main__":
    main()
