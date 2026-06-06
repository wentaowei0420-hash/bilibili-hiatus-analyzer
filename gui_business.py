import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

from common.file_io import atomic_write_json
from gui_backend_client import BackendApiClient, ensure_backend_available


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DOUYIN_UNFOLLOW_LIST = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"
GUI_CONFIG_PATH = ROOT_DIR / "data" / "state" / "gui_config.json"
DEFAULT_DOUYIN_DOWNLOADER_ROOT = ROOT_DIR / "douyin-downloader-main"
EXTERNAL_DOUYIN_DOWNLOADER_ROOT = Path(
    os.getenv("DOUYIN_DOWNLOADER_ROOT", str(DEFAULT_DOUYIN_DOWNLOADER_ROOT))
)
EXTERNAL_DOUYIN_DOWNLOADER_RUNNER = EXTERNAL_DOUYIN_DOWNLOADER_ROOT / "run.py"
EXTERNAL_DOUYIN_DOWNLOADER_LAUNCH_LOG = ROOT_DIR / "runtime" / "logs" / "douyin_downloader_gui_launch.log"
DEFAULT_AUTO_FULL_INTERVAL_MINUTES = 180


def fetch_order_option_map(options):
    return {value: label for label, value in options}


def option_pairs(items):
    return [(item.get("label", ""), item.get("value", "")) for item in (items or [])]


def column_pairs(items):
    return [(item.get("label", ""), item.get("key", "")) for item in (items or [])]


def runtime_field_tuples(items):
    return [
        (
            item.get("name", ""),
            item.get("env_name", ""),
            item.get("label", ""),
            item.get("type", "int"),
            item.get("minimum", 0),
            item.get("maximum", 0),
            item.get("step", 1),
        )
        for item in (items or [])
    ]


def bucket_tuples(items):
    return [
        (item.get("label", ""), item.get("lower", 0), item.get("upper"))
        for item in (items or [])
    ]


def load_backend_gui_metadata():
    ensure_backend_available()
    return BackendApiClient().gui_metadata()


def coerce_setting_value(value, field_type, fallback):
    if value is None:
        return fallback
    try:
        return int(value) if field_type == "int" else float(value)
    except (TypeError, ValueError):
        return fallback


def load_default_fetch_order_settings():
    return {
        "bilibili": {"field": "follower_count", "direction": "desc"},
        "douyin": {"field": "follower_count", "direction": "desc"},
    }


def normalize_fetch_order_settings(
    settings,
    *,
    bilibili_options,
    douyin_options,
    direction_options,
):
    defaults = load_default_fetch_order_settings()
    normalized = {}
    platform_options = {
        "bilibili": bilibili_options,
        "douyin": douyin_options,
    }
    for platform, options in platform_options.items():
        allowed_fields = set(fetch_order_option_map(options))
        current = (settings or {}).get(platform, {}) if isinstance(settings, dict) else {}
        field = current.get("field")
        if allowed_fields and field not in allowed_fields:
            field = defaults[platform]["field"]
        direction = str(current.get("direction") or defaults[platform]["direction"]).strip().lower()
        allowed_directions = set(fetch_order_option_map(direction_options))
        if allowed_directions and direction not in allowed_directions:
            direction = defaults[platform]["direction"]
        normalized[platform] = {"field": field, "direction": direction}
    return normalized


def load_backend_config_defaults(*, bilibili_options, douyin_options, direction_options):
    ensure_backend_available()
    data = BackendApiClient().config_defaults()
    return {
        "bilibili_runtime_settings": dict(data.get("bilibili_runtime_settings") or {}),
        "douyin_runtime_settings": dict(data.get("douyin_runtime_settings") or {}),
        "fetch_order_settings": normalize_fetch_order_settings(
            data.get("fetch_order_settings") or load_default_fetch_order_settings(),
            bilibili_options=bilibili_options,
            douyin_options=douyin_options,
            direction_options=direction_options,
        ),
        "douyin_full_fetch_retry_on_mismatch": bool(
            data.get("douyin_full_fetch_retry_on_mismatch", True)
        ),
    }


def load_gui_config() -> dict:
    if not GUI_CONFIG_PATH.exists():
        return {}
    with GUI_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
        data = json.load(config_file)
    return data if isinstance(data, dict) else {}


def save_gui_config(config: dict) -> None:
    atomic_write_json(GUI_CONFIG_PATH, config, indent=2)


def extract_progress_total(text: str):
    patterns = (
        r"本轮处理\s*[=：]\s*(\d+)\s*位",
        r"Douyin followings ready\s*\|\s*rows\s*=\s*(\d+)",
        r"Douyin analysis start\s*\|.*?cached_followings\s*=\s*(\d+)",
        r"关注列表准备完成\s*\|.*?本轮处理\s*=\s*(\d+)\s*位",
    )
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1))
    return None


def extract_progress_current(text: str):
    paired_patterns = (
        r"获取B站关注列表\s*\((\d+)\s*/\s*(\d+)\)",
        r"执行抖音取消关注\s*\((\d+)\s*/\s*(\d+)\)",
    )
    for pattern in paired_patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), int(match.group(2))

    current_patterns = (
        r"已处理博主\s*[:：]\s*(\d+)",
        r"已处理\s*(\d+)\s*位博主",
        r"已安全保存到本地\s*[:：]\s*已处理\s*(\d+)\s*位博主",
        r"抓取抖音关注列表\s*\|\s*已获取\s*(\d+)\s*位",
    )
    for pattern in current_patterns:
        match = re.search(pattern, text)
        if match:
            return int(match.group(1)), None
    return None, None


def video_downloader_launch_commands() -> list[list[str]]:
    commands = []
    seen = set()

    def add_command(parts):
        key = tuple(str(part) for part in parts)
        if not key or key in seen:
            return
        seen.add(key)
        commands.append(list(key))

    python_executable = Path(sys.executable) if sys.executable else None
    if python_executable and python_executable.exists():
        if python_executable.name.lower() == "python.exe":
            pythonw = python_executable.with_name("pythonw.exe")
            if pythonw.exists():
                add_command([str(pythonw), str(EXTERNAL_DOUYIN_DOWNLOADER_RUNNER), "--gui"])
        add_command([str(python_executable), str(EXTERNAL_DOUYIN_DOWNLOADER_RUNNER), "--gui"])

    for launcher in ("pyw", "py", "pythonw", "python"):
        resolved = shutil.which(launcher)
        if resolved:
            add_command([resolved, str(EXTERNAL_DOUYIN_DOWNLOADER_RUNNER), "--gui"])

    return commands


def launch_video_downloader_gui() -> tuple[bool, str]:
    if not EXTERNAL_DOUYIN_DOWNLOADER_RUNNER.exists():
        return False, f"未找到下载器启动文件：\n{EXTERNAL_DOUYIN_DOWNLOADER_RUNNER}"

    launch_errors = []
    creationflags = 0
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS

    for command in video_downloader_launch_commands():
        log_file = None
        try:
            EXTERNAL_DOUYIN_DOWNLOADER_LAUNCH_LOG.parent.mkdir(parents=True, exist_ok=True)
            log_file = EXTERNAL_DOUYIN_DOWNLOADER_LAUNCH_LOG.open("a", encoding="utf-8")
            log_file.write(f"\n[{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}] {' '.join(command)}\n")
            log_file.flush()
            process = subprocess.Popen(
                command,
                cwd=str(EXTERNAL_DOUYIN_DOWNLOADER_ROOT),
                creationflags=creationflags,
                stdout=log_file,
                stderr=log_file,
            )
            time.sleep(0.8)
            if process.poll() is not None:
                log_file.close()
                launch_errors.append(
                    f"{' '.join(command)} -> 进程立即退出，详见 {EXTERNAL_DOUYIN_DOWNLOADER_LAUNCH_LOG}"
                )
                continue
            log_file.close()
            return True, f"已启动视频下载界面：{EXTERNAL_DOUYIN_DOWNLOADER_RUNNER} --gui"
        except Exception as exc:
            launch_errors.append(f"{' '.join(command)} -> {exc}")
            if log_file is not None and not log_file.closed:
                log_file.close()

    message = "无法启动固定视频下载界面。"
    if launch_errors:
        message += "\n\n已尝试：\n" + "\n".join(launch_errors[:5])
    return False, message
