from pathlib import Path


DOUYIN_FETCH_MODE = "monitor"
# 可选值: "counts", "monitor", "delta", "full"

ROOT_DIR = Path(__file__).resolve().parent
DOUYIN_UNFOLLOW_LIST_PATH = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"


def prompt_platform_choice():
    print("=" * 60)
    print("请选择运行模式")
    print("1. B站 + 抖音 同时分析")
    print("2. 仅分析 B站")
    print("3. 仅分析 抖音")
    print("4. 执行抖音取消关注")
    print("=" * 60)

    while True:
        choice = input("请输入选项编号 (1/2/3/4): ").strip()
        if choice in {"1", "2", "3", "4"}:
            return choice
        print("输入无效，请输入 1、2、3 或 4。")


def run_bilibili():
    from bilibili_analyzer.app import main as bilibili_main

    bilibili_main()


def run_douyin():
    from douyin_analyzer.app import main as douyin_main

    douyin_main(fetch_mode_override=DOUYIN_FETCH_MODE)


def run_douyin_unfollow():
    from douyin_analyzer.app import run_unfollow

    run_unfollow(DOUYIN_UNFOLLOW_LIST_PATH)


def main():
    choice = prompt_platform_choice()

    if choice == "1":
        print("\n开始执行 B站 分析...\n")
        run_bilibili()
        print(f"\n开始执行 抖音 分析... (mode={DOUYIN_FETCH_MODE})\n")
        run_douyin()
        return

    if choice == "2":
        print("\n开始执行 B站 分析...\n")
        run_bilibili()
        return

    if choice == "3":
        print(f"\n开始执行 抖音 分析... (mode={DOUYIN_FETCH_MODE})\n")
        run_douyin()
        return

    print(f"\n开始执行 抖音取消关注... (list={DOUYIN_UNFOLLOW_LIST_PATH})\n")
    run_douyin_unfollow()


if __name__ == "__main__":
    main()
