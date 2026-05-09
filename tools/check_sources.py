import compileall
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
TARGETS = [
    "bilibili_analyzer",
    "common",
    "douyin_analyzer",
    "douyin-downloader-main/core",
    "douyin-downloader-main/storage",
    "douyin-downloader-main/control",
    "douyin-downloader-main/gui.py",
    "gui.py",
    "main.py",
]


def main():
    ok = True
    for target in TARGETS:
        path = ROOT / target
        if path.is_dir():
            ok = compileall.compile_dir(str(path), quiet=1) and ok
        elif path.exists():
            ok = compileall.compile_file(str(path), quiet=1) and ok
    raise SystemExit(0 if ok else 1)


if __name__ == "__main__":
    main()
