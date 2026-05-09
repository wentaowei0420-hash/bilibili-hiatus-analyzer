import os
import json
import re
import shutil
import sqlite3
import subprocess
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import requests
from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QComboBox,
    QFileDialog,
    QDialog,
    QDoubleSpinBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QScrollArea,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from common.file_io import atomic_write_json
from common.runtime_control import OperationCancelled, clear_stop, request_stop


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DOUYIN_UNFOLLOW_LIST = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"
DEFAULT_BILIBILI_UID_LIST = ROOT_DIR / "data" / "bilibili" / "ops" / "bilibili_uid_fetch_list.txt"
DEFAULT_DOUYIN_UID_LIST = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_uid_fetch_list.txt"
GUI_CONFIG_PATH = ROOT_DIR / "data" / "state" / "gui_config.json"
DEFAULT_DOUYIN_DOWNLOADER_ROOT = ROOT_DIR / "douyin-downloader-main"
EXTERNAL_DOUYIN_DOWNLOADER_ROOT = Path(
    os.getenv("DOUYIN_DOWNLOADER_ROOT", str(DEFAULT_DOUYIN_DOWNLOADER_ROOT))
)
EXTERNAL_DOUYIN_DOWNLOADER_RUNNER = EXTERNAL_DOUYIN_DOWNLOADER_ROOT / "run.py"
EXTERNAL_DOUYIN_DOWNLOADER_LAUNCH_LOG = ROOT_DIR / "runtime" / "logs" / "douyin_downloader_gui_launch.log"

BILIBILI_RUNTIME_FIELDS = [
    ("video_stat_batch_cooldown", "VIDEO_STAT_BATCH_COOLDOWN", "\u89c6\u9891\u7edf\u8ba1\u6279\u6b21\u51b7\u5374", "int", 0, 3600, 1),
    ("request_delay", "REQUEST_DELAY", "\u8bf7\u6c42\u57fa\u7840\u95f4\u9694", "int", 0, 3600, 1),
    ("max_request_delay", "MAX_REQUEST_DELAY", "\u8bf7\u6c42\u6700\u5927\u95f4\u9694", "int", 0, 3600, 1),
    ("video_analysis_start_delay", "VIDEO_ANALYSIS_START_DELAY", "\u89c6\u9891\u5206\u6790\u542f\u52a8\u7b49\u5f85", "int", 0, 3600, 1),
    ("batch_cooldown", "BATCH_COOLDOWN", "\u4e3b\u6279\u6b21\u51b7\u5374", "int", 0, 3600, 1),
    ("long_rate_limit_cooldown", "LONG_RATE_LIMIT_COOLDOWN", "\u9650\u6d41\u957f\u51b7\u5374", "int", 0, 7200, 1),
    ("rate_limit_retry_before_long_cooldown", "RATE_LIMIT_RETRY_BEFORE_LONG_COOLDOWN", "\u957f\u51b7\u5374\u524d\u91cd\u8bd5\u6b21\u6570", "int", 1, 100, 1),
    ("max_rate_limit_retries", "MAX_RATE_LIMIT_RETRIES", "\u9650\u6d41\u6700\u5927\u91cd\u8bd5\u6b21\u6570", "int", 1, 100, 1),
    ("failed_retry_cooldown", "FAILED_RETRY_COOLDOWN", "\u5931\u8d25\u91cd\u8bd5\u51b7\u5374", "int", 0, 7200, 1),
    ("video_analysis_batch_cooldown", "VIDEO_ANALYSIS_BATCH_COOLDOWN", "\u89c6\u9891\u5206\u6790\u6279\u6b21\u51b7\u5374", "int", 0, 3600, 1),
]

DOUYIN_RUNTIME_FIELDS = [
    ("page_load_delay", "DOUYIN_PAGE_LOAD_DELAY", "\u9875\u9762\u52a0\u8f7d\u7b49\u5f85", "float", 0.0, 1200.0, 0.1),
    ("user_request_interval", "DOUYIN_USER_REQUEST_INTERVAL", "\u7528\u6237\u8bf7\u6c42\u95f4\u9694", "float", 0.0, 1200.0, 0.1),
    ("request_rate_limit_per_second", "DOUYIN_REQUEST_RATE_LIMIT_PER_SECOND", "\u6bcf\u79d2\u8bf7\u6c42\u4e0a\u9650", "float", 0.1, 100.0, 0.1),
    ("retry_backoff_base_seconds", "DOUYIN_RETRY_BACKOFF_BASE_SECONDS", "\u91cd\u8bd5\u9000\u907f\u8d77\u59cb\u79d2\u6570", "float", 0.0, 7200.0, 0.5),
    ("retry_backoff_max_seconds", "DOUYIN_RETRY_BACKOFF_MAX_SECONDS", "\u91cd\u8bd5\u9000\u907f\u6700\u5927\u79d2\u6570", "float", 0.0, 7200.0, 0.5),
    ("conservative_mode_duration_seconds", "DOUYIN_CONSERVATIVE_MODE_DURATION_SECONDS", "\u4fdd\u5b88\u6a21\u5f0f\u6301\u7eed\u79d2\u6570", "float", 0.0, 7200.0, 1.0),
    ("refresh_batch_cooldown", "DOUYIN_REFRESH_BATCH_COOLDOWN", "\u5237\u65b0\u6279\u6b21\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
    ("browser_restart_interval_users", "DOUYIN_BROWSER_RESTART_INTERVAL_USERS", "\u6d4f\u89c8\u5668\u91cd\u542f\u95f4\u9694\u7528\u6237\u6570", "int", 1, 10000, 1),
    ("video_page_load_delay", "DOUYIN_VIDEO_PAGE_LOAD_DELAY", "\u89c6\u9891\u9875\u52a0\u8f7d\u7b49\u5f85", "float", 0.0, 1200.0, 0.1),
    ("service_error_retry_wait", "DOUYIN_SERVICE_ERROR_RETRY_WAIT", "\u670d\u52a1\u5f02\u5e38\u91cd\u8bd5\u7b49\u5f85", "float", 0.0, 7200.0, 0.5),
    ("service_error_long_cooldown", "DOUYIN_SERVICE_ERROR_LONG_COOLDOWN", "\u670d\u52a1\u5f02\u5e38\u957f\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
    ("service_error_global_cooldown", "DOUYIN_SERVICE_ERROR_GLOBAL_COOLDOWN", "\u670d\u52a1\u5f02\u5e38\u5168\u5c40\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
    ("rate_limit_retry_wait", "DOUYIN_RATE_LIMIT_RETRY_WAIT", "\u9650\u6d41\u91cd\u8bd5\u7b49\u5f85", "float", 0.0, 7200.0, 0.5),
    ("rate_limit_long_cooldown", "DOUYIN_RATE_LIMIT_LONG_COOLDOWN", "\u9650\u6d41\u957f\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
    ("rate_limit_global_cooldown", "DOUYIN_RATE_LIMIT_GLOBAL_COOLDOWN", "\u9650\u6d41\u5168\u5c40\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
    ("progress_save_interval_users", "DOUYIN_PROGRESS_SAVE_INTERVAL_USERS", "\u8fdb\u5ea6\u4fdd\u5b58\u95f4\u9694\u7528\u6237\u6570", "int", 1, 10000, 1),
    ("intermediate_upload_interval_users", "DOUYIN_INTERMEDIATE_UPLOAD_INTERVAL_USERS", "\u4e2d\u95f4\u4e0a\u4f20\u95f4\u9694\u7528\u6237\u6570", "int", 1, 10000, 1),
    ("unfollow_interval_seconds", "DOUYIN_UNFOLLOW_INTERVAL_SECONDS", "\u53d6\u6d88\u5173\u6ce8\u95f4\u9694\u79d2\u6570", "float", 0.0, 1200.0, 0.1),
    ("unfollow_batch_cooldown", "DOUYIN_UNFOLLOW_BATCH_COOLDOWN", "\u53d6\u6d88\u5173\u6ce8\u6279\u6b21\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
    ("unfollow_restart_interval", "DOUYIN_UNFOLLOW_RESTART_INTERVAL", "\u53d6\u6d88\u5173\u6ce8\u91cd\u542f\u95f4\u9694", "int", 1, 10000, 1),
    ("unfollow_failure_cooldown", "DOUYIN_UNFOLLOW_FAILURE_COOLDOWN", "\u53d6\u6d88\u5173\u6ce8\u5931\u8d25\u51b7\u5374", "float", 0.0, 7200.0, 0.5),
]

BILIBILI_FETCH_ORDER_OPTIONS = [
    ("\u7c89\u4e1d\u6570", "follower_count"),
    ("\u89c6\u9891\u603b\u6570", "published_video_count"),
    ("\u5e73\u5747\u70b9\u8d5e\u6570", "average_like_count"),
]

DOUYIN_FETCH_ORDER_OPTIONS = [
    ("\u7c89\u4e1d\u6570", "follower_count"),
    ("\u89c6\u9891\u603b\u6570", "published_video_count"),
    ("\u83b7\u8d5e\u603b\u6570", "total_favorited"),
    ("\u5e73\u5747\u70b9\u8d5e\u6570", "average_like_count"),
]

FETCH_ORDER_DIRECTION_OPTIONS = [
    ("\u964d\u5e8f", "desc"),
    ("\u5347\u5e8f", "asc"),
]


def _fetch_order_option_map(options):
    return {value: label for label, value in options}


def _settings_to_dict(config, fields):
    values = {}
    for name, *_rest in fields:
        values[name] = getattr(config, name)
    return values


def _load_default_bilibili_runtime_settings():
    from bilibili_analyzer.config import load_analyzer_config

    return _settings_to_dict(load_analyzer_config(), BILIBILI_RUNTIME_FIELDS)


def _load_default_douyin_runtime_settings():
    from douyin_analyzer.config import load_analyzer_config

    return _settings_to_dict(load_analyzer_config(), DOUYIN_RUNTIME_FIELDS)


def _coerce_setting_value(value, field_type, fallback):
    if value is None:
        return fallback
    try:
        return int(value) if field_type == "int" else float(value)
    except (TypeError, ValueError):
        return fallback


def _load_default_fetch_order_settings():
    return {
        "bilibili": {"field": "follower_count", "direction": "desc"},
        "douyin": {"field": "follower_count", "direction": "desc"},
    }


def _normalize_fetch_order_settings(settings):
    defaults = _load_default_fetch_order_settings()
    normalized = {}
    platform_options = {
        "bilibili": BILIBILI_FETCH_ORDER_OPTIONS,
        "douyin": DOUYIN_FETCH_ORDER_OPTIONS,
    }
    for platform, options in platform_options.items():
        allowed_fields = set(_fetch_order_option_map(options))
        current = (settings or {}).get(platform, {}) if isinstance(settings, dict) else {}
        field = current.get("field")
        if field not in allowed_fields:
            field = defaults[platform]["field"]
        direction = str(current.get("direction") or defaults[platform]["direction"]).strip().lower()
        if direction not in {"asc", "desc"}:
            direction = defaults[platform]["direction"]
        normalized[platform] = {"field": field, "direction": direction}
    return normalized


@dataclass
class RunConfig:
    platform: str
    action: str
    bilibili_mode: str
    douyin_fetch_mode: str
    douyin_backend: str
    monitor_video_limit: int
    uid_limit_enabled: bool
    uid_limit: int
    high_like_threshold: int
    unfollow_list_path: Path
    bilibili_uid_list_path: Path
    douyin_uid_list_path: Path
    bilibili_runtime_settings: dict
    douyin_runtime_settings: dict
    fetch_order_settings: dict


class SignalWriter:
    def __init__(self, signal):
        self.signal = signal
        self._buffer = ""
        self.encoding = "utf-8"

    def write(self, text):
        if not text:
            return
        self._buffer += text
        while "\n" in self._buffer:
            line, self._buffer = self._buffer.split("\n", 1)
            self.signal.emit(line)

    def flush(self):
        if self._buffer:
            self.signal.emit(self._buffer)
            self._buffer = ""

    def isatty(self):
        return False


class RunnerThread(QThread):
    log_line = pyqtSignal(str)
    done = pyqtSignal(bool, str)

    def __init__(self, config: RunConfig):
        super().__init__()
        self.config = config

    def run(self):
        writer = SignalWriter(self.log_line)
        try:
            clear_stop()
            with redirect_stdout(writer), redirect_stderr(writer):
                self._run_task()
            writer.flush()
            self.done.emit(True, "任务执行完成")
        except OperationCancelled:
            writer.flush()
            self.done.emit(True, "已终止运行，已保存当前可用数据")
        except Exception as exc:
            writer.flush()
            self.log_line.emit("任务执行失败:")
            self.log_line.emit(traceback.format_exc())
            self.done.emit(False, str(exc))

    def _run_task(self):
        os.environ["DOUYIN_BROWSER_BACKEND"] = self.config.douyin_backend
        self._apply_runtime_environment(BILIBILI_RUNTIME_FIELDS, self.config.bilibili_runtime_settings)
        self._apply_runtime_environment(DOUYIN_RUNTIME_FIELDS, self.config.douyin_runtime_settings)
        self._apply_fetch_order_environment(self.config.fetch_order_settings)
        bilibili_analysis_mode, bilibili_enable_video_analysis = self._resolve_bilibili_runtime_mode()
        os.environ["ANALYSIS_MODE"] = bilibili_analysis_mode
        os.environ["ENABLE_VIDEO_DURATION_ANALYSIS"] = (
            "true" if bilibili_enable_video_analysis else "false"
        )
        uid_limit = self.config.uid_limit if self.config.uid_limit_enabled else None

        self.log_line.emit(f"平台: {self.config.platform}")
        self.log_line.emit(f"动作: {self.config.action}")
        self.log_line.emit(f"抖音模式: {self.config.douyin_fetch_mode}")
        self.log_line.emit(f"抖音后端: {self.config.douyin_backend}")
        self.log_line.emit(f"监控视频数: {self.config.monitor_video_limit}")
        self.log_line.emit(f"B站模式: {self.config.bilibili_mode}")
        self.log_line.emit("-" * 60)

        if self.config.platform == "bilibili":
            self._run_bilibili_main()
        elif self.config.platform == "douyin":
            self._run_douyin_main()
        elif self.config.platform == "both":
            self._run_bilibili_main()
            self._run_douyin_main()
        elif self.config.platform == "douyin_unfollow":
            from douyin_analyzer.app import run_unfollow

            run_unfollow(self.config.unfollow_list_path)
        elif self.config.platform == "douyin_non_followed_cleanup":
            from douyin_analyzer.app import run_prune_non_followed_cache

            run_prune_non_followed_cache()
        elif self.config.platform == "bilibili_uid":
            from bilibili_analyzer.app import run_fetch_uid_videos

            run_fetch_uid_videos(self.config.bilibili_uid_list_path, max_targets=uid_limit)
        elif self.config.platform == "douyin_uid":
            from douyin_analyzer.app import run_fetch_uid_videos

            run_fetch_uid_videos(self.config.douyin_uid_list_path, max_targets=uid_limit)
        elif self.config.platform == "douyin_high_like":
            from douyin_analyzer.app import run_export_high_like_videos_from_cache

            run_export_high_like_videos_from_cache(threshold=self.config.high_like_threshold)
        elif self.config.platform == "douyin_video_score":
            from douyin_analyzer.app import run_score_videos_from_cache

            run_score_videos_from_cache()
        elif self.config.platform == "douyin_creator_score":
            from douyin_analyzer.app import run_score_creators_from_cache

            run_score_creators_from_cache()
        elif self.config.platform == "douyin_compact_export":
            from douyin_analyzer.app import run_export_compact_tables_from_cache

            run_export_compact_tables_from_cache(high_like_threshold=self.config.high_like_threshold)
        else:
            raise ValueError(f"未知平台模式: {self.config.platform}")

    def _resolve_bilibili_runtime_mode(self):
        mode = (self.config.bilibili_mode or "precise_full").strip().lower()
        mapping = {
            "precise_full": ("precise", True),
            "precise_main_only": ("precise", False),
            "fallback_full": ("fallback", True),
            "fallback_main_only": ("fallback", False),
        }
        return mapping.get(mode, ("precise", True))

    def _apply_runtime_environment(self, fields, values):
        for name, env_name, _label, field_type, _minimum, _maximum, _step in fields:
            value = _coerce_setting_value(values.get(name), field_type, None)
            if value is None:
                continue
            os.environ[env_name] = str(int(value) if field_type == "int" else float(value))

    def _apply_fetch_order_environment(self, settings):
        normalized = _normalize_fetch_order_settings(settings)
        os.environ["BILIBILI_FETCH_ORDER_BY"] = normalized["bilibili"]["field"]
        os.environ["BILIBILI_FETCH_ORDER_DESC"] = "true" if normalized["bilibili"]["direction"] == "desc" else "false"
        os.environ["DOUYIN_FETCH_ORDER_BY"] = normalized["douyin"]["field"]
        os.environ["DOUYIN_FETCH_ORDER_DESC"] = "true" if normalized["douyin"]["direction"] == "desc" else "false"

    def _run_bilibili_main(self):
        from bilibili_analyzer.app import run_analysis, run_feishu_upload

        uid_limit = self.config.uid_limit if self.config.uid_limit_enabled else None
        if self.config.action == "fetch":
            result = run_analysis(trigger_upload=False, max_followings=uid_limit)
            if result is None:
                raise RuntimeError("B站分析未成功完成，请检查 BILIBILI_COOKIE 是否已失效。")
        elif self.config.action == "fetch_upload":
            result = run_analysis(trigger_upload=True, max_followings=uid_limit)
            if result is None:
                raise RuntimeError("B站分析未成功完成，请检查 BILIBILI_COOKIE 是否已失效。")
        elif self.config.action == "upload":
            run_feishu_upload()
        else:
            raise ValueError(f"未知动作: {self.config.action}")

    def _run_douyin_main(self):
        from douyin_analyzer.app import run_analysis, run_feishu_upload

        uid_limit = self.config.uid_limit if self.config.uid_limit_enabled else None
        if self.config.action == "fetch":
            result = run_analysis(
                trigger_upload=False,
                fetch_mode_override=self.config.douyin_fetch_mode,
                max_followings=uid_limit,
                recent_video_limit_override=self.config.monitor_video_limit,
            )
            if result is None:
                raise RuntimeError("抖音分析未成功完成，请检查登录状态或抓取配置。")
        elif self.config.action == "fetch_upload":
            result = run_analysis(
                trigger_upload=True,
                fetch_mode_override=self.config.douyin_fetch_mode,
                max_followings=uid_limit,
                recent_video_limit_override=self.config.monitor_video_limit,
            )
            if result is None:
                raise RuntimeError("抖音分析未成功完成，请检查登录状态或抓取配置。")
        elif self.config.action == "upload":
            run_feishu_upload()
        else:
            raise ValueError(f"未知动作: {self.config.action}")


class BilibiliCookieCheckThread(QThread):
    checked = pyqtSignal(bool, str)

    def run(self):
        try:
            from bilibili_analyzer.config import load_analyzer_config

            config = load_analyzer_config()
            if not (config.cookie or "").strip():
                self.checked.emit(False, "未配置 BILIBILI_COOKIE")
                return

            response = requests.get(config.nav_api, headers=config.headers, timeout=20)
            response.raise_for_status()
            payload = response.json() if response.content else {}
            if payload.get("code") == 0:
                user = payload.get("data", {}) or {}
                uname = user.get("uname") or "未知用户"
                mid = user.get("mid") or ""
                self.checked.emit(True, f"已登录：{uname} (mid={mid})")
                return

            message = payload.get("message") or payload.get("msg") or "账号未登录"
            self.checked.emit(False, f"未登录：{message}")
        except Exception as exc:
            self.checked.emit(False, f"检测失败：{exc}")


class RuntimeSettingsDialog(QDialog):
    def __init__(self, parent, title, fields, current_values):
        super().__init__(parent)
        self.setWindowTitle(title)
        self.resize(760, 620)
        self._fields = fields
        self._widgets = {}

        layout = QVBoxLayout(self)

        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_content = QWidget()
        form = QFormLayout(scroll_content)

        for name, env_name, label, field_type, minimum, maximum, step in fields:
            if field_type == "int":
                widget = QSpinBox()
                widget.setRange(int(minimum), int(maximum))
                widget.setSingleStep(int(step))
                widget.setValue(int(_coerce_setting_value(current_values.get(name), field_type, minimum)))
            else:
                widget = QDoubleSpinBox()
                widget.setDecimals(self._decimals_for_step(step))
                widget.setRange(float(minimum), float(maximum))
                widget.setSingleStep(float(step))
                widget.setValue(float(_coerce_setting_value(current_values.get(name), field_type, minimum)))
            widget.setToolTip(env_name)
            form.addRow(label, widget)
            self._widgets[name] = widget

        scroll_area.setWidget(scroll_content)
        layout.addWidget(scroll_area)

        button_row = QHBoxLayout()
        save_button = QPushButton("\u4fdd\u5b58")
        cancel_button = QPushButton("\u53d6\u6d88")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def _decimals_for_step(self, step):
        text = f"{step}".rstrip("0").rstrip(".")
        if "." not in text:
            return 0
        return min(len(text.split(".", 1)[1]), 3)

    def settings(self):
        values = {}
        for name, _env_name, _label, field_type, _minimum, _maximum, _step in self._fields:
            widget = self._widgets[name]
            values[name] = int(widget.value()) if field_type == "int" else float(widget.value())
        return values


class FetchOrderSettingsDialog(QDialog):
    def __init__(self, parent, current_settings):
        super().__init__(parent)
        self.setWindowTitle("\u6293\u53d6\u987a\u5e8f\u8bbe\u7f6e")
        self.resize(520, 260)
        self._settings = _normalize_fetch_order_settings(current_settings)

        layout = QVBoxLayout(self)
        layout.addWidget(self._build_platform_group("B\u7ad9\u6293\u53d6\u987a\u5e8f", BILIBILI_FETCH_ORDER_OPTIONS, "bilibili"))
        layout.addWidget(self._build_platform_group("\u6296\u97f3\u6293\u53d6\u987a\u5e8f", DOUYIN_FETCH_ORDER_OPTIONS, "douyin"))

        button_row = QHBoxLayout()
        save_button = QPushButton("\u4fdd\u5b58")
        cancel_button = QPushButton("\u53d6\u6d88")
        save_button.clicked.connect(self.accept)
        cancel_button.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

    def _build_platform_group(self, title, options, platform):
        group = QGroupBox(title)
        form = QFormLayout(group)

        field_combo = QComboBox()
        for label, value in options:
            field_combo.addItem(label, value)
        self._set_combo_by_data(field_combo, self._settings[platform]["field"])
        form.addRow("\u6392\u5e8f\u5b57\u6bb5", field_combo)

        direction_combo = QComboBox()
        for label, value in FETCH_ORDER_DIRECTION_OPTIONS:
            direction_combo.addItem(label, value)
        self._set_combo_by_data(direction_combo, self._settings[platform]["direction"])
        form.addRow("\u6392\u5e8f\u65b9\u5411", direction_combo)

        setattr(self, f"{platform}_field_combo", field_combo)
        setattr(self, f"{platform}_direction_combo", direction_combo)
        return group

    def _set_combo_by_data(self, combo, value):
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                combo.setCurrentIndex(index)
                return

    def settings(self):
        return {
            "bilibili": {
                "field": self.bilibili_field_combo.currentData(),
                "direction": self.bilibili_direction_combo.currentData(),
            },
            "douyin": {
                "field": self.douyin_field_combo.currentData(),
                "direction": self.douyin_direction_combo.currentData(),
            },
        }


class AdvancedSettingsDialog(QDialog):
    def __init__(self, parent, current_paths, bilibili_runtime_settings, douyin_runtime_settings, fetch_order_settings):
        super().__init__(parent)
        self.setWindowTitle("\u9ad8\u7ea7\u8bbe\u7f6e")
        self.resize(760, 430)
        self._bilibili_runtime_settings = dict(bilibili_runtime_settings)
        self._douyin_runtime_settings = dict(douyin_runtime_settings)
        self._fetch_order_settings = _normalize_fetch_order_settings(fetch_order_settings)

        layout = QVBoxLayout(self)

        path_group = QGroupBox("\u8def\u5f84\u8bbe\u7f6e")
        path_form = QFormLayout(path_group)
        self.unfollow_path_edit = self._path_row(path_form, "\u53d6\u6d88\u5173\u6ce8\u540d\u5355", current_paths["unfollow"])
        self.bilibili_uid_path_edit = self._path_row(path_form, "B\u7ad9 UID \u540d\u5355", current_paths["bilibili_uid"])
        self.douyin_uid_path_edit = self._path_row(path_form, "\u6296\u97f3 UID \u540d\u5355", current_paths["douyin_uid"])
        layout.addWidget(path_group)

        runtime_group = QGroupBox("\u51b7\u5374\u4e0e\u9650\u6d41\u8bbe\u7f6e")
        runtime_layout = QVBoxLayout(runtime_group)
        self.bilibili_runtime_button = QPushButton("\u8bbe\u7f6e B\u7ad9\u51b7\u5374\u4e0e\u9650\u6d41\u53c2\u6570")
        self.bilibili_runtime_button.clicked.connect(self._edit_bilibili_runtime_settings)
        runtime_layout.addWidget(self.bilibili_runtime_button)
        self.bilibili_runtime_summary = QLabel(f"\u5df2\u914d\u7f6e {len(BILIBILI_RUNTIME_FIELDS)} \u9879\u3002")
        self.bilibili_runtime_summary.setStyleSheet("color: #666; padding-left: 4px;")
        runtime_layout.addWidget(self.bilibili_runtime_summary)

        self.douyin_runtime_button = QPushButton("\u8bbe\u7f6e\u6296\u97f3\u51b7\u5374\u4e0e\u9650\u6d41\u53c2\u6570")
        self.douyin_runtime_button.clicked.connect(self._edit_douyin_runtime_settings)
        runtime_layout.addWidget(self.douyin_runtime_button)
        self.douyin_runtime_summary = QLabel(f"\u5df2\u914d\u7f6e {len(DOUYIN_RUNTIME_FIELDS)} \u9879\u3002")
        self.douyin_runtime_summary.setStyleSheet("color: #666; padding-left: 4px;")
        runtime_layout.addWidget(self.douyin_runtime_summary)
        layout.addWidget(runtime_group)

        fetch_order_group = QGroupBox("\u6293\u53d6\u987a\u5e8f\u8bbe\u7f6e")
        fetch_order_layout = QVBoxLayout(fetch_order_group)
        self.fetch_order_button = QPushButton("\u8bbe\u7f6e\u6293\u53d6\u987a\u5e8f\u9875")
        self.fetch_order_button.clicked.connect(self._edit_fetch_order_settings)
        fetch_order_layout.addWidget(self.fetch_order_button)
        self.fetch_order_summary = QLabel()
        self.fetch_order_summary.setWordWrap(True)
        self.fetch_order_summary.setStyleSheet("color: #666; padding-left: 4px;")
        fetch_order_layout.addWidget(self.fetch_order_summary)
        layout.addWidget(fetch_order_group)

        self._refresh_fetch_order_summary()

        button_row = QHBoxLayout()
        self.save_button = QPushButton("\u4fdd\u5b58")
        self.cancel_button = QPushButton("\u53d6\u6d88")
        self.save_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

    def _path_row(self, form, label, value):
        row = QHBoxLayout()
        edit = QLineEdit(str(value))
        browse_button = QPushButton("\u9009\u62e9")
        browse_button.clicked.connect(lambda: self._browse_file(edit))
        row.addWidget(edit, stretch=1)
        row.addWidget(browse_button)
        form.addRow(label, row)
        return edit

    def _browse_file(self, edit):
        selected, _ = QFileDialog.getOpenFileName(
            self,
            "\u9009\u62e9\u540d\u5355\u6587\u4ef6",
            str(ROOT_DIR),
            "Text Files (*.txt);;All Files (*)",
        )
        if selected:
            edit.setText(selected)

    def _open_runtime_dialog(self, title, fields, current_values):
        dialog = RuntimeSettingsDialog(self, title, fields, current_values)
        if dialog.exec_() == QDialog.Accepted:
            return dialog.settings()
        return None

    def _edit_bilibili_runtime_settings(self):
        values = self._open_runtime_dialog("B\u7ad9\u51b7\u5374\u4e0e\u9650\u6d41\u53c2\u6570", BILIBILI_RUNTIME_FIELDS, self._bilibili_runtime_settings)
        if values is not None:
            self._bilibili_runtime_settings = values

    def _edit_douyin_runtime_settings(self):
        values = self._open_runtime_dialog("\u6296\u97f3\u51b7\u5374\u4e0e\u9650\u6d41\u53c2\u6570", DOUYIN_RUNTIME_FIELDS, self._douyin_runtime_settings)
        if values is not None:
            self._douyin_runtime_settings = values

    def _edit_fetch_order_settings(self):
        dialog = FetchOrderSettingsDialog(self, self._fetch_order_settings)
        if dialog.exec_() == QDialog.Accepted:
            self._fetch_order_settings = _normalize_fetch_order_settings(dialog.settings())
            self._refresh_fetch_order_summary()

    def _refresh_fetch_order_summary(self):
        bilibili_labels = _fetch_order_option_map(BILIBILI_FETCH_ORDER_OPTIONS)
        douyin_labels = _fetch_order_option_map(DOUYIN_FETCH_ORDER_OPTIONS)
        direction_labels = _fetch_order_option_map(FETCH_ORDER_DIRECTION_OPTIONS)
        bilibili_text = f"B\u7ad9\uff1a\u6309 {bilibili_labels[self._fetch_order_settings['bilibili']['field']]} {direction_labels[self._fetch_order_settings['bilibili']['direction']]}"
        douyin_text = f"\u6296\u97f3\uff1a\u6309 {douyin_labels[self._fetch_order_settings['douyin']['field']]} {direction_labels[self._fetch_order_settings['douyin']['direction']]}"
        self.fetch_order_summary.setText(bilibili_text + "\n" + douyin_text)

    def paths(self):
        return {
            "unfollow": self.unfollow_path_edit.text(),
            "bilibili_uid": self.bilibili_uid_path_edit.text(),
            "douyin_uid": self.douyin_uid_path_edit.text(),
        }

    def bilibili_runtime_settings(self):
        return dict(self._bilibili_runtime_settings)

    def douyin_runtime_settings(self):
        return dict(self._douyin_runtime_settings)

    def fetch_order_settings(self):
        return _normalize_fetch_order_settings(self._fetch_order_settings)


class DouyinStatsDialog(QDialog):
    MODE_ROWS = [
        ("verify", "主页校验模式"),
        ("monitor", "监控模式"),
        ("full", "完整模式"),
    ]

    def __init__(self, parent=None, high_like_threshold=10000):
        super().__init__(parent)
        self.high_like_threshold = int(high_like_threshold or 10000)
        self.setWindowTitle("抖音统计")
        self.resize(620, 360)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("正在读取抖音缓存统计...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 4px 2px; color: #444;")
        layout.addWidget(self.summary_label)

        stats_group = QGroupBox("模式完成度")
        stats_grid = QGridLayout(stats_group)
        stats_grid.addWidget(QLabel("模式"), 0, 0)
        stats_grid.addWidget(QLabel("已抓取博主数量"), 0, 1)
        stats_grid.addWidget(QLabel("完成百分比"), 0, 2)

        self.mode_count_labels = {}
        self.mode_percent_labels = {}
        for row_index, (mode, label) in enumerate(self.MODE_ROWS, start=1):
            title_label = QLabel(label)
            count_label = QLabel("-")
            percent_label = QLabel("-")
            count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            stats_grid.addWidget(title_label, row_index, 0)
            stats_grid.addWidget(count_label, row_index, 1)
            stats_grid.addWidget(percent_label, row_index, 2)
            self.mode_count_labels[mode] = count_label
            self.mode_percent_labels[mode] = percent_label
        layout.addWidget(stats_group)

        video_group = QGroupBox("视频缓存统计")
        video_grid = QGridLayout(video_group)
        video_grid.addWidget(QLabel("指标"), 0, 0)
        video_grid.addWidget(QLabel("数量"), 0, 1)
        video_grid.addWidget(QLabel("占比"), 0, 2)

        video_grid.addWidget(QLabel("当前缓存视频数量"), 1, 0)
        self.cached_video_count_label = QLabel("-")
        self.cached_video_ratio_label = QLabel("100.00%")
        self.cached_video_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.cached_video_ratio_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        video_grid.addWidget(self.cached_video_count_label, 1, 1)
        video_grid.addWidget(self.cached_video_ratio_label, 1, 2)

        video_grid.addWidget(QLabel(f"高赞视频数量（>{self.high_like_threshold}）"), 2, 0)
        self.high_like_video_count_label = QLabel("-")
        self.high_like_video_ratio_label = QLabel("-")
        self.high_like_video_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.high_like_video_ratio_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        video_grid.addWidget(self.high_like_video_count_label, 2, 1)
        video_grid.addWidget(self.high_like_video_ratio_label, 2, 2)
        layout.addWidget(video_group)

        self.refresh_info_label = QLabel("")
        self.refresh_info_label.setStyleSheet("padding: 2px 2px; color: #666;")
        layout.addWidget(self.refresh_info_label)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新数据")
        self.close_button = QPushButton("关闭")
        self.refresh_button.clicked.connect(self.refresh_stats)
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_stats()

    def refresh_stats(self):
        try:
            from douyin_analyzer.analyzer import DouyinHiatusAnalyzer
            from douyin_analyzer.cache import CacheStore
            from douyin_analyzer.config import load_analyzer_config

            config = load_analyzer_config()
            cache_store = CacheStore(config)
            analyzer = DouyinHiatusAnalyzer(config, browser_client=None, cache_store=cache_store)

            followings_payload = cache_store.load_followings_cache_payload()
            progress = cache_store.load_progress()
            cache_rows = analyzer.build_cache_inventory_rows(followings_payload, progress)
            active_rows = [
                row for row in cache_rows
                if str((row or {}).get("has_followings_cache", "")).strip() == "是"
            ]
            total_followings = len(active_rows)
            followings_cached_at = analyzer._format_cached_at(
                (followings_payload or {}).get("cached_at") if isinstance(followings_payload, dict) else ""
            )

            for mode, _ in self.MODE_ROWS:
                flag_key = f"has_{mode}_cache"
                captured_count = sum(
                    1 for row in active_rows
                    if str((row or {}).get(flag_key, "")).strip() == "是"
                )
                percent = (captured_count / total_followings * 100) if total_followings else 0
                self.mode_count_labels[mode].setText(str(captured_count))
                self.mode_percent_labels[mode].setText(f"{percent:.2f}%")

            active_uids = {
                str((row or {}).get("uploader_id") or "").strip()
                for row in active_rows
                if str((row or {}).get("uploader_id") or "").strip()
            }
            cached_video_count = 0
            high_like_video_count = 0
            seen_video_ids = set()
            for uid, entry in (progress or {}).items():
                if str(uid).strip() not in active_uids or not isinstance(entry, dict):
                    continue
                for video in entry.get("videos", []) or []:
                    if not isinstance(video, dict):
                        continue
                    video_id = str(video.get("aweme_id") or video.get("video_id") or "").strip()
                    dedupe_key = video_id or f"{uid}:{len(seen_video_ids)}"
                    if dedupe_key in seen_video_ids:
                        continue
                    seen_video_ids.add(dedupe_key)
                    cached_video_count += 1
                    try:
                        like_count = int(float(video.get("like_count") or 0))
                    except (TypeError, ValueError):
                        like_count = 0
                    if like_count > self.high_like_threshold:
                        high_like_video_count += 1

            high_like_ratio = (
                high_like_video_count / cached_video_count * 100
                if cached_video_count
                else 0
            )
            self.cached_video_count_label.setText(str(cached_video_count))
            self.cached_video_ratio_label.setText("100.00%" if cached_video_count else "0.00%")
            self.high_like_video_count_label.setText(str(high_like_video_count))
            self.high_like_video_ratio_label.setText(f"{high_like_ratio:.2f}%")

            if total_followings:
                self.summary_label.setText(
                    f"当前关注博主总数：{total_followings} 位\n"
                    f"关注列表缓存时间：{followings_cached_at or '暂无'}\n"
                    f"进度缓存条目：{len(progress or {})} 条"
                )
            else:
                self.summary_label.setText(
                    "当前没有可用的抖音关注缓存数据。\n请先运行一次基础统计模式，再查看模式完成度。"
                )

            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取抖音统计失败：{exc}")
            for mode, _ in self.MODE_ROWS:
                self.mode_count_labels[mode].setText("-")
                self.mode_percent_labels[mode].setText("-")
            self.cached_video_count_label.setText("-")
            self.cached_video_ratio_label.setText("-")
            self.high_like_video_count_label.setText("-")
            self.high_like_video_ratio_label.setText("-")
            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )


class DouyinStatsDialogV2(QDialog):
    MODE_ROWS = [
        ("verify", "主页校验模式"),
        ("monitor", "监控模式"),
        ("full", "完整模式"),
    ]
    CREATOR_VIDEO_BUCKETS = [
        ("0~50", 0, 50),
        ("51~300", 51, 300),
        ("301~500", 301, 500),
        ("501~1000", 501, 1000),
        ("1001以上", 1001, None),
    ]
    VIDEO_DURATION_BUCKETS = [
        ("0~20s", 0, 20),
        ("21~60s", 21, 60),
        ("61s以上", 61, None),
    ]

    def __init__(self, parent=None, high_like_threshold=10000):
        super().__init__(parent)
        self.high_like_threshold = int(high_like_threshold or 10000)
        self.setWindowTitle("抖音统计")
        self.resize(720, 560)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("正在读取抖音缓存统计...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 4px 2px; color: #444;")
        layout.addWidget(self.summary_label)

        stats_group = QGroupBox("模式完成度")
        stats_grid = QGridLayout(stats_group)
        stats_grid.addWidget(QLabel("模式"), 0, 0)
        stats_grid.addWidget(QLabel("已抓取博主数量"), 0, 1)
        stats_grid.addWidget(QLabel("完成百分比"), 0, 2)

        self.mode_count_labels = {}
        self.mode_percent_labels = {}
        for row_index, (mode, label) in enumerate(self.MODE_ROWS, start=1):
            title_label = QLabel(label)
            count_label = QLabel("-")
            percent_label = QLabel("-")
            count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            percent_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            stats_grid.addWidget(title_label, row_index, 0)
            stats_grid.addWidget(count_label, row_index, 1)
            stats_grid.addWidget(percent_label, row_index, 2)
            self.mode_count_labels[mode] = count_label
            self.mode_percent_labels[mode] = percent_label
        layout.addWidget(stats_group)

        video_group = QGroupBox("视频缓存统计")
        video_grid = QGridLayout(video_group)
        video_grid.addWidget(QLabel("指标"), 0, 0)
        video_grid.addWidget(QLabel("数量"), 0, 1)
        video_grid.addWidget(QLabel("占比"), 0, 2)

        video_grid.addWidget(QLabel("当前缓存视频数量"), 1, 0)
        self.cached_video_count_label = QLabel("-")
        self.cached_video_ratio_label = QLabel("100.00%")
        self.cached_video_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.cached_video_ratio_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        video_grid.addWidget(self.cached_video_count_label, 1, 1)
        video_grid.addWidget(self.cached_video_ratio_label, 1, 2)

        video_grid.addWidget(QLabel(f"高赞视频数量（>{self.high_like_threshold}）"), 2, 0)
        self.high_like_video_count_label = QLabel("-")
        self.high_like_video_ratio_label = QLabel("-")
        self.high_like_video_count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        self.high_like_video_ratio_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
        video_grid.addWidget(self.high_like_video_count_label, 2, 1)
        video_grid.addWidget(self.high_like_video_ratio_label, 2, 2)
        layout.addWidget(video_group)

        creator_group = QGroupBox("博主视频数分布")
        creator_grid = QGridLayout(creator_group)
        creator_grid.addWidget(QLabel("区间"), 0, 0)
        creator_grid.addWidget(QLabel("博主数量"), 0, 1)
        creator_grid.addWidget(QLabel("占比"), 0, 2)

        self.creator_bucket_count_labels = {}
        self.creator_bucket_ratio_labels = {}
        for row_index, (label, _, _) in enumerate(self.CREATOR_VIDEO_BUCKETS, start=1):
            range_label = QLabel(label)
            count_label = QLabel("-")
            ratio_label = QLabel("-")
            count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            ratio_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            creator_grid.addWidget(range_label, row_index, 0)
            creator_grid.addWidget(count_label, row_index, 1)
            creator_grid.addWidget(ratio_label, row_index, 2)
            self.creator_bucket_count_labels[label] = count_label
            self.creator_bucket_ratio_labels[label] = ratio_label
        layout.addWidget(creator_group)

        duration_group = QGroupBox("缓存视频时长分布")
        duration_grid = QGridLayout(duration_group)
        duration_grid.addWidget(QLabel("区间"), 0, 0)
        duration_grid.addWidget(QLabel("视频数量"), 0, 1)
        duration_grid.addWidget(QLabel("占比"), 0, 2)

        self.duration_bucket_count_labels = {}
        self.duration_bucket_ratio_labels = {}
        for row_index, (label, _, _) in enumerate(self.VIDEO_DURATION_BUCKETS, start=1):
            range_label = QLabel(label)
            count_label = QLabel("-")
            ratio_label = QLabel("-")
            count_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            ratio_label.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
            duration_grid.addWidget(range_label, row_index, 0)
            duration_grid.addWidget(count_label, row_index, 1)
            duration_grid.addWidget(ratio_label, row_index, 2)
            self.duration_bucket_count_labels[label] = count_label
            self.duration_bucket_ratio_labels[label] = ratio_label
        layout.addWidget(duration_group)

        self.refresh_info_label = QLabel("")
        self.refresh_info_label.setStyleSheet("padding: 2px 2px; color: #666;")
        layout.addWidget(self.refresh_info_label)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新数据")
        self.close_button = QPushButton("关闭")
        self.refresh_button.clicked.connect(self.refresh_stats)
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_stats()

    @staticmethod
    def _safe_int(value, default=0):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _bucket_match(value, lower, upper):
        if value < lower:
            return False
        if upper is None:
            return True
        return value <= upper

    def refresh_stats(self):
        try:
            from douyin_analyzer.analyzer import DouyinHiatusAnalyzer
            from douyin_analyzer.cache import CacheStore
            from douyin_analyzer.config import load_analyzer_config

            config = load_analyzer_config()
            cache_store = CacheStore(config)
            analyzer = DouyinHiatusAnalyzer(config, browser_client=None, cache_store=cache_store)

            followings_payload = cache_store.load_followings_cache_payload()
            progress = cache_store.load_progress()
            cache_rows = analyzer.build_cache_inventory_rows(followings_payload, progress)
            active_rows = [
                row for row in cache_rows
                if str((row or {}).get("has_followings_cache", "")).strip() == "是"
            ]
            total_followings = len(active_rows)
            followings_cached_at = analyzer._format_cached_at(
                (followings_payload or {}).get("cached_at") if isinstance(followings_payload, dict) else ""
            )

            for mode, _ in self.MODE_ROWS:
                flag_key = f"has_{mode}_cache"
                captured_count = sum(
                    1 for row in active_rows
                    if str((row or {}).get(flag_key, "")).strip() == "是"
                )
                percent = (captured_count / total_followings * 100) if total_followings else 0
                self.mode_count_labels[mode].setText(str(captured_count))
                self.mode_percent_labels[mode].setText(f"{percent:.2f}%")

            creator_bucket_counts = {label: 0 for label, _, _ in self.CREATOR_VIDEO_BUCKETS}
            for row in active_rows:
                published_video_count = self._safe_int((row or {}).get("published_video_count"), 0)
                for label, lower, upper in self.CREATOR_VIDEO_BUCKETS:
                    if self._bucket_match(published_video_count, lower, upper):
                        creator_bucket_counts[label] += 1
                        break

            active_uids = {
                str((row or {}).get("uploader_id") or "").strip()
                for row in active_rows
                if str((row or {}).get("uploader_id") or "").strip()
            }
            cached_video_count = 0
            high_like_video_count = 0
            duration_bucket_counts = {label: 0 for label, _, _ in self.VIDEO_DURATION_BUCKETS}
            seen_video_ids = set()
            for uid, entry in (progress or {}).items():
                if str(uid).strip() not in active_uids or not isinstance(entry, dict):
                    continue
                for video in entry.get("videos", []) or []:
                    if not isinstance(video, dict):
                        continue
                    video_id = str(video.get("aweme_id") or video.get("video_id") or "").strip()
                    dedupe_key = video_id or f"{uid}:{len(seen_video_ids)}"
                    if dedupe_key in seen_video_ids:
                        continue
                    seen_video_ids.add(dedupe_key)
                    cached_video_count += 1

                    like_count = self._safe_int(video.get("like_count"), 0)
                    if like_count > self.high_like_threshold:
                        high_like_video_count += 1

                    duration_seconds = self._safe_int(video.get("duration_seconds"), 0)
                    for label, lower, upper in self.VIDEO_DURATION_BUCKETS:
                        if self._bucket_match(duration_seconds, lower, upper):
                            duration_bucket_counts[label] += 1
                            break

            high_like_ratio = (
                high_like_video_count / cached_video_count * 100
                if cached_video_count
                else 0
            )
            self.cached_video_count_label.setText(str(cached_video_count))
            self.cached_video_ratio_label.setText("100.00%" if cached_video_count else "0.00%")
            self.high_like_video_count_label.setText(str(high_like_video_count))
            self.high_like_video_ratio_label.setText(f"{high_like_ratio:.2f}%")

            for label, _, _ in self.CREATOR_VIDEO_BUCKETS:
                bucket_count = creator_bucket_counts.get(label, 0)
                bucket_ratio = (bucket_count / total_followings * 100) if total_followings else 0
                self.creator_bucket_count_labels[label].setText(str(bucket_count))
                self.creator_bucket_ratio_labels[label].setText(f"{bucket_ratio:.2f}%")

            for label, _, _ in self.VIDEO_DURATION_BUCKETS:
                bucket_count = duration_bucket_counts.get(label, 0)
                bucket_ratio = (bucket_count / cached_video_count * 100) if cached_video_count else 0
                self.duration_bucket_count_labels[label].setText(str(bucket_count))
                self.duration_bucket_ratio_labels[label].setText(f"{bucket_ratio:.2f}%")

            if total_followings:
                self.summary_label.setText(
                    f"当前关注博主总数：{total_followings} 位\n"
                    f"关注列表缓存时间：{followings_cached_at or '暂无'}\n"
                    f"进度缓存条目：{len(progress or {})} 条"
                )
            else:
                self.summary_label.setText(
                    "当前没有可用的抖音关注缓存数据。\n请先运行一次基础统计模式，再查看统计信息。"
                )

            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取抖音统计失败：{exc}")
            for mode, _ in self.MODE_ROWS:
                self.mode_count_labels[mode].setText("-")
                self.mode_percent_labels[mode].setText("-")
            self.cached_video_count_label.setText("-")
            self.cached_video_ratio_label.setText("-")
            self.high_like_video_count_label.setText("-")
            self.high_like_video_ratio_label.setText("-")
            for label, _, _ in self.CREATOR_VIDEO_BUCKETS:
                self.creator_bucket_count_labels[label].setText("-")
                self.creator_bucket_ratio_labels[label].setText("-")
            for label, _, _ in self.VIDEO_DURATION_BUCKETS:
                self.duration_bucket_count_labels[label].setText("-")
                self.duration_bucket_ratio_labels[label].setText("-")
            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )


class DouyinRatingOverviewDialog(QDialog):
    CREATOR_TABLE = "creator_score_current"
    VIDEO_TABLE = "video_score_current"
    GRADE_ORDER = ("S", "A", "B", "C", "D")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("抖音评分概览")
        self.resize(1040, 720)

        from douyin_analyzer.config import load_analyzer_config

        self.config = load_analyzer_config()
        self.db_path = Path(self.config.export_store_db)
        self.csv_paths = {
            "up_score": Path(self.config.creator_score_csv),
            "video_score": Path(self.config.video_score_csv),
            "up_summary": Path(self.config.compact_creator_csv),
            "video_summary": Path(self.config.compact_video_csv),
        }

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("正在读取评分数据...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 4px 2px; color: #444;")
        layout.addWidget(self.summary_label)

        summary_group = QGroupBox("等级分布")
        summary_grid = QGridLayout(summary_group)
        headers = ["对象", "总数", "S", "A", "B", "C", "D", "低/中置信度"]
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet("font-weight: 700;")
            summary_grid.addWidget(label, 0, column)

        self.summary_cells = {}
        for row, key in enumerate(("creator", "video"), start=1):
            title = "UP主" if key == "creator" else "视频"
            summary_grid.addWidget(QLabel(title), row, 0)
            for column, name in enumerate(("total", *self.GRADE_ORDER, "low_confidence"), start=1):
                cell = QLabel("-")
                cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                summary_grid.addWidget(cell, row, column)
                self.summary_cells[(key, name)] = cell
        layout.addWidget(summary_group)

        self.tabs = QTabWidget()
        self.creator_top_table = self._make_table(
            ["UP主", "等级", "分数", "置信度", "粉丝数", "视频数", "评分原因"]
        )
        self.creator_low_table = self._make_table(
            ["UP主", "等级", "分数", "置信度", "未更新天数", "低等级比例", "评分原因"]
        )
        self.video_top_table = self._make_table(
            ["视频标题", "UP主", "等级", "分数", "置信度", "点赞数", "下载状态", "评分原因"]
        )
        self.video_watch_table = self._make_table(
            ["视频标题", "UP主", "等级", "分数", "置信度", "缺失指标", "评分原因"]
        )
        self.tabs.addTab(self.creator_top_table, "高分UP")
        self.tabs.addTab(self.creator_low_table, "低分/风险UP")
        self.tabs.addTab(self.video_top_table, "高分视频")
        self.tabs.addTab(self.video_watch_table, "待观察视频")
        layout.addWidget(self.tabs, stretch=1)

        self.refresh_info_label = QLabel("")
        self.refresh_info_label.setStyleSheet("padding: 2px 2px; color: #666;")
        layout.addWidget(self.refresh_info_label)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.open_creator_score_button = QPushButton("打开UP评分CSV")
        self.open_video_score_button = QPushButton("打开视频评分CSV")
        self.open_creator_summary_button = QPushButton("打开UP精简表")
        self.open_video_summary_button = QPushButton("打开视频精简表")
        self.close_button = QPushButton("关闭")

        self.refresh_button.clicked.connect(self.refresh_data)
        self.open_creator_score_button.clicked.connect(lambda: self._open_path("up_score"))
        self.open_video_score_button.clicked.connect(lambda: self._open_path("video_score"))
        self.open_creator_summary_button.clicked.connect(lambda: self._open_path("up_summary"))
        self.open_video_summary_button.clicked.connect(lambda: self._open_path("video_summary"))
        self.close_button.clicked.connect(self.accept)

        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.open_creator_score_button)
        button_row.addWidget(self.open_video_score_button)
        button_row.addWidget(self.open_creator_summary_button)
        button_row.addWidget(self.open_video_summary_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_data()

    @staticmethod
    def _make_table(headers):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setWordWrap(False)
        table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        table.horizontalHeader().setStretchLastSection(True)
        table.verticalHeader().setVisible(False)
        return table

    @staticmethod
    def _table_exists(conn, table_name):
        row = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (table_name,),
        ).fetchone()
        return row is not None

    @staticmethod
    def _fmt(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.2f}"
        text = str(value)
        return text if len(text) <= 120 else text[:117] + "..."

    def _grade_counts(self, conn, table, column):
        counts = {grade: 0 for grade in self.GRADE_ORDER}
        total = 0
        for grade, count in conn.execute(
            f'SELECT "{column}", COUNT(*) FROM {table} GROUP BY "{column}"'
        ):
            grade_text = str(grade or "").strip().upper()
            if grade_text in counts:
                counts[grade_text] += int(count or 0)
            total += int(count or 0)
        return total, counts

    def _confidence_count(self, conn, table, column, values):
        placeholders = ",".join("?" for _ in values)
        row = conn.execute(
            f'SELECT COUNT(*) FROM {table} WHERE "{column}" IN ({placeholders})',
            tuple(values),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _query_rows(self, conn, sql, limit=30):
        return conn.execute(sql, (limit,)).fetchall()

    def _populate_table(self, table, rows):
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                item = QTableWidgetItem(self._fmt(value))
                item.setToolTip(self._fmt(value))
                table.setItem(row_index, column_index, item)
        table.resizeColumnsToContents()
        table.horizontalHeader().setStretchLastSection(True)

    def _clear_tables(self):
        for table in (
            self.creator_top_table,
            self.creator_low_table,
            self.video_top_table,
            self.video_watch_table,
        ):
            table.setRowCount(0)

    def refresh_data(self):
        if not self.db_path.exists():
            self.summary_label.setText(f"未找到评分数据库：{self.db_path}")
            self._clear_tables()
            return

        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                has_creator = self._table_exists(conn, self.CREATOR_TABLE)
                has_video = self._table_exists(conn, self.VIDEO_TABLE)

                if not has_creator and not has_video:
                    self.summary_label.setText(
                        "还没有生成评分数据。请先在主界面运行“抖音视频评分”和“抖音UP主评分”。"
                    )
                    self._clear_tables()
                    return

                if has_creator:
                    total, counts = self._grade_counts(conn, self.CREATOR_TABLE, "UP最终等级")
                    low_confidence = self._confidence_count(
                        conn, self.CREATOR_TABLE, "评级置信度", ["低", "中"]
                    )
                    self._set_summary("creator", total, counts, low_confidence)
                    self._populate_table(
                        self.creator_top_table,
                        self._query_rows(
                            conn,
                            """
                            SELECT "UP主姓名", "UP最终等级", "UP最终分", "评级置信度",
                                   "粉丝数", "已评分视频数", "评分原因"
                            FROM creator_score_current
                            ORDER BY CAST("UP最终分" AS REAL) DESC
                            LIMIT ?
                            """,
                        ),
                    )
                    self._populate_table(
                        self.creator_low_table,
                        self._query_rows(
                            conn,
                            """
                            SELECT "UP主姓名", "UP最终等级", "UP最终分", "评级置信度",
                                   "未更新天数", "低等级视频比例", "评分原因"
                            FROM creator_score_current
                            WHERE "UP最终等级" IN ('C', 'D')
                               OR "评级置信度" IN ('低', '中')
                            ORDER BY CAST("UP最终分" AS REAL) ASC
                            LIMIT ?
                            """,
                        ),
                    )
                else:
                    self._set_summary("creator", 0, {}, 0)
                    self.creator_top_table.setRowCount(0)
                    self.creator_low_table.setRowCount(0)

                if has_video:
                    total, counts = self._grade_counts(conn, self.VIDEO_TABLE, "视频最终等级")
                    low_confidence = self._confidence_count(
                        conn, self.VIDEO_TABLE, "评分置信度", ["很低", "低", "中"]
                    )
                    self._set_summary("video", total, counts, low_confidence)
                    self._populate_table(
                        self.video_top_table,
                        self._query_rows(
                            conn,
                            """
                            SELECT "视频标题", "UP主姓名", "视频最终等级", "视频最终分",
                                   "评分置信度", "点赞数", "下载状态", "评分原因"
                            FROM video_score_current
                            ORDER BY CAST("视频最终分" AS REAL) DESC
                            LIMIT ?
                            """,
                        ),
                    )
                    self._populate_table(
                        self.video_watch_table,
                        self._query_rows(
                            conn,
                            """
                            SELECT "视频标题", "UP主姓名", "视频最终等级", "视频最终分",
                                   "评分置信度", "缺失指标", "评分原因"
                            FROM video_score_current
                            WHERE "评分置信度" IN ('很低', '低', '中')
                               OR "评分状态" LIKE '%观察%'
                            ORDER BY CAST("视频最终分" AS REAL) DESC
                            LIMIT ?
                            """,
                        ),
                    )
                else:
                    self._set_summary("video", 0, {}, 0)
                    self.video_top_table.setRowCount(0)
                    self.video_watch_table.setRowCount(0)

            self.summary_label.setText(
                f"评分数据来源：{self.db_path}\n"
                "S级只代表手动偏好；自动评分最高为A级。这里展示的是当前 SQLite 快照。"
            )
            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取评分概览失败：{exc}")
            self._clear_tables()

    def _set_summary(self, key, total, counts, low_confidence):
        self.summary_cells[(key, "total")].setText(str(total))
        for grade in self.GRADE_ORDER:
            self.summary_cells[(key, grade)].setText(str(int((counts or {}).get(grade, 0))))
        self.summary_cells[(key, "low_confidence")].setText(str(low_confidence))

    def _open_path(self, key):
        path = self.csv_paths.get(key)
        if not path:
            return
        if not path.exists():
            QMessageBox.warning(self, "文件不存在", f"还没有找到这个文件：\n{path}")
            return
        try:
            os.startfile(str(path))
        except Exception as exc:
            QMessageBox.warning(self, "打开失败", f"无法打开文件：\n{path}\n\n{exc}")


class MainWindow(QMainWindow):
    BILIBILI_MODE_OPTIONS = [
        ("精确模式（主榜 + 视频分析）", "precise_full"),
        ("精确模式（仅主榜）", "precise_main_only"),
        ("回退模式（主榜 + 视频分析）", "fallback_full"),
        ("回退模式（仅主榜）", "fallback_main_only"),
    ]
    PLATFORM_OPTIONS = [
        ("B站 + 抖音", "both"),
        ("仅 B站", "bilibili"),
        ("仅 抖音", "douyin"),
        ("抖音取消关注", "douyin_unfollow"),
        ("B站 UID 全量视频", "bilibili_uid"),
        ("抖音 UID 全量视频", "douyin_uid"),
        ("导出抖音高赞视频", "douyin_high_like"),
        ("抖音视频评分", "douyin_video_score"),
        ("抖音UP主评分", "douyin_creator_score"),
        ("导出抖音精简表", "douyin_compact_export"),
    ]
    ACTION_OPTIONS = [
        ("仅抓取", "fetch"),
        ("抓取并上传飞书", "fetch_upload"),
        ("仅上传飞书", "upload"),
    ]

    def __init__(self):
        super().__init__()
        self.worker = None
        self.cookie_checker = None
        self.config_locked = False
        self.unfollow_list_path = str(DEFAULT_DOUYIN_UNFOLLOW_LIST)
        self.bilibili_uid_list_path = str(DEFAULT_BILIBILI_UID_LIST)
        self.douyin_uid_list_path = str(DEFAULT_DOUYIN_UID_LIST)
        self.bilibili_runtime_settings = _load_default_bilibili_runtime_settings()
        self.douyin_runtime_settings = _load_default_douyin_runtime_settings()
        self.fetch_order_settings = _load_default_fetch_order_settings()
        self._progress_current = 0
        self._progress_total = 0
        self._progress_running = False
        self.setWindowTitle("")
        self.resize(1100, 760)
        self._build_ui()
        self._load_gui_config()
        self._sync_visible_options()

    def _build_ui(self):
        root = QWidget(self)
        layout = QVBoxLayout(root)

        config_layout = QGridLayout()
        layout.addLayout(config_layout)

        run_group = QGroupBox("运行配置")
        run_form = QFormLayout(run_group)
        self.platform_combo = QComboBox()
        for label, value in self.PLATFORM_OPTIONS:
            self.platform_combo.addItem(label, value)
        self.platform_combo.currentIndexChanged.connect(self._sync_visible_options)
        run_form.addRow("平台/模式", self.platform_combo)

        self.action_combo = QComboBox()
        for label, value in self.ACTION_OPTIONS:
            self.action_combo.addItem(label, value)
        run_form.addRow("动作", self.action_combo)

        self.bilibili_mode_combo = QComboBox()
        for label, mode in self.BILIBILI_MODE_OPTIONS:
            self.bilibili_mode_combo.addItem(label, mode)
        self.bilibili_mode_combo.setCurrentIndex(0)
        run_form.addRow("B站抓取模式", self.bilibili_mode_combo)

        self.douyin_mode_combo = QComboBox()
        for label, mode in (
            ("基础统计模式（粉丝数/获赞总数/视频数）", "counts"),
            ("主页校验模式（按基础缓存进主页核对）", "verify"),
            ("监控模式（推荐日常使用）", "monitor"),
            ("增量模式（只补变化数据）", "delta"),
            ("完整模式（抓取视频明细）", "full"),
        ):
            self.douyin_mode_combo.addItem(label, mode)
        self.douyin_mode_combo.setCurrentIndex(2)
        self.douyin_mode_combo.currentIndexChanged.connect(self._sync_visible_options)
        run_form.addRow("抖音抓取模式", self.douyin_mode_combo)

        self.monitor_video_limit_spin = QSpinBox()
        self.monitor_video_limit_spin.setRange(1, 500)
        self.monitor_video_limit_spin.setValue(10)
        self.monitor_video_limit_spin.setToolTip("监控/增量模式下，每位博主最多抓取最近 N 条视频。基础统计和完整模式会忽略该参数。")
        run_form.addRow("监控视频数", self.monitor_video_limit_spin)

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("DrissionPage", "drission")
        self.backend_combo.addItem("Playwright", "playwright")
        run_form.addRow("抖音浏览器后端", self.backend_combo)

        config_layout.addWidget(run_group, 0, 0)

        uid_group = QGroupBox("UID 与筛选参数")
        uid_form = QFormLayout(uid_group)
        self.uid_fetch_all_radio = QRadioButton("全抓取模式")
        self.uid_fetch_all_radio.setToolTip("抓取 UID 名单中的全部博主。")
        self.uid_fetch_partial_radio = QRadioButton("部分抓取模式")
        self.uid_fetch_partial_radio.setToolTip(
            "只抓取排序后的前 N 个 UID。已存在博主会更新，新博主会添加，未涉及博主保持不变。"
        )
        self.uid_fetch_all_radio.setChecked(True)
        self.uid_fetch_all_radio.toggled.connect(self._sync_visible_options)
        self.uid_fetch_partial_radio.toggled.connect(self._sync_visible_options)
        self.uid_limit_spin = QSpinBox()
        self.uid_limit_spin.setRange(1, 100000)
        self.uid_limit_spin.setValue(100)
        self.uid_limit_spin.setToolTip("部分抓取模式下生效；全抓取模式会忽略该数量。")
        fetch_mode_row = QHBoxLayout()
        fetch_mode_row.addWidget(self.uid_fetch_all_radio)
        fetch_mode_row.addWidget(self.uid_fetch_partial_radio)
        fetch_mode_row.addStretch(1)
        uid_form.addRow("抓取方式", fetch_mode_row)
        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("抓取前 N 个 UID"))
        limit_row.addWidget(self.uid_limit_spin)
        limit_row.addStretch(1)
        uid_form.addRow("UID 数量", limit_row)

        self.high_like_spin = QSpinBox()
        self.high_like_spin.setRange(1, 100000000)
        self.high_like_spin.setValue(10000)
        self.high_like_spin.setToolTip("导出抖音高赞视频时使用，其它模式不会使用该参数。")
        uid_form.addRow("高赞阈值", self.high_like_spin)
        config_layout.addWidget(uid_group, 0, 1)

        button_row = QHBoxLayout()
        self.start_button = QPushButton("开始运行")
        self.start_button.setStyleSheet("font-size: 16px; font-weight: 700; padding: 10px;")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("终止运行")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_stop)
        self.high_like_export_button = QPushButton("导出高赞视频")
        self.high_like_export_button.clicked.connect(self._start_high_like_export)
        self.video_download_button = QPushButton("视频下载")
        self.video_download_button.clicked.connect(self._open_video_downloader_gui)
        self.unfollow_cleanup_button = QPushButton("清理非当前关注缓存")
        self.unfollow_cleanup_button.clicked.connect(self._start_douyin_non_followed_cache_cleanup)
        self.douyin_stats_button = QPushButton("抖音统计")
        self.douyin_stats_button.clicked.connect(self._open_douyin_stats)
        self.rating_overview_button = QPushButton("评分概览")
        self.rating_overview_button.clicked.connect(self._open_rating_overview)
        self.cookie_check_button = QPushButton("检测 B站 Cookie")
        self.cookie_check_button.clicked.connect(self._check_bilibili_cookie)
        self.advanced_button = QPushButton("高级设置")
        self.advanced_button.clicked.connect(self._open_advanced_settings)
        self.lock_button = QPushButton("锁定配置")
        self.lock_button.clicked.connect(self._toggle_config_lock)
        self.clear_button = QPushButton("清空日志")
        self.clear_button.clicked.connect(lambda: self.log_text.clear())
        button_row.addWidget(self.start_button)
        button_row.addWidget(self.stop_button)
        button_row.addWidget(self.high_like_export_button)
        button_row.addWidget(self.video_download_button)
        button_row.addWidget(self.unfollow_cleanup_button)
        button_row.addWidget(self.douyin_stats_button)
        button_row.addWidget(self.rating_overview_button)
        button_row.addWidget(self.cookie_check_button)
        button_row.addWidget(self.advanced_button)
        button_row.addWidget(self.lock_button)
        button_row.addWidget(self.clear_button)
        layout.addLayout(button_row)

        self.cookie_status_label = QLabel("B站 Cookie：未检测")
        self.cookie_status_label.setStyleSheet("padding: 2px 0 8px 2px; color: #666;")
        layout.addWidget(self.cookie_status_label)

        progress_group = QGroupBox("抓取进度")
        progress_layout = QVBoxLayout(progress_group)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("等待开始")
        self.progress_bar.setStyleSheet("QProgressBar { min-height: 20px; }")
        self.progress_label = QLabel("请选择配置后点击开始运行")
        self.progress_label.setStyleSheet("padding: 2px 0 0 2px; color: #555;")
        progress_layout.addWidget(self.progress_bar)
        progress_layout.addWidget(self.progress_label)
        layout.addWidget(progress_group)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)
        self.log_text.setStyleSheet(
            "font-family: Consolas, 'Microsoft YaHei UI'; font-size: 12px; background: #10141f; color: #d7e0f0;"
        )
        layout.addWidget(self.log_text, stretch=1)

        self.setCentralWidget(root)

    def _sync_visible_options(self):
        platform = self.platform_combo.currentData()
        is_normal = platform in {"both", "bilibili", "douyin"}
        is_bilibili = platform in {"both", "bilibili"}
        is_douyin = platform in {"both", "douyin", "douyin_unfollow", "douyin_uid"}
        is_recent_video_mode = self.douyin_mode_combo.currentData() in {"monitor", "delta"}

        editable = not self.config_locked
        self.action_combo.setEnabled(editable and is_normal)
        self.bilibili_mode_combo.setEnabled(editable and is_bilibili)
        self.douyin_mode_combo.setEnabled(editable and is_douyin and platform != "douyin_unfollow")
        self.monitor_video_limit_spin.setEnabled(editable and is_douyin and is_recent_video_mode)
        self.backend_combo.setEnabled(editable and is_douyin)
        self.platform_combo.setEnabled(editable)
        self.uid_fetch_all_radio.setEnabled(editable)
        self.uid_fetch_partial_radio.setEnabled(editable)
        self.uid_limit_spin.setEnabled(editable and self.uid_fetch_partial_radio.isChecked())
        self.high_like_spin.setEnabled(editable)
        self.high_like_export_button.setEnabled(editable)
        self.advanced_button.setEnabled(editable)

        self.lock_button.setText("解除锁定" if self.config_locked else "锁定配置")

    def _show_info_dialog(self, title, message):
        QMessageBox.information(self, title, message)

    def _show_warning_dialog(self, title, message):
        QMessageBox.warning(self, title, message)

    def _show_error_dialog(self, title, message):
        QMessageBox.critical(self, title, message)

    def _collect_config(self):
        return RunConfig(
            platform=self.platform_combo.currentData(),
            action=self.action_combo.currentData(),
            bilibili_mode=self.bilibili_mode_combo.currentData(),
            douyin_fetch_mode=self.douyin_mode_combo.currentData(),
            douyin_backend=self.backend_combo.currentData(),
            monitor_video_limit=self.monitor_video_limit_spin.value(),
            uid_limit_enabled=self.uid_fetch_partial_radio.isChecked(),
            uid_limit=self.uid_limit_spin.value(),
            high_like_threshold=self.high_like_spin.value(),
            unfollow_list_path=Path(self.unfollow_list_path).expanduser(),
            bilibili_uid_list_path=Path(self.bilibili_uid_list_path).expanduser(),
            douyin_uid_list_path=Path(self.douyin_uid_list_path).expanduser(),
            bilibili_runtime_settings=dict(self.bilibili_runtime_settings),
            douyin_runtime_settings=dict(self.douyin_runtime_settings),
            fetch_order_settings=_normalize_fetch_order_settings(self.fetch_order_settings),
        )

    def _combo_index_by_data(self, combo, value):
        for index in range(combo.count()):
            if combo.itemData(index) == value:
                return index
        return -1

    def _snapshot_gui_config(self):
        return {
            "locked": self.config_locked,
            "platform": self.platform_combo.currentData(),
            "action": self.action_combo.currentData(),
            "bilibili_mode": self.bilibili_mode_combo.currentData(),
            "douyin_fetch_mode": self.douyin_mode_combo.currentData(),
            "douyin_backend": self.backend_combo.currentData(),
            "monitor_video_limit": self.monitor_video_limit_spin.value(),
            "uid_limit_enabled": self.uid_fetch_partial_radio.isChecked(),
            "uid_limit": self.uid_limit_spin.value(),
            "high_like_threshold": self.high_like_spin.value(),
            "unfollow_list_path": self.unfollow_list_path,
            "bilibili_uid_list_path": self.bilibili_uid_list_path,
            "douyin_uid_list_path": self.douyin_uid_list_path,
            "bilibili_runtime_settings": self.bilibili_runtime_settings,
            "douyin_runtime_settings": self.douyin_runtime_settings,
            "fetch_order_settings": self.fetch_order_settings,
        }

    def _save_gui_config(self):
        atomic_write_json(GUI_CONFIG_PATH, self._snapshot_gui_config(), indent=2)

    def _load_gui_config(self):
        if not GUI_CONFIG_PATH.exists():
            return
        try:
            with GUI_CONFIG_PATH.open("r", encoding="utf-8") as config_file:
                data = json.load(config_file)
        except Exception:
            return

        for combo, key in (
            (self.platform_combo, "platform"),
            (self.action_combo, "action"),
            (self.bilibili_mode_combo, "bilibili_mode"),
            (self.douyin_mode_combo, "douyin_fetch_mode"),
            (self.backend_combo, "douyin_backend"),
        ):
            index = self._combo_index_by_data(combo, data.get(key))
            if index >= 0:
                combo.setCurrentIndex(index)

        if bool(data.get("uid_limit_enabled", False)):
            self.uid_fetch_partial_radio.setChecked(True)
        else:
            self.uid_fetch_all_radio.setChecked(True)
        self.uid_limit_spin.setValue(int(data.get("uid_limit", self.uid_limit_spin.value()) or self.uid_limit_spin.value()))
        self.monitor_video_limit_spin.setValue(
            int(data.get("monitor_video_limit", self.monitor_video_limit_spin.value()) or self.monitor_video_limit_spin.value())
        )
        self.high_like_spin.setValue(
            int(data.get("high_like_threshold", self.high_like_spin.value()) or self.high_like_spin.value())
        )
        self.unfollow_list_path = data.get("unfollow_list_path") or str(DEFAULT_DOUYIN_UNFOLLOW_LIST)
        self.bilibili_uid_list_path = data.get("bilibili_uid_list_path") or str(DEFAULT_BILIBILI_UID_LIST)
        self.douyin_uid_list_path = data.get("douyin_uid_list_path") or str(DEFAULT_DOUYIN_UID_LIST)

        saved_bilibili = data.get("bilibili_runtime_settings", {}) or {}
        for name, _env_name, _label, field_type, _minimum, _maximum, _step in BILIBILI_RUNTIME_FIELDS:
            self.bilibili_runtime_settings[name] = _coerce_setting_value(
                saved_bilibili.get(name),
                field_type,
                self.bilibili_runtime_settings.get(name),
            )

        saved_douyin = data.get("douyin_runtime_settings", {}) or {}
        for name, _env_name, _label, field_type, _minimum, _maximum, _step in DOUYIN_RUNTIME_FIELDS:
            self.douyin_runtime_settings[name] = _coerce_setting_value(
                saved_douyin.get(name),
                field_type,
                self.douyin_runtime_settings.get(name),
            )

        self.fetch_order_settings = _normalize_fetch_order_settings(
            data.get("fetch_order_settings", self.fetch_order_settings)
        )

        self.config_locked = bool(data.get("locked", False))

    def _open_advanced_settings(self):
        dialog = AdvancedSettingsDialog(
            self,
            {
                "unfollow": self.unfollow_list_path,
                "bilibili_uid": self.bilibili_uid_list_path,
                "douyin_uid": self.douyin_uid_list_path,
            },
            dict(self.bilibili_runtime_settings),
            dict(self.douyin_runtime_settings),
            _normalize_fetch_order_settings(self.fetch_order_settings),
        )
        if dialog.exec_() == QDialog.Accepted:
            paths = dialog.paths()
            self.unfollow_list_path = paths["unfollow"]
            self.bilibili_uid_list_path = paths["bilibili_uid"]
            self.douyin_uid_list_path = paths["douyin_uid"]
            self.bilibili_runtime_settings = dialog.bilibili_runtime_settings()
            self.douyin_runtime_settings = dialog.douyin_runtime_settings()
            self.fetch_order_settings = dialog.fetch_order_settings()
            self._save_gui_config()
            self._append_log("\u9ad8\u7ea7\u8bbe\u7f6e\u5df2\u4fdd\u5b58\u3002")

    def _toggle_config_lock(self):
        if self.config_locked:
            self.config_locked = False
            self._save_gui_config()
            self._append_log("配置已解除锁定，可以修改参数。")
        else:
            self.config_locked = True
            self._save_gui_config()
            self._append_log(f"配置已锁定，后续将按当前参数运行。配置文件: {GUI_CONFIG_PATH}")
        self._sync_visible_options()

    def _open_douyin_stats(self):
        dialog = DouyinStatsDialogV2(self, high_like_threshold=self.high_like_spin.value())
        dialog.exec_()

    def _open_rating_overview(self):
        dialog = DouyinRatingOverviewDialog(self)
        dialog.exec_()

    def _start(self):
        if self.worker and self.worker.isRunning():
            self._show_info_dialog("任务运行中", "当前任务还在运行，请等待完成。")
            return

        config = self._collect_config()
        if not self._validate_config(config):
            return
        if self.config_locked:
            self._save_gui_config()

        self.log_text.clear()
        self._start_task_progress("任务已启动，正在等待抓取总数...")
        self.start_button.setEnabled(False)
        self.start_button.setText("运行中...")
        self.high_like_export_button.setEnabled(False)
        self.unfollow_cleanup_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("终止运行")
        self.stop_button.setStyleSheet("")
        self.worker = RunnerThread(config)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _start_high_like_export(self):
        if self.worker and self.worker.isRunning():
            self._show_info_dialog("任务运行中", "当前任务还在运行，请等待完成。")
            return

        config = self._collect_config()
        config.platform = "douyin_high_like"
        config.action = "fetch"
        if self.config_locked:
            self._save_gui_config()

        self.log_text.clear()
        self._start_task_progress("高赞视频导出中，正在等待统计结果...")
        self.start_button.setEnabled(False)
        self.high_like_export_button.setEnabled(False)
        self.high_like_export_button.setText("导出中...")
        self.unfollow_cleanup_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("终止运行")
        self.stop_button.setStyleSheet("")
        self.worker = RunnerThread(config)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _video_downloader_launch_commands(self):
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

    def _open_video_downloader_gui(self):
        if not EXTERNAL_DOUYIN_DOWNLOADER_RUNNER.exists():
            self._show_error_dialog(
                "下载器不存在",
                f"未找到下载器启动文件：\n{EXTERNAL_DOUYIN_DOWNLOADER_RUNNER}",
            )
            return

        launch_errors = []
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS

        for command in self._video_downloader_launch_commands():
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
                self._append_log(
                    f"已启动视频下载界面：{EXTERNAL_DOUYIN_DOWNLOADER_RUNNER} --gui"
                )
                return
            except Exception as exc:
                launch_errors.append(f"{' '.join(command)} -> {exc}")
                if log_file is not None and not log_file.closed:
                    log_file.close()

        message = "无法启动固定视频下载界面。"
        if launch_errors:
            message += "\n\n已尝试：\n" + "\n".join(launch_errors[:5])
        self._show_error_dialog("启动失败", message)

    def _start_douyin_non_followed_cache_cleanup(self):
        if self.worker and self.worker.isRunning():
            self._show_info_dialog("任务运行中", "当前任务还在运行，请等待完成。")
            return

        config = self._collect_config()
        config.platform = "douyin_non_followed_cleanup"
        config.action = "fetch"
        if self.config_locked:
            self._save_gui_config()

        self.log_text.clear()
        self._start_task_progress("缓存清理中，正在等待处理进度...")
        self.start_button.setEnabled(False)
        self.high_like_export_button.setEnabled(False)
        self.unfollow_cleanup_button.setEnabled(False)
        self.unfollow_cleanup_button.setText("清理中...")
        self.stop_button.setEnabled(True)
        self.stop_button.setText("终止运行")
        self.stop_button.setStyleSheet("")
        self.worker = RunnerThread(config)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _request_stop(self):
        if not self.worker or not self.worker.isRunning():
            return
        request_stop()
        self.stop_button.setText("正在保存...")
        self.stop_button.setStyleSheet("background-color: #c62828; color: white; font-weight: 700;")
        self._append_log("已请求终止运行，正在等待安全检查点并保存当前数据...")

    def _check_bilibili_cookie(self):
        if self.cookie_checker and self.cookie_checker.isRunning():
            return
        self.cookie_check_button.setEnabled(False)
        self.cookie_check_button.setText("检测中...")
        self.cookie_status_label.setText("B站 Cookie：检测中...")
        self.cookie_status_label.setStyleSheet("padding: 2px 0 8px 2px; color: #1565c0;")
        self.cookie_checker = BilibiliCookieCheckThread()
        self.cookie_checker.checked.connect(self._on_bilibili_cookie_checked)
        self.cookie_checker.start()

    def _on_bilibili_cookie_checked(self, ok, message):
        self.cookie_check_button.setEnabled(True)
        self.cookie_check_button.setText("检测 B站 Cookie")
        if ok:
            self.cookie_status_label.setText(f"B站 Cookie：{message}")
            self.cookie_status_label.setStyleSheet("padding: 2px 0 8px 2px; color: #2e7d32; font-weight: 700;")
            self._append_log(f"B站 Cookie 状态检测：{message}")
            self._show_info_dialog("B站 Cookie 状态", message)
        else:
            self.cookie_status_label.setText(f"B站 Cookie：{message}")
            self.cookie_status_label.setStyleSheet("padding: 2px 0 8px 2px; color: #c62828; font-weight: 700;")
            self._append_log(f"B站 Cookie 状态检测：{message}")
            self._show_warning_dialog("B站 Cookie 状态", message)

    def _validate_config(self, config):
        required_paths = []
        if config.platform == "douyin_unfollow":
            required_paths.append(config.unfollow_list_path)
        elif config.platform == "bilibili_uid":
            required_paths.append(config.bilibili_uid_list_path)
        elif config.platform == "douyin_uid":
            required_paths.append(config.douyin_uid_list_path)

        missing = [str(path) for path in required_paths if not path.exists()]
        if missing:
            self._show_warning_dialog("名单文件不存在", "\n".join(missing))
            return False
        return True

    def _append_log(self, line):
        self.log_text.append(line)
        self._update_task_progress_from_log(line)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _start_task_progress(self, message):
        self._progress_current = 0
        self._progress_total = 0
        self._progress_running = True
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("运行中")
        self.progress_label.setText(message)

    def _set_task_progress(self, current=None, total=None, label=None):
        if total is not None and total > 0:
            total = int(total)
            if self._progress_total <= 0 or self._progress_current <= total:
                self._progress_total = total
        if current is not None and current >= 0:
            self._progress_current = max(int(current), self._progress_current)

        if self._progress_total <= 0:
            if self._progress_running:
                self.progress_bar.setRange(0, 0)
                self.progress_bar.setFormat("运行中")
                if label:
                    self.progress_label.setText(label)
            return

        current_value = min(self._progress_current, self._progress_total)
        percent = current_value / self._progress_total * 100
        self.progress_bar.setRange(0, self._progress_total)
        self.progress_bar.setValue(current_value)
        self.progress_bar.setFormat(f"{current_value}/{self._progress_total} ({percent:.1f}%)")
        if label:
            self.progress_label.setText(label)
        else:
            self.progress_label.setText(f"抓取进度：已处理 {current_value} / {self._progress_total}")

    def _update_task_progress_from_log(self, line):
        if not self._progress_running or not line:
            return

        text = str(line)
        total = self._extract_progress_total(text)
        current, current_total = self._extract_progress_current(text)
        if current_total:
            total = current_total

        if total:
            self._set_task_progress(total=total, label=f"已识别本轮总数：{total}")
        if current is not None:
            self._set_task_progress(current=current, label=f"抓取进度：已处理 {current} / {self._progress_total or '?'}")

    def _extract_progress_total(self, text):
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

    def _extract_progress_current(self, text):
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

    def _finish_task_progress(self, ok, message):
        self._progress_running = False
        if ok:
            if self._progress_total > 0:
                final_value = self._progress_total
                if message.startswith("已终止运行"):
                    final_value = min(self._progress_current, self._progress_total)
                self.progress_bar.setRange(0, self._progress_total)
                self.progress_bar.setValue(final_value)
                percent = final_value / self._progress_total * 100 if self._progress_total else 0
                self.progress_bar.setFormat(f"{final_value}/{self._progress_total} ({percent:.1f}%)")
            else:
                self.progress_bar.setRange(0, 100)
                self.progress_bar.setValue(100)
                self.progress_bar.setFormat("完成")
            self.progress_label.setText(message)
        else:
            self.progress_bar.setRange(0, 100)
            self.progress_bar.setValue(0)
            self.progress_bar.setFormat("失败")
            self.progress_label.setText(f"任务失败：{message}")

    def _on_done(self, ok, message):
        self.start_button.setEnabled(True)
        self.start_button.setText("开始运行")
        self.high_like_export_button.setEnabled(not self.config_locked)
        self.high_like_export_button.setText("导出高赞视频")
        self.unfollow_cleanup_button.setEnabled(True)
        self.unfollow_cleanup_button.setText("清理非当前关注缓存")
        self.stop_button.setEnabled(False)
        if message.startswith("已终止运行"):
            self.stop_button.setText("保存完成，可以关闭")
            self.stop_button.setStyleSheet("background-color: #2e7d32; color: white; font-weight: 700;")
        else:
            self.stop_button.setText("终止运行")
            self.stop_button.setStyleSheet("")
        self._finish_task_progress(ok, message)
        if ok:
            self._append_log("-" * 60)
            self._append_log(message)
        else:
            self._show_error_dialog("任务失败", message)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
