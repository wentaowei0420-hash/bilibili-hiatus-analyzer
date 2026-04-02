def prompt_platform_choice():
    print("=" * 60)
    print("请选择运行模式")
    print("1. B站 + 抖音 同时分析")
    print("2. 仅分析 B站")
    print("3. 仅分析 抖音")
    print("=" * 60)

    while True:
        choice = input("请输入选项编号 (1/2/3): ").strip()
        if choice in {"1", "2", "3"}:
            return choice
        print("输入无效，请输入 1、2 或 3。")


def run_bilibili():
    from bilibili_analyzer.app import main as bilibili_main

    bilibili_main()


def run_douyin():
    from douyin_analyzer.app import main as douyin_main

    douyin_main()


def main():
    choice = prompt_platform_choice()

    if choice == "1":
        print("\n开始执行 B站 分析...\n")
        run_bilibili()
        print("\n开始执行 抖音 分析...\n")
        run_douyin()
        return

    if choice == "2":
        print("\n开始执行 B站 分析...\n")
        run_bilibili()
        return

    print("\n开始执行 抖音 分析...\n")
    run_douyin()


if __name__ == "__main__":
    main()
