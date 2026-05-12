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
from PyQt5.QtCore import QThread, Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QColor, QDesktopServices, QPainter, QPen
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
    QSizePolicy,
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


class RatingRefreshThread(QThread):
    done = pyqtSignal(bool, str)

    def run(self):
        try:
            from douyin_analyzer.app import run_score_creators_from_cache, run_score_videos_from_cache

            run_score_videos_from_cache()
            output_path = run_score_creators_from_cache(refresh_inventory=False)
            self.done.emit(True, f"评分数据已更新：{output_path}")
        except Exception as exc:
            self.done.emit(False, f"评分数据刷新失败：{exc}")


class DouyinDataSyncThread(QThread):
    done = pyqtSignal(bool, str)

    def run(self):
        try:
            from douyin_analyzer.config import load_analyzer_config
            from douyin_analyzer.data_sync import sync_progress_videos_to_state

            config = load_analyzer_config()
            result = sync_progress_videos_to_state(config, rerun_scores=True)
            before = result.get("before_diagnostics", {})
            after = result.get("after_diagnostics", {})
            message = (
                "抖音数据同步完成："
                f"同步UP {result['processed_creators']} 位，"
                f"progress视频 {result['processed_videos']} 条，"
                f"raw视频 {result.get('raw_videos_processed', 0)} 条；"
                f"清单缓存数更新 {result.get('inventory_rows_updated', 0)} 行；"
                f"douyin_video_state {result['video_state_before']} -> {result['video_state_after']}；"
                f"video_score_current {result['video_score_before']} -> {result['video_score_after']}；"
                f"creator_score_current {result['creator_score_before']} -> {result['creator_score_after']}；"
                f"当前缓存视频 {after.get('current_progress_videos', 0)} 条；"
                f"当前缓存未评分 {before.get('current_progress_not_scored', 0)} -> "
                f"{after.get('current_progress_not_scored', 0)}；"
                f"评分不在当前缓存 {before.get('score_not_current_progress', 0)} -> "
                f"{after.get('score_not_current_progress', 0)}；"
                f"下载标记孤儿 {before.get('score_download_mark_without_aweme', 0)} -> "
                f"{after.get('score_download_mark_without_aweme', 0)}；"
                f"需人工状态重置 {after.get('full_cache_count_mismatch_gt_30', 0)} 位"
            )
            self.done.emit(True, message)
        except Exception as exc:
            self.done.emit(False, f"抖音数据同步失败：{exc}")


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
        _set_button_busy(self.refresh_button, "刷新中...")
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
        finally:
            _restore_button_busy(self.refresh_button)


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
        _set_button_busy(self.refresh_button, "刷新中...")
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
        finally:
            _restore_button_busy(self.refresh_button)


SORT_ROLE = Qt.UserRole + 1
BUSY_BUTTON_STYLE = (
    "QPushButton { background-color: #2563eb; color: white; font-weight: 700; "
    "border: 1px solid #1d4ed8; border-radius: 4px; }"
    "QPushButton:disabled { background-color: #2563eb; color: white; font-weight: 700; "
    "border: 1px solid #1d4ed8; border-radius: 4px; }"
)


def _set_button_busy(button, text="刷新中..."):
    if button is None:
        return
    if not button.property("_busy_state_saved"):
        button.setProperty("_busy_old_text", button.text())
        button.setProperty("_busy_old_style", button.styleSheet())
        button.setProperty("_busy_old_enabled", button.isEnabled())
        button.setProperty("_busy_state_saved", True)
    button.setText(text)
    button.setEnabled(False)
    button.setStyleSheet(BUSY_BUTTON_STYLE)
    QApplication.processEvents()


def _restore_button_busy(button):
    if button is None or not button.property("_busy_state_saved"):
        return
    button.setText(button.property("_busy_old_text") or "")
    button.setStyleSheet(button.property("_busy_old_style") or "")
    button.setEnabled(bool(button.property("_busy_old_enabled")))
    button.setProperty("_busy_old_text", None)
    button.setProperty("_busy_old_style", None)
    button.setProperty("_busy_old_enabled", None)
    button.setProperty("_busy_state_saved", False)
    QApplication.processEvents()


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if other is not None else None
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class DouyinRatingOverviewDialog(QDialog):
    CREATOR_TABLE = "creator_score_current"
    VIDEO_TABLE = "video_score_current"
    ELIGIBLE_UID_TABLE = "_rating_eligible_uids"
    GRADE_ORDER = ("S", "A", "B", "C", "D")

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("抖音评分概览")
        self.resize(1280, 820)
        self.setMinimumSize(1120, 720)
        self.setStyleSheet(
            """
            QDialog {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-size: 15px;
                font-weight: 700;
                margin-top: 10px;
                padding-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QTabBar::tab {
                font-size: 15px;
                padding: 9px 22px;
                min-width: 112px;
                min-height: 28px;
            }
            QTabWidget::pane {
                top: -1px;
                border: 1px solid #d8dde6;
            }
            QTableWidget {
                font-size: 15px;
                gridline-color: #d8dde6;
                selection-background-color: #dbeafe;
                selection-color: #111827;
                alternate-background-color: #f7f8fb;
            }
            QHeaderView::section {
                font-size: 15px;
                font-weight: 700;
                padding: 8px 10px;
                background: #f1f5f9;
                border: 1px solid #d8dde6;
            }
            QPushButton {
                font-size: 14px;
                padding: 7px 16px;
                min-height: 30px;
            }
            """
        )

        from douyin_analyzer.config import load_analyzer_config

        self.config = load_analyzer_config()
        self.db_path = Path(self.config.export_store_db)
        self.refresh_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.summary_label = QLabel("正在读取评分数据...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setMinimumHeight(54)
        self.summary_label.setStyleSheet(
            "padding: 10px 12px; color: #263241; background: #f8fafc; "
            "border: 1px solid #d8dde6; border-radius: 8px; font-size: 14px;"
        )
        layout.addWidget(self.summary_label)

        summary_group = QGroupBox("等级分布")
        summary_grid = QGridLayout(summary_group)
        summary_grid.setContentsMargins(14, 14, 14, 14)
        summary_grid.setHorizontalSpacing(28)
        summary_grid.setVerticalSpacing(12)
        headers = ["对象", "总数", "S", "A", "B", "C", "D", "低/中置信度"]
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet("font-weight: 700; font-size: 15px; color: #111827;")
            summary_grid.addWidget(label, 0, column)

        self.summary_cells = {}
        for row, key in enumerate(("creator", "video"), start=1):
            title = "UP主" if key == "creator" else "视频"
            title_label = QLabel(title)
            title_label.setStyleSheet("font-weight: 700; font-size: 15px; color: #111827;")
            summary_grid.addWidget(title_label, row, 0)
            for column, name in enumerate(("total", *self.GRADE_ORDER, "low_confidence"), start=1):
                cell = QLabel("-")
                cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell.setMinimumWidth(72)
                cell.setStyleSheet("font-size: 16px; font-weight: 600; color: #111827;")
                summary_grid.addWidget(cell, row, column)
                self.summary_cells[(key, name)] = cell
        layout.addWidget(summary_group)

        search_group = QGroupBox("UP搜索")
        search_layout = QHBoxLayout(search_group)
        search_layout.setContentsMargins(14, 12, 14, 12)
        search_layout.setSpacing(10)
        search_layout.addWidget(QLabel("UP主ID"))
        self.creator_search_input = QLineEdit()
        self.creator_search_input.setPlaceholderText("输入 UP主UID / sec_uid 后筛选单个 UP 主")
        self.creator_search_input.returnPressed.connect(self._search_creator)
        search_layout.addWidget(self.creator_search_input, stretch=1)
        self.creator_search_button = QPushButton("筛选UP")
        self.creator_search_button.clicked.connect(self._search_creator)
        self.clear_creator_search_button = QPushButton("清空筛选")
        self.clear_creator_search_button.clicked.connect(self._clear_creator_search)
        search_layout.addWidget(self.creator_search_button)
        search_layout.addWidget(self.clear_creator_search_button)
        layout.addWidget(search_group)

        self.tabs = QTabWidget()
        self.tabs.setDocumentMode(False)
        self.tabs.setStyleSheet(
            """
            QTabBar {
                min-height: 48px;
            }
            QTabBar::tab {
                margin-top: 6px;
                margin-right: 4px;
            }
            """
        )
        self.creator_top_table = self._make_table(
            ["UP主", "等级", "分数", "置信度", "粉丝数", "作品数(缓存)", "详情"]
        )
        self.creator_low_table = self._make_table(
            ["UP主", "等级", "分数", "置信度", "未更新天数", "低等级比例", "详情"]
        )
        self.video_top_table = self._make_table(
            ["视频标题", "UP主", "等级", "分数", "置信度", "点赞数", "下载状态", "视频链接"]
        )
        self.video_watch_table = self._make_table(
            ["视频标题", "UP主", "等级", "分数", "置信度", "缺失指标", "视频链接"]
        )
        self.archived_creator_table = self._make_table(
            ["UP主", "等级", "分数", "置信度", "未更新天数", "粉丝数", "作品数", "归档时间", "归档原因", "详情"]
        )
        self.tabs.addTab(self.creator_top_table, "抖音排行表")
        self.tabs.addTab(self.creator_low_table, "低分/风险UP")
        self.tabs.addTab(self.video_top_table, "高分视频")
        self.tabs.addTab(self.video_watch_table, "待观察视频")
        self.tabs.addTab(self.archived_creator_table, "归档UP")
        layout.addWidget(self.tabs, stretch=1)

        self.refresh_info_label = QLabel("")
        self.refresh_info_label.setStyleSheet("padding: 4px 2px; color: #666; font-size: 13px;")
        layout.addWidget(self.refresh_info_label)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.refresh_button = QPushButton("刷新")
        self.close_button = QPushButton("关闭")

        self.refresh_button.clicked.connect(self.refresh_scores)
        self.close_button.clicked.connect(self.accept)

        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_data()

    def _make_table(self, headers):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setWordWrap(False)
        table.setShowGrid(True)
        table.setSortingEnabled(True)
        table.itemClicked.connect(self._open_link_item)
        table.verticalHeader().setDefaultSectionSize(42)
        table.verticalHeader().setMinimumSectionSize(38)
        table.horizontalHeader().setMinimumHeight(42)
        table.horizontalHeader().setMinimumSectionSize(70)
        for column, header in enumerate(headers):
            if header == "详情":
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
            elif column == len(headers) - 1 or header in {"UP主主页链接", "视频链接", "视频标题", "归档原因"}:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
            else:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
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

    @staticmethod
    def _sort_value(header_text, value):
        text = "" if value is None else str(value).strip()
        if header_text == "等级":
            return {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(text.upper(), 0)
        if header_text == "置信度":
            return {"很高": 5, "高": 4, "中": 3, "低": 2, "很低": 1}.get(text, 0)
        if header_text in {
            "分数",
            "粉丝数",
            "作品数(缓存)",
            "点赞数",
            "未更新天数",
            "低等级比例",
        }:
            try:
                return float(text.replace(",", ""))
            except ValueError:
                return -1
        return text.lower()

    def _grade_counts(self, conn, table, column, eligible_only=False):
        counts = {grade: 0 for grade in self.GRADE_ORDER}
        total = 0
        source = f'"{table}" AS s'
        join = (
            f' JOIN "{self.ELIGIBLE_UID_TABLE}" AS e ON s."UP主UID" = e.uid'
            if eligible_only
            else ""
        )
        for grade, count in conn.execute(
            f'SELECT s."{column}", COUNT(*) FROM {source}{join} GROUP BY s."{column}"'
        ):
            grade_text = str(grade or "").strip().upper()
            if grade_text in counts:
                counts[grade_text] += int(count or 0)
            total += int(count or 0)
        return total, counts

    def _confidence_count(self, conn, table, column, values, eligible_only=False):
        placeholders = ",".join("?" for _ in values)
        source = f'"{table}" AS s'
        join = (
            f' JOIN "{self.ELIGIBLE_UID_TABLE}" AS e ON s."UP主UID" = e.uid'
            if eligible_only
            else ""
        )
        row = conn.execute(
            f'SELECT COUNT(*) FROM {source}{join} WHERE s."{column}" IN ({placeholders})',
            tuple(values),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _prepare_eligible_uid_filter(self, conn):
        conn.execute(f'DROP TABLE IF EXISTS "{self.ELIGIBLE_UID_TABLE}"')
        conn.execute(f'CREATE TEMP TABLE "{self.ELIGIBLE_UID_TABLE}" (uid TEXT PRIMARY KEY)')
        if not self._table_exists(conn, "cache_inventory_current"):
            return False, 0, 0

        columns = {
            row[1] for row in conn.execute('PRAGMA table_info("cache_inventory_current")')
        }
        if "UP主UID" not in columns:
            return False, 0, 0

        active_archived_uids = set()
        if self._table_exists(conn, "douyin_archived_creators"):
            active_archived_uids = {
                str(row[0] or "").strip()
                for row in conn.execute(
                    '''
                    SELECT uploader_id
                    FROM douyin_archived_creators
                    WHERE archive_status = 'active'
                    '''
                ).fetchall()
                if str(row[0] or "").strip()
            }

        rows = conn.execute(
            '''
            SELECT "UP主UID"
            FROM cache_inventory_current
            WHERE TRIM(COALESCE("UP主UID", "")) != ""
              AND (
                  LOWER(TRIM(COALESCE("有full缓存", ""))) IN ('是', 'yes', 'true', '1', 'y')
                  OR LOWER(COALESCE("已缓存模式", "")) LIKE '%full%'
                  OR LOWER(TRIM(COALESCE("最近抓取模式", ""))) = 'full'
                  OR LOWER(TRIM(COALESCE("统计范围", ""))) = 'full'
              )
            '''
        ).fetchall()
        uids = [
            (uid,)
            for uid in (str(row[0] or "").strip() for row in rows)
            if uid and uid not in active_archived_uids
        ]
        if uids:
            conn.executemany(
                f'INSERT OR IGNORE INTO "{self.ELIGIBLE_UID_TABLE}" (uid) VALUES (?)',
                uids,
            )
        return True, len(uids), len(active_archived_uids)

    def _stale_uid_count(self, conn, table):
        if not self._table_exists(conn, table):
            return 0
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')}
        if "UP主UID" not in columns:
            return 0
        row = conn.execute(
            f'''
            SELECT COUNT(*)
            FROM "{table}" AS s
            LEFT JOIN "{self.ELIGIBLE_UID_TABLE}" AS e ON s."UP主UID" = e.uid
            WHERE e.uid IS NULL
            '''
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _query_rows(self, conn, sql, limit=30, params=()):
        if limit is None:
            return conn.execute(sql, tuple(params)).fetchall()
        return conn.execute(sql, (*tuple(params), limit)).fetchall()

    def _current_search_uid(self):
        return str(self.creator_search_input.text() or "").strip()

    def _search_creator(self):
        uid = self._current_search_uid()
        if not uid:
            QMessageBox.information(self, "请输入UP主ID", "请先输入要筛选的 UP主UID / sec_uid。")
            return
        self.refresh_data()
        self.tabs.setCurrentWidget(self.creator_top_table)

    def _clear_creator_search(self):
        if self._current_search_uid():
            self.creator_search_input.clear()
            self.refresh_data()

    def _load_archived_creator_rows(self, conn, limit=500, uploader_id=""):
        if not self._table_exists(conn, "douyin_archived_creators"):
            return []
        uid_filter = "AND uploader_id = ?" if uploader_id else ""
        params = (uploader_id, limit) if uploader_id else (limit,)
        return conn.execute(
            f"""
            SELECT uploader_name, final_grade, final_score, confidence,
                   inactive_days, follower_count, published_video_count,
                   archived_at, archive_reason, uploader_id
            FROM douyin_archived_creators
            WHERE archive_status = 'active'
              {uid_filter}
            ORDER BY CAST(COALESCE(inactive_days, 0) AS REAL) DESC,
                     CAST(COALESCE(final_score, 0) AS REAL) ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

    def _populate_table(self, table, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, value in enumerate(row):
                header_item = table.horizontalHeaderItem(column_index)
                header_text = header_item.text() if header_item else ""
                if header_text == "详情":
                    uid = str(value or "").strip()
                    button = QPushButton("详情")
                    button.setEnabled(bool(uid))
                    button.setProperty("uploader_id", uid)
                    button.clicked.connect(self._show_creator_detail_from_button)
                    table.setCellWidget(row_index, column_index, button)
                    item = SortableTableWidgetItem("详情")
                    item.setData(SORT_ROLE, uid.lower())
                    table.setItem(row_index, column_index, item)
                    continue
                text = self._fmt(value)
                item = SortableTableWidgetItem(text)
                item.setToolTip(text)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item.setData(SORT_ROLE, self._sort_value(header_text, value))
                if header_text in {"UP主主页链接", "视频链接"} and text:
                    item.setText("打开主页" if header_text == "UP主主页链接" else "打开视频")
                    item.setToolTip(text)
                    item.setData(Qt.UserRole, text)
                    item.setData(SORT_ROLE, text.lower())
                    item.setForeground(Qt.blue)
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                table.setItem(row_index, column_index, item)
            table.setRowHeight(row_index, 42)
        table.setSortingEnabled(True)

    def _show_creator_detail_from_button(self):
        button = self.sender()
        uid = str(button.property("uploader_id") or "").strip() if button else ""
        if uid:
            self._show_creator_detail(uid)

    def _open_link_item(self, item):
        url = item.data(Qt.UserRole) if item else ""
        if not url:
            return
        QDesktopServices.openUrl(QUrl(str(url)))

    def _show_creator_detail(self, uploader_id):
        try:
            detail = self._load_creator_detail(str(uploader_id or "").strip())
        except Exception as exc:
            QMessageBox.warning(self, "读取详情失败", str(exc))
            return
        if not detail.get("creator"):
            QMessageBox.information(self, "没有详情", "未在当前评分快照中找到该 UP 主。")
            return
        CreatorDetailDialog(detail, self).exec_()

    def _load_creator_detail(self, uploader_id):
        if not uploader_id or not self.db_path.exists():
            return {}

        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            creator = conn.execute(
                f'SELECT * FROM "{self.CREATOR_TABLE}" WHERE "UP主UID" = ? LIMIT 1',
                (uploader_id,),
            ).fetchone()
            if not creator:
                creator = self._load_archived_creator_detail(conn, uploader_id)
            if not creator:
                return {}

            cached_video_count = self._load_cached_video_count(conn, uploader_id)
            scored_video_count = self._count_videos_for_creator(conn, uploader_id)
            downloaded_snapshot = self._count_downloaded_snapshot_for_creator(conn, uploader_id)
            downloaded_records = self._count_aweme_download_records_for_creator(conn, uploader_id)
            duration_rows = self._group_video_values(conn, uploader_id, "时长分类")
            grade_rows = self._group_video_values(conn, uploader_id, "视频最终等级", grade_order=True)
            like_rows = self._group_like_values(conn, uploader_id)
            year_rows = self._group_year_values(conn, uploader_id)
            like_series = self._load_like_series(conn, uploader_id)

        return {
            "creator": dict(creator),
            "cached_video_count": cached_video_count,
            "scored_video_count": scored_video_count,
            "downloaded_count": max(downloaded_snapshot, downloaded_records),
            "duration_rows": duration_rows,
            "grade_rows": grade_rows,
            "like_rows": like_rows,
            "year_rows": year_rows,
            "like_series": like_series,
        }

    def _load_archived_creator_detail(self, conn, uploader_id):
        if not self._table_exists(conn, "douyin_archived_creators"):
            return None
        row = conn.execute(
            """
            SELECT *
            FROM douyin_archived_creators
            WHERE uploader_id = ? AND archive_status = 'active'
            LIMIT 1
            """,
            (uploader_id,),
        ).fetchone()
        if not row:
            return None
        data = dict(row)
        return {
            "UP主姓名": data.get("uploader_name", ""),
            "UP主UID": data.get("uploader_id", ""),
            "UP主主页链接": data.get("homepage_url", ""),
            "UP手动等级": data.get("manual_grade", ""),
            "UP最终等级": data.get("final_grade", ""),
            "UP最终分": data.get("final_score", ""),
            "评级置信度": data.get("confidence", ""),
            "评分来源": "archived_snapshot",
            "粉丝数": data.get("follower_count", ""),
            "获赞总数": data.get("total_like_count", ""),
            "最近更新时间": data.get("latest_publish_time", ""),
            "未更新天数": data.get("inactive_days", ""),
            "平均几天一更": data.get("avg_update_days", ""),
            "视频数量": data.get("published_video_count", ""),
            "评分原因": data.get("archive_reason", ""),
            "缺失指标": "",
        }

    def _load_cached_video_count(self, conn, uploader_id):
        counts = []
        if self._table_exists(conn, "cache_inventory_current"):
            row = conn.execute(
                'SELECT "缓存视频数" FROM "cache_inventory_current" WHERE "UP主UID" = ? LIMIT 1',
                (uploader_id,),
            ).fetchone()
            if row:
                try:
                    counts.append(int(float(row[0] or 0)))
                except (TypeError, ValueError):
                    pass
        if self._table_exists(conn, "douyin_video_state"):
            row = conn.execute(
                'SELECT COUNT(*) FROM "douyin_video_state" WHERE "uploader_id" = ?',
                (uploader_id,),
            ).fetchone()
            if row:
                counts.append(int(row[0] or 0))
        return max(counts) if counts else 0

    def _count_videos_for_creator(self, conn, uploader_id):
        if not self._table_exists(conn, self.VIDEO_TABLE):
            return 0
        row = conn.execute(
            f'SELECT COUNT(*) FROM "{self.VIDEO_TABLE}" WHERE "UP主UID" = ?',
            (uploader_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _count_downloaded_snapshot_for_creator(self, conn, uploader_id):
        if not self._table_exists(conn, self.VIDEO_TABLE):
            return 0
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{self.VIDEO_TABLE}")')}
        if "下载状态" not in columns and "下载路径" not in columns:
            return 0
        status_expr = 'TRIM(COALESCE("下载状态", "")) != ""' if "下载状态" in columns else "0"
        path_expr = 'TRIM(COALESCE("下载路径", "")) != ""' if "下载路径" in columns else "0"
        row = conn.execute(
            f'''
            SELECT COUNT(*)
            FROM "{self.VIDEO_TABLE}"
            WHERE "UP主UID" = ?
              AND ({status_expr} OR {path_expr})
            ''',
            (uploader_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _count_aweme_download_records_for_creator(self, conn, uploader_id):
        if not self._table_exists(conn, "aweme"):
            return 0
        columns = {row[1] for row in conn.execute('PRAGMA table_info("aweme")')}
        if "author_id" not in columns:
            return 0
        file_filter = 'AND TRIM(COALESCE(file_path, "")) != ""' if "file_path" in columns else ""
        row = conn.execute(
            f'''
            SELECT COUNT(*)
            FROM aweme
            WHERE TRIM(COALESCE(author_id, "")) = ?
            {file_filter}
            ''',
            (uploader_id,),
        ).fetchone()
        return int(row[0] or 0) if row else 0

    def _group_video_values(self, conn, uploader_id, column, grade_order=False):
        if not self._table_exists(conn, self.VIDEO_TABLE):
            return []
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{self.VIDEO_TABLE}")')}
        if column not in columns:
            return []
        rows = conn.execute(
            f'''
            SELECT COALESCE(NULLIF(TRIM("{column}"), ''), '未分类') AS name, COUNT(*) AS count
            FROM "{self.VIDEO_TABLE}"
            WHERE "UP主UID" = ?
            GROUP BY COALESCE(NULLIF(TRIM("{column}"), ''), '未分类')
            ''',
            (uploader_id,),
        ).fetchall()
        values = [(str(row["name"] or "未分类"), int(row["count"] or 0)) for row in rows]
        if grade_order:
            order = {grade: index for index, grade in enumerate(self.GRADE_ORDER)}
            values.sort(key=lambda item: order.get(item[0], len(order)))
        else:
            values.sort(key=lambda item: item[1], reverse=True)
        return values

    def _group_like_values(self, conn, uploader_id):
        if not self._table_exists(conn, self.VIDEO_TABLE):
            return []
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{self.VIDEO_TABLE}")')}
        if "点赞数" not in columns:
            return []
        rows = conn.execute(
            f'SELECT "点赞数" FROM "{self.VIDEO_TABLE}" WHERE "UP主UID" = ?',
            (uploader_id,),
        ).fetchall()
        buckets = [
            ("0-999", 0, 999),
            ("1千-9999", 1000, 9999),
            ("1万-9.9万", 10000, 99999),
            ("10万+", 100000, None),
        ]
        counts = {label: 0 for label, _, _ in buckets}
        missing = 0
        for (value,) in rows:
            try:
                like_count = int(float(str(value or "").replace(",", "")))
            except (TypeError, ValueError):
                missing += 1
                continue
            matched = False
            for label, lower, upper in buckets:
                if like_count >= lower and (upper is None or like_count <= upper):
                    counts[label] += 1
                    matched = True
                    break
            if not matched:
                missing += 1
        values = [(label, count) for label, count in counts.items() if count > 0]
        if missing:
            values.append(("无点赞数据", missing))
        return values

    def _group_year_values(self, conn, uploader_id):
        if not self._table_exists(conn, self.VIDEO_TABLE):
            return []
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{self.VIDEO_TABLE}")')}
        if "发布时间戳" not in columns and "发布日期" not in columns:
            return []
        publish_expr = '"发布时间戳"' if "发布时间戳" in columns else "''"
        date_expr = '"发布日期"' if "发布日期" in columns else "''"
        like_expr = '"点赞数"' if "点赞数" in columns else "0"
        rows = conn.execute(
            f'''
            SELECT {publish_expr} AS publish_ts, {date_expr} AS publish_date, {like_expr} AS like_count
            FROM "{self.VIDEO_TABLE}"
            WHERE "UP主UID" = ?
            ''',
            (uploader_id,),
        ).fetchall()
        grouped = {}
        for publish_ts, publish_date, like_count in rows:
            year = self._extract_year(publish_ts, publish_date)
            item = grouped.setdefault(year, {"count": 0, "likes": 0})
            item["count"] += 1
            item["likes"] += self._safe_number(like_count)
        sortable = sorted(
            grouped.items(),
            key=lambda item: (item[0] == "未知年份", item[0]),
        )
        known_years = [item for item in sortable if item[0] != "未知年份"]
        unknown_years = [item for item in sortable if item[0] == "未知年份"]
        sortable = list(reversed(known_years)) + unknown_years
        return [(year, data["count"], data["likes"]) for year, data in sortable]

    def _load_like_series(self, conn, uploader_id):
        if not self._table_exists(conn, self.VIDEO_TABLE):
            return []
        columns = {row[1] for row in conn.execute(f'PRAGMA table_info("{self.VIDEO_TABLE}")')}
        if "点赞数" not in columns:
            return []
        title_expr = '"视频标题"' if "视频标题" in columns else "''"
        publish_expr = '"发布时间戳"' if "发布时间戳" in columns else "0"
        date_expr = '"发布日期"' if "发布日期" in columns else "''"
        grade_expr = '"视频最终等级"' if "视频最终等级" in columns else "''"
        rows = conn.execute(
            f'''
            SELECT {title_expr} AS title,
                   {publish_expr} AS publish_ts,
                   {date_expr} AS publish_date,
                   "点赞数" AS like_count,
                   {grade_expr} AS grade
            FROM "{self.VIDEO_TABLE}"
            WHERE "UP主UID" = ?
            ''',
            (uploader_id,),
        ).fetchall()
        values = []
        for row in rows:
            publish_ts = self._safe_number(row["publish_ts"])
            publish_date = str(row["publish_date"] or "").strip()
            values.append(
                {
                    "title": str(row["title"] or "").strip(),
                    "publish_ts": publish_ts,
                    "publish_date": publish_date,
                    "like_count": self._safe_number(row["like_count"]),
                    "grade": str(row["grade"] or "").strip(),
                }
            )
        values.sort(
            key=lambda item: (
                item.get("publish_ts") or 0,
                item.get("publish_date") or "",
                item.get("title") or "",
            )
        )
        return values

    @staticmethod
    def _safe_number(value):
        try:
            text = str(value or "").replace(",", "").strip()
            return int(float(text)) if text else 0
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _extract_year(publish_ts, publish_date):
        timestamp = DouyinRatingOverviewDialog._safe_number(publish_ts)
        if timestamp > 0:
            try:
                return datetime.fromtimestamp(timestamp).strftime("%Y")
            except (OSError, OverflowError, ValueError):
                pass
        text = str(publish_date or "").strip()
        match = re.search(r"(19|20)\d{2}", text)
        return match.group(0) if match else "未知年份"

    def _clear_tables(self):
        for table in (
            self.creator_top_table,
            self.creator_low_table,
            self.video_top_table,
            self.video_watch_table,
            self.archived_creator_table,
        ):
            table.setRowCount(0)

    def refresh_scores(self):
        if self.refresh_worker and self.refresh_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "刷新中...")
        self.summary_label.setText("正在按当前 full 缓存重新生成 UP 主评分，请稍候...")
        self.refresh_worker = RatingRefreshThread(self)
        self.refresh_worker.done.connect(self._on_scores_refreshed)
        self.refresh_worker.start()

    def _on_scores_refreshed(self, ok, message):
        _restore_button_busy(self.refresh_button)
        self.refresh_data()
        if not ok:
            QMessageBox.warning(self, "评分刷新失败", message)
            return
        self.refresh_info_label.setText(
            f"{message} | 最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def refresh_data(self):
        if not self.db_path.exists():
            self.summary_label.setText(f"未找到评分数据库：{self.db_path}")
            self._clear_tables()
            return

        search_uid = self._current_search_uid()

        try:
            with sqlite3.connect(str(self.db_path)) as conn:
                has_creator = self._table_exists(conn, self.CREATOR_TABLE)
                has_video = self._table_exists(conn, self.VIDEO_TABLE)
                has_eligible_filter, eligible_count, archived_count = self._prepare_eligible_uid_filter(conn)
                stale_creator_count = (
                    self._stale_uid_count(conn, self.CREATOR_TABLE)
                    if has_eligible_filter and has_creator
                    else 0
                )
                stale_video_count = 0

                if not has_creator and not has_video:
                    self.summary_label.setText(
                        "未找到评分表，请先运行抖音视频评分或 UP 主评分。"
                    )
                    self._clear_tables()
                    return

                if has_creator:
                    total, counts = self._grade_counts(
                        conn,
                        self.CREATOR_TABLE,
                        "UP最终等级",
                        eligible_only=has_eligible_filter,
                    )
                    low_confidence = self._confidence_count(
                        conn,
                        self.CREATOR_TABLE,
                        "评级置信度",
                        ["低", "中"],
                        eligible_only=has_eligible_filter,
                    )
                    self._set_summary("creator", total, counts, low_confidence)
                    creator_join = (
                        f'JOIN "{self.ELIGIBLE_UID_TABLE}" AS e ON c."UP主UID" = e.uid'
                        if has_eligible_filter and not search_uid
                        else ""
                    )
                    creator_where = 'WHERE c."UP主UID" = ?' if search_uid else ""
                    creator_params = (search_uid,) if search_uid else ()
                    self._populate_table(
                        self.creator_top_table,
                        self._query_rows(
                            conn,
                            f"""
                            SELECT c."UP主姓名", c."UP最终等级", c."UP最终分", c."评级置信度",
                                   c."粉丝数", c."视频数量", c."UP主UID"
                            FROM creator_score_current AS c
                            {creator_join}
                            {creator_where}
                            ORDER BY CAST(c."UP最终分" AS REAL) DESC
                            """,
                            limit=None,
                            params=creator_params,
                        ),
                    )
                    low_conditions = [
                        '(c."UP最终等级" IN (\'C\', \'D\') OR c."评级置信度" IN (\'低\', \'中\'))'
                    ]
                    low_params = []
                    if search_uid:
                        low_conditions.insert(0, 'c."UP主UID" = ?')
                        low_params.append(search_uid)
                    self._populate_table(
                        self.creator_low_table,
                        self._query_rows(
                            conn,
                            f"""
                            SELECT c."UP主姓名", c."UP最终等级", c."UP最终分", c."评级置信度",
                                   c."未更新天数", c."低等级视频比例", c."UP主UID"
                            FROM creator_score_current AS c
                            {creator_join}
                            WHERE {' AND '.join(low_conditions)}
                            ORDER BY CAST(c."UP最终分" AS REAL) ASC
                            LIMIT ?
                            """,
                            params=low_params,
                        ),
                    )
                else:
                    self._set_summary("creator", 0, {}, 0)
                    self.creator_top_table.setRowCount(0)
                    self.creator_low_table.setRowCount(0)

                self._populate_table(
                    self.archived_creator_table,
                    self._load_archived_creator_rows(conn, uploader_id=search_uid),
                )

                if has_video:
                    total, counts = self._grade_counts(
                        conn,
                        self.VIDEO_TABLE,
                        "视频最终等级",
                        eligible_only=False,
                    )
                    low_confidence = self._confidence_count(
                        conn,
                        self.VIDEO_TABLE,
                        "评分置信度",
                        ["很低", "低", "中"],
                        eligible_only=False,
                    )
                    self._set_summary("video", total, counts, low_confidence)
                    video_join = ""
                    video_where = 'WHERE v."UP主UID" = ?' if search_uid else ""
                    video_params = (search_uid,) if search_uid else ()
                    self._populate_table(
                        self.video_top_table,
                        self._query_rows(
                            conn,
                            f"""
                            SELECT v."视频标题", v."UP主姓名", v."视频最终等级", v."视频最终分",
                                   v."评分置信度", v."点赞数", v."下载状态", v."视频链接"
                            FROM video_score_current AS v
                            {video_join}
                            LEFT JOIN creator_score_current AS c
                              ON v."UP主UID" = c."UP主UID"
                            {video_where}
                            ORDER BY CAST(v."视频最终分" AS REAL) DESC
                            LIMIT ?
                            """,
                            params=video_params,
                        ),
                    )
                    watch_conditions = [
                        '(v."评分置信度" IN (\'很低\', \'低\', \'中\') OR v."缺失指标" != \'\')'
                    ]
                    watch_params = []
                    if search_uid:
                        watch_conditions.insert(0, 'v."UP主UID" = ?')
                        watch_params.append(search_uid)
                    self._populate_table(
                        self.video_watch_table,
                        self._query_rows(
                            conn,
                            f"""
                            SELECT v."视频标题", v."UP主姓名", v."视频最终等级", v."视频最终分",
                                   v."评分置信度", v."缺失指标", v."视频链接"
                            FROM video_score_current AS v
                            {video_join}
                            LEFT JOIN creator_score_current AS c
                              ON v."UP主UID" = c."UP主UID"
                            WHERE {' AND '.join(watch_conditions)}
                            ORDER BY CAST(v."视频最终分" AS REAL) DESC
                            LIMIT ?
                            """,
                            params=watch_params,
                        ),
                    )
                else:
                    self._set_summary("video", 0, {}, 0)
                    self.video_top_table.setRowCount(0)
                    self.video_watch_table.setRowCount(0)

            warning_parts = []
            if search_uid:
                warning_parts.append(f"筛选UP {search_uid}")
            if has_eligible_filter:
                warning_parts.append(f"UP榜仅展示 full 缓存且未归档UP {eligible_count} 位")
            if archived_count:
                warning_parts.append(f"已排除 active 归档UP {archived_count} 位")
            if stale_creator_count:
                warning_parts.append(f"非 full/已归档UP评分 {stale_creator_count} 位未展示")
            if has_video:
                warning_parts.append("视频榜展示当前缓存视频全集")
            warning_text = f"\n范围：{'；'.join(warning_parts)}" if warning_parts else ""
            self.summary_label.setText(
                f"评分数据已加载：{self.db_path}\n"
                f"S级仅来自手动等级；自动评分最高为A级。数据来自本地 SQLite。{warning_text}"
            )
            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取评分数据失败：{exc}")
            self._clear_tables()

    def _set_summary(self, key, total, counts, low_confidence):
        self.summary_cells[(key, "total")].setText(str(total))
        for grade in self.GRADE_ORDER:
            self.summary_cells[(key, grade)].setText(str(int((counts or {}).get(grade, 0))))
        self.summary_cells[(key, "low_confidence")].setText(str(low_confidence))


class LikeLineChartWidget(QWidget):
    def __init__(self, rows, parent=None):
        super().__init__(parent)
        self.rows = rows or []
        self.setMinimumHeight(360)

    @staticmethod
    def _fmt_number(value):
        try:
            number = int(float(value or 0))
        except (TypeError, ValueError):
            number = 0
        if number >= 10000:
            return f"{number / 10000:.1f}万"
        return f"{number:,}"

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        rect = self.rect().adjusted(8, 8, -8, -8)
        painter.fillRect(rect, QColor("#ffffff"))
        painter.setPen(QPen(QColor("#d8dde6"), 1))
        painter.drawRoundedRect(rect, 8, 8)

        if not self.rows:
            painter.setPen(QColor("#6b7280"))
            painter.drawText(rect, Qt.AlignCenter, "暂无点赞明细数据")
            return

        left = rect.left() + 72
        right = rect.right() - 26
        top = rect.top() + 34
        bottom = rect.bottom() - 58
        width = max(right - left, 1)
        height = max(bottom - top, 1)

        likes = [max(int(row.get("like_count") or 0), 0) for row in self.rows]
        max_like = max(max(likes), 1)
        point_count = len(likes)

        painter.setPen(QPen(QColor("#e5e7eb"), 1))
        for index in range(6):
            y = bottom - int(height * index / 5)
            painter.drawLine(left, y, right, y)
            label_value = int(max_like * index / 5)
            painter.setPen(QColor("#6b7280"))
            painter.drawText(rect.left() + 8, y - 9, 58, 18, Qt.AlignRight | Qt.AlignVCenter, self._fmt_number(label_value))
            painter.setPen(QPen(QColor("#e5e7eb"), 1))

        painter.setPen(QPen(QColor("#9ca3af"), 1.4))
        painter.drawLine(left, bottom, right, bottom)
        painter.drawLine(left, top, left, bottom)

        def x_for(idx):
            if point_count <= 1:
                return left + width // 2
            return left + int(width * idx / (point_count - 1))

        def y_for(value):
            return bottom - int(height * value / max_like)

        painter.setPen(QPen(QColor("#2563eb"), 2.2))
        points = [(x_for(index), y_for(value)) for index, value in enumerate(likes)]
        for start, end in zip(points, points[1:]):
            painter.drawLine(start[0], start[1], end[0], end[1])

        painter.setPen(QPen(QColor("#1d4ed8"), 1.5))
        painter.setBrush(QColor("#60a5fa"))
        step = max(point_count // 90, 1)
        for index, (x, y) in enumerate(points):
            if index % step == 0 or index in {0, point_count - 1}:
                painter.drawEllipse(x - 3, y - 3, 6, 6)

        painter.setBrush(Qt.NoBrush)
        painter.setPen(QColor("#374151"))
        painter.drawText(left, rect.top() + 8, right - left, 22, Qt.AlignCenter, "点赞趋势（按发表时间从早到晚排序）")
        painter.drawText(left, bottom + 28, right - left, 22, Qt.AlignCenter, "视频编号")
        painter.save()
        painter.translate(rect.left() + 18, top + height // 2)
        painter.rotate(-90)
        painter.drawText(-80, 0, 160, 18, Qt.AlignCenter, "点赞数")
        painter.restore()

        x_labels = [(0, "1"), (point_count - 1, str(point_count))]
        if point_count > 2:
            middle = point_count // 2
            x_labels.insert(1, (middle, str(middle + 1)))
        for index, label in x_labels:
            x = x_for(index)
            painter.drawText(x - 28, bottom + 8, 56, 18, Qt.AlignCenter, label)


class LikePreviewDialog(QDialog):
    def __init__(self, detail, parent=None):
        super().__init__(parent)
        self.detail = detail or {}
        self.creator = self.detail.get("creator") or {}
        self.setWindowTitle(f"点赞预览 - {self.creator.get('UP主姓名') or ''}")
        self.resize(980, 760)
        self.setMinimumSize(860, 620)
        self.setStyleSheet(
            """
            QDialog {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-size: 15px;
                font-weight: 700;
                margin-top: 10px;
                padding-top: 14px;
            }
            QLabel {
                font-size: 14px;
            }
            QPushButton {
                font-size: 14px;
                padding: 7px 16px;
                min-height: 30px;
            }
            """
        )
        self._build_ui()

    @staticmethod
    def _fmt(value):
        if value is None:
            return "-"
        text = str(value).strip()
        return text if text else "-"

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(
            f"{self._fmt(self.creator.get('UP主姓名'))}  |  "
            f"等级 {self._fmt(self.creator.get('UP最终等级'))}  |  "
            f"视频数 {len(self.detail.get('like_series') or [])}"
        )
        title.setWordWrap(True)
        title.setStyleSheet(
            "padding: 10px 12px; color: #111827; background: #f8fafc; "
            "border: 1px solid #d8dde6; border-radius: 8px; font-size: 16px; font-weight: 700;"
        )
        layout.addWidget(title)

        chart_group = QGroupBox("点赞折线图")
        chart_layout = QVBoxLayout(chart_group)
        chart_layout.addWidget(LikeLineChartWidget(self.detail.get("like_series") or []))
        tip = QLabel("横坐标：按发表时间升序排列的视频编号；纵坐标：单条视频点赞数。")
        tip.setStyleSheet("color: #6b7280;")
        chart_layout.addWidget(tip)
        layout.addWidget(chart_group, stretch=1)

        cards = QGroupBox("框选数据")
        cards_layout = QVBoxLayout(cards)
        cards_layout.addWidget(self._distribution_group("视频点赞构成", self.detail.get("like_rows") or []))
        cards_layout.addWidget(self._year_distribution_group("视频年份构成", self.detail.get("year_rows") or []))
        layout.addWidget(cards)

        button_row = QHBoxLayout()
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _distribution_group(self, title, rows):
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        total = sum(count for _, count in rows)
        if not rows:
            grid.addWidget(QLabel("暂无明细数据"), 0, 0)
            return group
        for index, (name, count) in enumerate(rows):
            percent = f"{(count / total * 100):.1f}%" if total else "0.0%"
            row = index // 2
            col = (index % 2) * 2
            name_label = QLabel(str(name))
            name_label.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(f"{count}  ({percent})")
            grid.addWidget(name_label, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _year_distribution_group(self, title, rows):
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        total_count = sum(count for _, count, _ in rows)
        if not rows:
            grid.addWidget(QLabel("暂无明细数据"), 0, 0)
            return group
        for index, (year, count, like_sum) in enumerate(rows):
            percent = f"{(count / total_count * 100):.1f}%" if total_count else "0.0%"
            row = index // 2
            col = (index % 2) * 2
            name_label = QLabel(str(year))
            name_label.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(f"视频 {int(count):,} 条 ({percent}) / 点赞总数 {int(like_sum):,}")
            grid.addWidget(name_label, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group


class CreatorDetailDialog(QDialog):
    FACTOR_COLUMNS = [
        ("粉丝数量分", "粉丝数量分"),
        ("获赞总数分", "获赞总数分"),
        ("最近更新时间分", "最近更新时间分"),
        ("平均几天一更分", "平均几天一更分"),
        ("视频数量分", "视频数量分"),
        ("最早视频时间分", "最早视频时间分"),
        ("平均点赞数分", "平均点赞数分"),
        ("视频等级分布分", "视频等级分布分"),
        ("低等级比例分", "低等级比例分"),
        ("最近10条趋势分", "最近10条趋势分"),
        ("风险扣分", "风险扣分"),
    ]

    def __init__(self, detail, parent=None):
        super().__init__(parent)
        self.detail = detail or {}
        self.creator = self.detail.get("creator") or {}
        self.setWindowTitle(f"UP主详情 - {self.creator.get('UP主姓名') or ''}")
        self.resize(920, 760)
        self.setMinimumSize(820, 640)
        self.setStyleSheet(
            """
            QDialog {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-size: 15px;
                font-weight: 700;
                margin-top: 10px;
                padding-top: 14px;
            }
            QLabel {
                font-size: 14px;
            }
            QTableWidget {
                font-size: 14px;
                gridline-color: #d8dde6;
                alternate-background-color: #f7f8fb;
            }
            QHeaderView::section {
                font-size: 14px;
                font-weight: 700;
                padding: 7px 9px;
                background: #f1f5f9;
                border: 1px solid #d8dde6;
            }
            QPushButton {
                font-size: 14px;
                padding: 7px 16px;
                min-height: 30px;
            }
            """
        )
        self._build_ui()

    @staticmethod
    def _fmt(value):
        if value is None:
            return "-"
        if isinstance(value, float):
            return f"{value:.2f}"
        text = str(value).strip()
        return text if text else "-"

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        title = QLabel(
            f"{self._fmt(self.creator.get('UP主姓名'))}  |  "
            f"等级 {self._fmt(self.creator.get('UP最终等级'))}  |  "
            f"分数 {self._fmt(self.creator.get('UP最终分'))}  |  "
            f"置信度 {self._fmt(self.creator.get('评级置信度'))}"
        )
        title.setWordWrap(True)
        title.setStyleSheet(
            "padding: 10px 12px; color: #111827; background: #f8fafc; "
            "border: 1px solid #d8dde6; border-radius: 8px; font-size: 16px; font-weight: 700;"
        )
        layout.addWidget(title)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(0, 0, 0, 0)
        body_layout.setSpacing(10)

        body_layout.addWidget(self._basic_group())
        body_layout.addWidget(self._factor_group())
        body_layout.addWidget(self._distribution_group("视频等级构成", self.detail.get("grade_rows") or []))
        body_layout.addWidget(self._distribution_group("视频时长构成", self.detail.get("duration_rows") or []))
        body_layout.addWidget(self._distribution_group("视频点赞构成", self.detail.get("like_rows") or []))
        body_layout.addWidget(self._year_distribution_group("视频年份构成", self.detail.get("year_rows") or []))

        reason_group = QGroupBox("评分说明")
        reason_layout = QVBoxLayout(reason_group)
        reason = QLabel(
            f"评分来源：{self._fmt(self.creator.get('评分来源'))}\n"
            f"评分原因：{self._fmt(self.creator.get('评分原因'))}\n"
            f"缺失指标：{self._fmt(self.creator.get('缺失指标'))}"
        )
        reason.setWordWrap(True)
        reason.setStyleSheet("line-height: 1.5;")
        reason_layout.addWidget(reason)
        body_layout.addWidget(reason_group)

        scroll.setWidget(body)
        layout.addWidget(scroll, stretch=1)

        button_row = QHBoxLayout()
        like_preview_button = QPushButton("点赞预览")
        like_preview_button.clicked.connect(self._open_like_preview)
        grade_button = QPushButton("等级设置")
        grade_button.clicked.connect(self._set_manual_grade)
        homepage = QPushButton("打开主页")
        homepage.setEnabled(bool(str(self.creator.get("UP主主页链接") or "").strip()))
        homepage.clicked.connect(self._open_homepage)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(like_preview_button)
        button_row.addWidget(grade_button)
        button_row.addWidget(homepage)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _basic_group(self):
        group = QGroupBox("基础信息")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        items = [
            ("粉丝数", self.creator.get("粉丝数")),
            ("获赞总数", self.creator.get("获赞总数")),
            ("作品数", self.creator.get("视频数量")),
            ("已缓存视频数", self.detail.get("cached_video_count")),
            ("已下载视频数", self.detail.get("downloaded_count")),
            ("视频评分表数量", self.detail.get("scored_video_count")),
            ("已评分视频数", self.creator.get("已评分视频数")),
            ("未更新天数", self.creator.get("未更新天数")),
            ("最近更新时间", self.creator.get("最近更新时间")),
            ("平均几天一更", self.creator.get("平均几天一更")),
            ("最早视频时间", self.creator.get("最早视频时间")),
            ("创作跨度(天)", self.creator.get("创作跨度(天)")),
            ("低等级视频比例", self.creator.get("低等级视频比例")),
        ]
        for index, (label, value) in enumerate(items):
            row = index // 2
            col = (index % 2) * 2
            name = QLabel(label)
            name.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(self._fmt(value))
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _factor_group(self):
        group = QGroupBox("各因素分数")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        for index, (label, key) in enumerate(self.FACTOR_COLUMNS):
            row = index // 2
            col = (index % 2) * 2
            name = QLabel(label)
            name.setStyleSheet("font-weight: 700; color: #374151;")
            value = QLabel(self._fmt(self.creator.get(key)))
            value.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name, row, col)
            grid.addWidget(value, row, col + 1)
        return group

    def _distribution_group(self, title, rows):
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        total = sum(count for _, count in rows)
        if not rows:
            empty = QLabel("暂无明细数据")
            empty.setStyleSheet("color: #6b7280;")
            grid.addWidget(empty, 0, 0)
            return group
        for index, (name, count) in enumerate(rows):
            percent = f"{(count / total * 100):.1f}%" if total else "0.0%"
            row = index // 2
            col = (index % 2) * 2
            name_label = QLabel(str(name))
            name_label.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(f"{count}  ({percent})")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name_label, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _year_distribution_group(self, title, rows):
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        total_count = sum(count for _, count, _ in rows)
        if not rows:
            empty = QLabel("暂无明细数据")
            empty.setStyleSheet("color: #6b7280;")
            grid.addWidget(empty, 0, 0)
            return group
        for index, (year, count, like_sum) in enumerate(rows):
            percent = f"{(count / total_count * 100):.1f}%" if total_count else "0.0%"
            row = index // 2
            col = (index % 2) * 2
            name_label = QLabel(str(year))
            name_label.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(f"视频 {int(count):,} 条 ({percent}) / 点赞总数 {int(like_sum):,}")
            value_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
            grid.addWidget(name_label, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _open_like_preview(self):
        LikePreviewDialog(self.detail, self).exec_()

    def _set_manual_grade(self):
        uploader_id = str(self.creator.get("UP主UID") or "").strip()
        uploader_name = str(self.creator.get("UP主姓名") or "").strip() or uploader_id
        if not uploader_id:
            QMessageBox.warning(self, "无法设置等级", "当前详情缺少 UP主UID，无法写入手动等级。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"等级设置 - {uploader_name}")
        dialog.resize(420, 180)
        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(12)

        hint = QLabel(
            "手动等级会写入本地 SQLite，并在重新评分后覆盖自动等级。\n"
            "选择“自动评分”会清除该 UP 的手动等级。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        grade_combo = QComboBox()
        grade_combo.addItem("自动评分（清除手动等级）", "")
        for grade in ("S", "A", "B", "C", "D"):
            grade_combo.addItem(grade, grade)
        current_grade = str(self.creator.get("UP手动等级") or "").strip().upper()
        if current_grade in {"S", "A", "B", "C", "D"}:
            grade_combo.setCurrentIndex({"S": 1, "A": 2, "B": 3, "C": 4, "D": 5}[current_grade])
        note_input = QLineEdit()
        note_input.setPlaceholderText("可选：记录设置原因")
        form.addRow("手动等级", grade_combo)
        form.addRow("备注", note_input)
        layout.addLayout(form)

        button_row = QHBoxLayout()
        save_button = QPushButton("保存")
        cancel_button = QPushButton("取消")
        save_button.clicked.connect(dialog.accept)
        cancel_button.clicked.connect(dialog.reject)
        button_row.addStretch(1)
        button_row.addWidget(save_button)
        button_row.addWidget(cancel_button)
        layout.addLayout(button_row)

        if dialog.exec_() != QDialog.Accepted:
            return

        grade = str(grade_combo.currentData() or "").strip().upper()
        note = note_input.text().strip()
        try:
            self._save_manual_grade(uploader_id, grade, note)
        except Exception as exc:
            QMessageBox.warning(self, "保存失败", f"手动等级保存失败：{exc}")
            return

        parent = self.parent()
        if parent is not None and hasattr(parent, "refresh_scores"):
            parent.refresh_scores()
        QMessageBox.information(
            self,
            "已保存",
            f"已{'设置' if grade else '清除'} {uploader_name} 的手动等级"
            f"{f'：{grade}' if grade else ''}。\n评分数据正在刷新，完成后列表会更新。",
        )
        self.accept()

    def _save_manual_grade(self, uploader_id, grade, note):
        parent = self.parent()
        db_path = getattr(parent, "db_path", None)
        if not db_path:
            raise RuntimeError("未找到评分数据库路径")
        with sqlite3.connect(str(db_path)) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS douyin_creator_manual_rating (
                    uploader_id TEXT PRIMARY KEY,
                    manual_grade TEXT NOT NULL,
                    note TEXT,
                    updated_at TEXT NOT NULL
                )
                """
            )
            if grade:
                conn.execute(
                    """
                    INSERT INTO douyin_creator_manual_rating
                        (uploader_id, manual_grade, note, updated_at)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(uploader_id) DO UPDATE SET
                        manual_grade=excluded.manual_grade,
                        note=excluded.note,
                        updated_at=excluded.updated_at
                    """,
                    (uploader_id, grade, note, datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                )
            else:
                conn.execute(
                    'DELETE FROM douyin_creator_manual_rating WHERE uploader_id = ?',
                    (uploader_id,),
                )
            conn.commit()

    def _open_homepage(self):
        url = str(self.creator.get("UP主主页链接") or "").strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))


class DouyinStatusResetDialog(QDialog):
    COLUMNS = [
        ("UP主", "uploader_name"),
        ("发布视频数", "published_video_count"),
        ("缓存视频数", "cached_video_count"),
        ("差值", "diff_count"),
        ("最近抓取模式", "last_fetch_mode"),
        ("已缓存模式", "cache_modes"),
        ("缓存时间", "progress_cached_at"),
        ("UP主主页链接", "homepage_url"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("抖音状态重置")
        self.resize(1180, 720)
        self.setMinimumSize(980, 620)
        self.setStyleSheet(
            """
            QDialog {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-size: 15px;
                font-weight: 700;
                margin-top: 10px;
                padding-top: 14px;
            }
            QTableWidget {
                font-size: 15px;
                gridline-color: #d8dde6;
                alternate-background-color: #f7f8fb;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QHeaderView::section {
                font-size: 15px;
                font-weight: 700;
                padding: 8px 10px;
                background: #f1f5f9;
                border: 1px solid #d8dde6;
            }
            QPushButton {
                font-size: 14px;
                padding: 7px 16px;
                min-height: 30px;
            }
            """
        )

        from douyin_analyzer.config import load_analyzer_config

        self.config = load_analyzer_config()
        self.db_path = Path(self.config.export_store_db)
        self.reset_uids = set()
        self.rows = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.summary_label = QLabel("")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            "padding: 10px 12px; color: #263241; background: #f8fafc; "
            "border: 1px solid #d8dde6; border-radius: 8px;"
        )
        layout.addWidget(self.summary_label)

        filter_group = QGroupBox("筛选条件")
        filter_layout = QHBoxLayout(filter_group)
        filter_layout.addWidget(QLabel("差值超过"))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 100000)
        self.threshold_spin.setValue(30)
        self.threshold_spin.setMaximumWidth(110)
        filter_layout.addWidget(self.threshold_spin)
        filter_layout.addWidget(QLabel("仅列出 full 状态下，发布视频数与缓存视频数差距过大的 UP。"))
        filter_layout.addStretch(1)
        self.refresh_button = QPushButton("刷新列表")
        self.refresh_button.clicked.connect(self.refresh_data)
        filter_layout.addWidget(self.refresh_button)
        layout.addWidget(filter_group)

        self.table = QTableWidget()
        self.table.setColumnCount(len(self.COLUMNS))
        self.table.setHorizontalHeaderLabels([label for label, _ in self.COLUMNS])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setSelectionMode(QTableWidget.ExtendedSelection)
        self.table.itemClicked.connect(self._open_link_item)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.horizontalHeader().setMinimumHeight(42)
        for column, (label, _) in enumerate(self.COLUMNS):
            if label == "UP主主页链接":
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
            else:
                self.table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        layout.addWidget(self.table, stretch=1)

        button_row = QHBoxLayout()
        self.reset_selected_button = QPushButton("重置选中状态")
        self.reset_selected_button.clicked.connect(self.reset_selected)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.reset_selected_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_data()

    @staticmethod
    def _safe_int(value, default=0):
        try:
            if value in (None, ""):
                return default
            return int(float(str(value).replace(",", "")))
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _fmt(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.0f}" if value.is_integer() else f"{value:.2f}"
        return str(value)

    def _is_full_row(self, row):
        cached_modes = str(row.get("已缓存模式") or "").lower()
        last_fetch_mode = str(row.get("最近抓取模式") or "").strip().lower()
        has_full = str(row.get("有full缓存") or "").strip().lower()
        return (
            "full" in {part.strip() for part in cached_modes.split(",")}
            or last_fetch_mode == "full"
            or has_full in {"是", "yes", "true", "1", "y"}
        )

    def _load_candidates(self):
        if not self.db_path.exists():
            return []
        threshold = self.threshold_spin.value()
        self.reset_uids = self._load_reset_uids()
        with sqlite3.connect(str(self.db_path)) as conn:
            conn.row_factory = sqlite3.Row
            if not DouyinRatingOverviewDialog._table_exists(conn, "cache_inventory_current"):
                return []
            rows = conn.execute('SELECT * FROM "cache_inventory_current"').fetchall()
        candidates = []
        for row in rows:
            row_dict = dict(row)
            uid = str(row_dict.get("UP主UID") or "").strip()
            if uid and uid in self.reset_uids:
                continue
            if not self._is_full_row(row_dict):
                continue
            published = self._safe_int(row_dict.get("发布视频数量"))
            cached = self._safe_int(row_dict.get("缓存视频数"))
            diff = published - cached
            if published <= 0 or diff <= threshold:
                continue
            candidates.append(
                {
                    "uploader_id": uid,
                    "uploader_name": row_dict.get("UP主姓名") or "",
                    "published_video_count": published,
                    "cached_video_count": cached,
                    "diff_count": diff,
                    "last_fetch_mode": row_dict.get("最近抓取模式") or "",
                    "cache_modes": row_dict.get("已缓存模式") or "",
                    "progress_cached_at": row_dict.get("进度缓存时间") or "",
                    "homepage_url": row_dict.get("UP主主页链接") or "",
                }
            )
        candidates.sort(key=lambda item: item["diff_count"], reverse=True)
        return candidates

    def _load_reset_uids(self):
        reset_uids = self._load_db_reset_uids()
        try:
            from douyin_analyzer.cache import CacheStore

            progress = CacheStore(self.config).load_progress()
        except Exception:
            return reset_uids
        for uid, entry in (progress or {}).items():
            if not isinstance(entry, dict):
                continue
            summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
            if entry.get("full_status_reset") or str(summary.get("summary_scope") or "").strip().lower() == "status_reset":
                reset_uids.add(str(uid or "").strip())
        return {uid for uid in reset_uids if uid}

    def _load_db_reset_uids(self):
        if not self.db_path.exists():
            return set()
        with sqlite3.connect(str(self.db_path)) as conn:
            if not DouyinRatingOverviewDialog._table_exists(conn, "douyin_full_status_reset"):
                return set()
            rows = conn.execute(
                """
                SELECT uploader_id
                FROM douyin_full_status_reset
                WHERE reset_status = 'active'
                """
            ).fetchall()
        return {str(row[0] or "").strip() for row in rows if str(row[0] or "").strip()}

    def refresh_data(self):
        try:
            self.rows = self._load_candidates()
        except Exception as exc:
            self.summary_label.setText(f"读取异常 full 状态列表失败：{exc}")
            self.table.setRowCount(0)
            return
        self._populate_table(self.rows)
        self.summary_label.setText(
            f"数据库：{self.db_path}\n"
            f"候选：{len(self.rows)} 位；重置只会撤销 full 状态并置为过期，不删除视频缓存和评分数据。"
        )

    def _populate_table(self, rows):
        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (label, key) in enumerate(self.COLUMNS):
                value = row.get(key)
                item = QTableWidgetItem(self._fmt(value))
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                if column_index == 0:
                    item.setData(Qt.UserRole + 1, row.get("uploader_id", ""))
                if label == "UP主主页链接" and value:
                    item.setText("打开主页")
                    item.setToolTip(str(value))
                    item.setData(Qt.UserRole, str(value))
                    item.setForeground(Qt.blue)
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                self.table.setItem(row_index, column_index, item)
            self.table.setRowHeight(row_index, 42)
        self.table.setSortingEnabled(True)

    def _open_link_item(self, item):
        url = item.data(Qt.UserRole) if item else ""
        if url:
            QDesktopServices.openUrl(QUrl(str(url)))

    def _selected_uids(self):
        uids = []
        for index in self.table.selectionModel().selectedRows():
            item = self.table.item(index.row(), 0)
            uid = str(item.data(Qt.UserRole + 1) or "").strip() if item else ""
            if uid:
                uids.append(uid)
        return sorted(set(uids))

    def reset_selected(self):
        uids = self._selected_uids()
        if not uids:
            QMessageBox.information(self, "未选择", "请先选择需要重置 full 状态的 UP。")
            return
        if not QMessageBox.question(
            self,
            "确认重置状态",
            f"将重置 {len(uids)} 位 UP 的 full 状态标记，并让后续完整模式重新抓取。\n"
            "不会删除已有视频缓存、评分数据或下载文件。是否继续？",
        ) == QMessageBox.Yes:
            return
        try:
            count = self._reset_full_status(uids)
        except Exception as exc:
            QMessageBox.warning(self, "重置失败", str(exc))
            return
        QMessageBox.information(self, "重置完成", f"已重置 {count} 位 UP 的 full 状态。")
        self.refresh_data()

    def _reset_full_status(self, uids):
        from douyin_analyzer.cache import CacheStore

        cache_store = CacheStore(self.config)
        progress = cache_store.load_progress()
        if not isinstance(progress, dict):
            return 0
        candidate_by_uid = {row["uploader_id"]: row for row in self.rows}
        now_text = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        changed = 0
        for uid in uids:
            entry = progress.get(uid)
            row = candidate_by_uid.get(uid, {})
            if isinstance(entry, dict):
                raw_modes = entry.get("cache_modes") or []
                if isinstance(raw_modes, str):
                    raw_modes = raw_modes.split(",")
                modes = [
                    str(mode).strip().lower()
                    for mode in raw_modes
                    if str(mode).strip() and str(mode).strip().lower() != "full"
                ]
                entry["cache_modes"] = sorted(set(modes))
                entry["last_fetch_mode"] = "status_reset"
                entry["cached_at"] = 0
                entry["full_status_reset"] = {
                    "reset_at": now_text,
                    "reason": "full_cached_video_count_mismatch",
                    "published_video_count": row.get("published_video_count", ""),
                    "cached_video_count": row.get("cached_video_count", ""),
                    "diff_count": row.get("diff_count", ""),
                }
                summary = entry.get("summary")
                if isinstance(summary, dict):
                    summary["summary_scope"] = "status_reset"
                    summary["status_reset_at"] = now_text
                    summary["status_reset_reason"] = "full_cached_video_count_mismatch"
            changed += 1
        if changed:
            cache_store.save_progress(progress)
            self._record_reset_rows(uids, candidate_by_uid, now_text)
            self._update_inventory_rows(uids)
        return changed

    def _ensure_reset_table(self, conn):
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS douyin_full_status_reset (
                uploader_id TEXT PRIMARY KEY,
                uploader_name TEXT,
                reset_status TEXT NOT NULL DEFAULT 'active',
                reset_at TEXT NOT NULL,
                reset_reason TEXT,
                published_video_count INTEGER,
                cached_video_count INTEGER,
                diff_count INTEGER
            )
            """
        )

    def _record_reset_rows(self, uids, candidate_by_uid, reset_at):
        if not self.db_path.exists():
            return
        with sqlite3.connect(str(self.db_path)) as conn:
            self._ensure_reset_table(conn)
            for uid in uids:
                row = candidate_by_uid.get(uid, {})
                conn.execute(
                    """
                    INSERT INTO douyin_full_status_reset (
                        uploader_id,
                        uploader_name,
                        reset_status,
                        reset_at,
                        reset_reason,
                        published_video_count,
                        cached_video_count,
                        diff_count
                    )
                    VALUES (?, ?, 'active', ?, 'full_cached_video_count_mismatch', ?, ?, ?)
                    ON CONFLICT(uploader_id) DO UPDATE SET
                        uploader_name=excluded.uploader_name,
                        reset_status='active',
                        reset_at=excluded.reset_at,
                        reset_reason=excluded.reset_reason,
                        published_video_count=excluded.published_video_count,
                        cached_video_count=excluded.cached_video_count,
                        diff_count=excluded.diff_count
                    """,
                    (
                        uid,
                        row.get("uploader_name", ""),
                        reset_at,
                        self._safe_int(row.get("published_video_count")),
                        self._safe_int(row.get("cached_video_count")),
                        self._safe_int(row.get("diff_count")),
                    ),
                )
            conn.commit()

    def _update_inventory_rows(self, uids):
        if not self.db_path.exists():
            return
        with sqlite3.connect(str(self.db_path)) as conn:
            if not DouyinRatingOverviewDialog._table_exists(conn, "cache_inventory_current"):
                return
            for uid in uids:
                row = conn.execute(
                    'SELECT "已缓存模式" FROM "cache_inventory_current" WHERE "UP主UID" = ?',
                    (uid,),
                ).fetchone()
                modes = []
                if row:
                    modes = [
                        part.strip()
                        for part in str(row[0] or "").split(",")
                        if part.strip() and part.strip().lower() != "full"
                    ]
                conn.execute(
                    '''
                UPDATE "cache_inventory_current"
                SET "已缓存模式" = ?,
                    "最近抓取模式" = 'status_reset',
                    "有full缓存" = '',
                    "进度缓存时间" = '',
                    "下次可抓取时间" = '',
                    "是否已到期" = '是',
                    "统计范围" = 'status_reset'
                WHERE "UP主UID" = ?
                    ''',
                    (",".join(sorted(set(modes))), uid),
                )
            conn.commit()


class DouyinArchiveDialog(QDialog):
    CANDIDATE_COLUMNS = [
        ("UP主", "uploader_name"),
        ("未更新天数", "inactive_days"),
        ("最后发布时间", "latest_publish_time"),
        ("等级", "final_grade"),
        ("粉丝数", "follower_count"),
        ("作品数", "published_video_count"),
        ("缓存视频数", "cached_video_count"),
        ("最近抓取模式", "last_fetch_mode"),
        ("UP主主页链接", "homepage_url"),
    ]
    ARCHIVED_COLUMNS = [
        ("UP主", "uploader_name"),
        ("状态", "archive_status"),
        ("未更新天数", "inactive_days"),
        ("最后发布时间", "latest_publish_time"),
        ("等级", "final_grade"),
        ("归档时间", "archived_at"),
        ("归档原因", "archive_reason"),
        ("UP主主页链接", "homepage_url"),
    ]

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("抖音归档管理")
        self.resize(1280, 780)
        self.setMinimumSize(1080, 680)
        self.setStyleSheet(
            """
            QDialog {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-size: 15px;
                font-weight: 700;
                margin-top: 10px;
                padding-top: 14px;
            }
            QTabBar::tab {
                font-size: 15px;
                padding: 9px 22px;
                min-width: 120px;
                min-height: 28px;
            }
            QTableWidget {
                font-size: 15px;
                gridline-color: #d8dde6;
                alternate-background-color: #f7f8fb;
                selection-background-color: #dbeafe;
                selection-color: #111827;
            }
            QHeaderView::section {
                font-size: 15px;
                font-weight: 700;
                padding: 8px 10px;
                background: #f1f5f9;
                border: 1px solid #d8dde6;
            }
            QPushButton {
                font-size: 14px;
                padding: 7px 16px;
                min-height: 30px;
            }
            """
        )

        from douyin_analyzer.config import load_analyzer_config

        self.config = load_analyzer_config()
        self.db_path = Path(self.config.export_store_db)
        self.candidates = []
        self.archived_rows = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.summary_label = QLabel(
            "归档只记录本地状态，不删除缓存、CSV、评分或视频数据。active 归档对象会在后续主流程中默认跳过。"
        )
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet(
            "padding: 10px 12px; color: #263241; background: #f8fafc; "
            "border: 1px solid #d8dde6; border-radius: 8px; font-size: 14px;"
        )
        layout.addWidget(self.summary_label)

        filter_group = QGroupBox("筛选条件")
        filter_row = QHBoxLayout(filter_group)
        filter_row.setContentsMargins(14, 16, 14, 12)
        filter_row.setSpacing(10)
        filter_row.addWidget(QLabel("未更新天数 ≥"))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 10000)
        self.threshold_spin.setValue(100)
        self.threshold_spin.setMaximumWidth(120)
        filter_row.addWidget(self.threshold_spin)
        filter_row.addWidget(QLabel("仅展示已有 full 缓存/完整数据，且未手动 S/A 保留、未 active 归档的 UP。"))
        filter_row.addStretch(1)
        self.refresh_button = QPushButton("刷新候选")
        self.refresh_button.clicked.connect(self.refresh_data)
        filter_row.addWidget(self.refresh_button)
        layout.addWidget(filter_group)

        self.tabs = QTabWidget()
        self.candidate_table = self._make_table([label for label, _ in self.CANDIDATE_COLUMNS])
        self.archived_table = self._make_table([label for label, _ in self.ARCHIVED_COLUMNS])
        self.tabs.addTab(self.candidate_table, "候选归档")
        self.tabs.addTab(self.archived_table, "已归档/已恢复")
        layout.addWidget(self.tabs, stretch=1)

        button_row = QHBoxLayout()
        button_row.setSpacing(10)
        self.archive_selected_button = QPushButton("归档选中")
        self.archive_all_button = QPushButton("归档全部候选")
        self.restore_selected_button = QPushButton("恢复选中")
        self.close_button = QPushButton("关闭")
        self.archive_selected_button.clicked.connect(self.archive_selected)
        self.archive_all_button.clicked.connect(self.archive_all)
        self.restore_selected_button.clicked.connect(self.restore_selected)
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.archive_selected_button)
        button_row.addWidget(self.archive_all_button)
        button_row.addWidget(self.restore_selected_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_data()

    def _make_table(self, headers):
        table = QTableWidget()
        table.setColumnCount(len(headers))
        table.setHorizontalHeaderLabels(headers)
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setSelectionMode(QTableWidget.ExtendedSelection)
        table.setSortingEnabled(True)
        table.setWordWrap(False)
        table.itemClicked.connect(self._open_link_item)
        table.verticalHeader().setVisible(False)
        table.verticalHeader().setDefaultSectionSize(42)
        table.horizontalHeader().setMinimumHeight(42)
        for column, header in enumerate(headers):
            if header in {"UP主主页链接", "UP主UID"}:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
            else:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        return table

    @staticmethod
    def _fmt(value):
        if value is None:
            return ""
        if isinstance(value, float):
            return f"{value:.2f}"
        return str(value)

    @staticmethod
    def _sort_value(header_text, value):
        text = "" if value is None else str(value).strip()
        if header_text == "等级":
            return {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1}.get(text.upper(), 0)
        if header_text == "置信度":
            return {"很高": 5, "高": 4, "中": 3, "低": 2, "很低": 1}.get(text, 0)
        if header_text in {"未更新天数", "分数", "粉丝数", "作品数", "缓存视频数"}:
            try:
                return float(text.replace(",", ""))
            except ValueError:
                return -1
        return text.lower()

    def _populate_table(self, table, columns, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            uid = str(row.get("uploader_id") or "").strip()
            for column_index, (header, key) in enumerate(columns):
                value = row.get(key, "")
                text = self._fmt(value)
                item = SortableTableWidgetItem(text)
                item.setData(SORT_ROLE, self._sort_value(header, value))
                item.setData(Qt.UserRole + 1, uid)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item.setToolTip(text)
                if header == "UP主主页链接" and text:
                    item.setText("打开主页")
                    item.setData(Qt.UserRole, text)
                    item.setForeground(Qt.blue)
                    font = item.font()
                    font.setUnderline(True)
                    item.setFont(font)
                table.setItem(row_index, column_index, item)
            table.setRowHeight(row_index, 42)
        table.setSortingEnabled(True)

    def _open_link_item(self, item):
        url = item.data(Qt.UserRole) if item else ""
        if url:
            QDesktopServices.openUrl(QUrl(str(url)))

    def _selected_uids(self, table):
        rows = table.selectionModel().selectedRows() if table.selectionModel() else []
        uids = []
        for model_index in rows:
            item = table.item(model_index.row(), 0)
            uid = item.data(Qt.UserRole + 1) if item else ""
            if uid:
                uids.append(str(uid))
        return sorted(set(uids))

    def refresh_data(self):
        from douyin_analyzer.archive import load_archive_candidates, load_archived_creators

        _set_button_busy(self.refresh_button, "刷新中...")
        try:
            self.candidates = load_archive_candidates(
                self.db_path,
                inactive_days_threshold=self.threshold_spin.value(),
            )
            self.archived_rows = load_archived_creators(self.db_path, active_only=False)
            self._populate_table(self.candidate_table, self.CANDIDATE_COLUMNS, self.candidates)
            self._populate_table(self.archived_table, self.ARCHIVED_COLUMNS, self.archived_rows)
            active_count = sum(1 for row in self.archived_rows if str(row.get("archive_status") or "") == "active")
            self.summary_label.setText(
                f"数据库：{self.db_path}\n"
                f"候选归档：{len(self.candidates)} 位；active 已归档：{active_count} 位；"
                "归档不会删除任何历史数据，后续主流程会跳过 active 归档对象。"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取归档数据失败：{exc}")
            self.candidate_table.setRowCount(0)
            self.archived_table.setRowCount(0)
        finally:
            _restore_button_busy(self.refresh_button)

    def _candidate_rows_by_uids(self, uids):
        wanted = set(uids or [])
        return [row for row in self.candidates if str(row.get("uploader_id") or "") in wanted]

    def archive_selected(self):
        uids = self._selected_uids(self.candidate_table)
        if not uids:
            QMessageBox.information(self, "请选择候选", "请先在候选归档表中选择要归档的 UP。")
            return
        self._archive_rows(self._candidate_rows_by_uids(uids))

    def archive_all(self):
        if not self.candidates:
            QMessageBox.information(self, "没有候选", "当前没有符合条件的归档候选。")
            return
        self._archive_rows(list(self.candidates))

    def _archive_rows(self, rows):
        from douyin_analyzer.archive import archive_creators

        if not rows:
            return
        reply = QMessageBox.question(
            self,
            "确认归档",
            f"确认将 {len(rows)} 位长期未更新 UP 标记为 active 归档吗？\n"
            "这只写入本地 SQLite 归档表，不删除历史数据。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        count = archive_creators(self.db_path, rows)
        QMessageBox.information(self, "归档完成", f"已归档 {count} 位 UP。")
        self.refresh_data()

    def restore_selected(self):
        from douyin_analyzer.archive import restore_creators

        uids = self._selected_uids(self.archived_table)
        if not uids:
            QMessageBox.information(self, "请选择归档对象", "请先在已归档列表中选择要恢复的 UP。")
            return
        reply = QMessageBox.question(
            self,
            "确认恢复",
            f"确认恢复 {len(uids)} 位 UP 的归档状态吗？恢复后主流程会重新允许处理这些对象。",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if reply != QMessageBox.Yes:
            return
        count = restore_creators(self.db_path, uids)
        QMessageBox.information(self, "恢复完成", f"已恢复 {count} 位 active 归档 UP。")
        self.refresh_data()


class LogCenterDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("日志中心")
        self.resize(1120, 680)
        self.setMinimumSize(900, 520)
        self.setStyleSheet(
            """
            QDialog {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QPushButton {
                font-size: 14px;
                min-height: 32px;
                padding: 6px 16px;
            }
            QLabel {
                color: #475569;
                font-size: 14px;
            }
            """
        )

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        header_row = QHBoxLayout()
        info_label = QLabel("运行日志会实时追加到这里；关闭窗口不会清空日志。")
        header_row.addWidget(info_label)
        header_row.addStretch(1)
        layout.addLayout(header_row)

        self.log_text = QTextEdit()
        self.log_text.setReadOnly(True)
        self.log_text.setLineWrapMode(QTextEdit.NoWrap)
        self.log_text.setStyleSheet(
            "font-family: Consolas, 'Microsoft YaHei UI'; font-size: 14px; "
            "background: #10141f; color: #d7e0f0; border: 1px solid #263244;"
        )
        layout.addWidget(self.log_text, stretch=1)

        button_row = QHBoxLayout()
        button_row.addStretch(1)
        self.clear_button = QPushButton("清空日志")
        self.close_button = QPushButton("关闭")
        self.clear_button.clicked.connect(self.log_text.clear)
        self.close_button.clicked.connect(self.hide)
        button_row.addWidget(self.clear_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

    def closeEvent(self, event):
        event.ignore()
        self.hide()


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
        self.data_sync_worker = None
        self.log_dialog = None
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
        self.setWindowTitle("B站/抖音数据分析系统")
        self.resize(1240, 720)
        self.setMinimumSize(1120, 660)
        self._apply_readable_style()
        self._build_ui()
        self._load_gui_config()
        self._sync_visible_options()

    def _apply_readable_style(self):
        self.setStyleSheet(
            """
            QMainWindow, QWidget {
                font-family: "Microsoft YaHei UI", "Segoe UI", sans-serif;
                font-size: 14px;
            }
            QGroupBox {
                font-size: 15px;
                font-weight: 700;
                margin-top: 10px;
                padding-top: 14px;
            }
            QGroupBox::title {
                subcontrol-origin: margin;
                left: 10px;
                padding: 0 6px;
            }
            QLabel {
                font-size: 14px;
            }
            QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox {
                font-size: 14px;
                min-height: 30px;
                padding: 3px 6px;
            }
            QRadioButton {
                font-size: 14px;
                spacing: 8px;
            }
            QPushButton {
                font-size: 14px;
                min-height: 32px;
                padding: 6px 14px;
            }
            QProgressBar {
                font-size: 13px;
                min-height: 26px;
                text-align: center;
            }
            #StatusCard {
                background: #f8fafc;
                border: 1px solid #d8dde6;
                border-radius: 8px;
            }
            """
        )

    def _build_ui(self):
        root = QWidget(self)
        layout = QVBoxLayout(root)
        layout.setContentsMargins(18, 16, 18, 16)
        layout.setSpacing(10)
        layout.setAlignment(Qt.AlignTop)

        settings_group = QGroupBox("运行设置")
        settings_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        settings_grid = QGridLayout(settings_group)
        settings_grid.setContentsMargins(14, 18, 14, 14)
        settings_grid.setHorizontalSpacing(16)
        settings_grid.setVerticalSpacing(10)
        settings_grid.setColumnMinimumWidth(0, 96)
        settings_grid.setColumnMinimumWidth(2, 120)
        settings_grid.setColumnStretch(1, 1)
        settings_grid.setColumnStretch(3, 1)

        def add_setting(row, left_label, left_widget, right_label=None, right_widget=None):
            settings_grid.addWidget(QLabel(left_label), row, 0)
            settings_grid.addWidget(left_widget, row, 1)
            if right_label is not None and right_widget is not None:
                settings_grid.addWidget(QLabel(right_label), row, 2)
                settings_grid.addWidget(right_widget, row, 3)

        self.platform_combo = QComboBox()
        for label, value in self.PLATFORM_OPTIONS:
            self.platform_combo.addItem(label, value)
        self.platform_combo.currentIndexChanged.connect(self._sync_visible_options)

        self.action_combo = QComboBox()
        for label, value in self.ACTION_OPTIONS:
            self.action_combo.addItem(label, value)
        add_setting(0, "平台/模式", self.platform_combo, "动作", self.action_combo)

        self.bilibili_mode_combo = QComboBox()
        for label, mode in self.BILIBILI_MODE_OPTIONS:
            self.bilibili_mode_combo.addItem(label, mode)
        self.bilibili_mode_combo.setCurrentIndex(0)

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
        add_setting(1, "B站抓取模式", self.bilibili_mode_combo, "抖音抓取模式", self.douyin_mode_combo)

        self.monitor_video_limit_spin = QSpinBox()
        self.monitor_video_limit_spin.setRange(1, 500)
        self.monitor_video_limit_spin.setValue(10)
        self.monitor_video_limit_spin.setMaximumWidth(140)
        self.monitor_video_limit_spin.setToolTip("监控/增量模式下，每位博主最多抓取最近 N 条视频。基础统计和完整模式会忽略该参数。")

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("DrissionPage", "drission")
        self.backend_combo.addItem("Playwright", "playwright")
        add_setting(2, "监控视频数", self.monitor_video_limit_spin, "抖音浏览器后端", self.backend_combo)

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
        self.uid_limit_spin.setMaximumWidth(120)
        self.uid_limit_spin.setToolTip("部分抓取模式下生效；全抓取模式会忽略该数量。")
        fetch_mode_row = QHBoxLayout()
        fetch_mode_row.addWidget(self.uid_fetch_all_radio)
        fetch_mode_row.addWidget(self.uid_fetch_partial_radio)
        fetch_mode_row.addStretch(1)
        fetch_mode_widget = QWidget()
        fetch_mode_widget.setLayout(fetch_mode_row)

        limit_row = QHBoxLayout()
        limit_row.addWidget(QLabel("抓取前 N 个 UID"))
        limit_row.addWidget(self.uid_limit_spin)
        limit_row.addStretch(1)
        limit_widget = QWidget()
        limit_widget.setLayout(limit_row)
        add_setting(3, "抓取方式", fetch_mode_widget, "UID 数量", limit_widget)

        self.high_like_spin = QSpinBox()
        self.high_like_spin.setRange(1, 100000000)
        self.high_like_spin.setValue(10000)
        self.high_like_spin.setMaximumWidth(180)
        self.high_like_spin.setToolTip("导出抖音高赞视频时使用，其它模式不会使用该参数。")
        add_setting(4, "高赞阈值", self.high_like_spin)
        layout.addWidget(settings_group)

        self.log_dialog = LogCenterDialog(self)
        self.log_text = self.log_dialog.log_text

        controls_group = QGroupBox("快捷操作")
        controls_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setContentsMargins(14, 16, 14, 12)
        controls_layout.setSpacing(8)
        button_grid = QGridLayout()
        button_grid.setHorizontalSpacing(10)
        button_grid.setVerticalSpacing(8)
        for column in range(7):
            button_grid.setColumnStretch(column, 1)
        self.start_button = QPushButton("开始运行")
        self.start_button.setStyleSheet("font-weight: 700;")
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
        self.archive_button = QPushButton("归档管理")
        self.archive_button.clicked.connect(self._open_archive_manager)
        self.douyin_status_reset_button = QPushButton("抖音状态重置")
        self.douyin_status_reset_button.clicked.connect(self._open_douyin_status_reset)
        self.douyin_data_sync_button = QPushButton("抖音数据同步")
        self.douyin_data_sync_button.clicked.connect(self._start_douyin_data_sync)
        self.log_center_button = QPushButton("日志中心")
        self.log_center_button.clicked.connect(self._open_log_center)
        self.cookie_check_button = QPushButton("检测 B站 Cookie")
        self.cookie_check_button.clicked.connect(self._check_bilibili_cookie)
        self.advanced_button = QPushButton("高级设置")
        self.advanced_button.clicked.connect(self._open_advanced_settings)
        self.lock_button = QPushButton("锁定配置")
        self.lock_button.clicked.connect(self._toggle_config_lock)
        self.clear_button = QPushButton("清空日志")
        self.clear_button.clicked.connect(self.log_text.clear)
        toolbar_buttons = (
            self.start_button,
            self.stop_button,
            self.high_like_export_button,
            self.video_download_button,
            self.unfollow_cleanup_button,
            self.douyin_stats_button,
            self.rating_overview_button,
            self.archive_button,
            self.douyin_status_reset_button,
            self.douyin_data_sync_button,
            self.log_center_button,
            self.cookie_check_button,
            self.advanced_button,
            self.lock_button,
            self.clear_button,
        )
        for button in toolbar_buttons:
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            button.setMinimumHeight(30)
            button.setMaximumHeight(32)
            button.setStyleSheet((button.styleSheet() + " " if button.styleSheet() else "") + "font-size: 14px; padding: 4px 8px;")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for index, button in enumerate(toolbar_buttons):
            button_grid.addWidget(button, index // 7, index % 7)
        controls_layout.addLayout(button_grid)
        layout.addWidget(controls_group)

        status_group = QGroupBox("运行状态")
        status_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        status_layout = QGridLayout(status_group)
        status_layout.setContentsMargins(14, 18, 14, 14)
        status_layout.setHorizontalSpacing(12)
        status_layout.setVerticalSpacing(8)

        self.cookie_status_label = QLabel("B站 Cookie：未检测")
        self.cookie_status_label.setStyleSheet("color: #475569; font-size: 14px;")
        status_layout.addWidget(QLabel("Cookie 状态"), 0, 0)
        status_layout.addWidget(self.cookie_status_label, 0, 1)

        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setFormat("等待开始")
        self.progress_bar.setStyleSheet("QProgressBar { min-height: 28px; }")
        self.progress_label = QLabel("请选择配置后点击开始运行")
        self.progress_label.setStyleSheet("padding: 3px 0 0 2px; color: #555; font-size: 14px;")
        status_layout.addWidget(QLabel("抓取进度"), 1, 0)
        status_layout.addWidget(self.progress_bar, 1, 1)
        status_layout.addWidget(self.progress_label, 2, 1)
        status_layout.setColumnStretch(1, 1)
        layout.addWidget(status_group)
        layout.addStretch(1)

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

    def _open_archive_manager(self):
        dialog = DouyinArchiveDialog(self)
        dialog.exec_()

    def _open_douyin_status_reset(self):
        dialog = DouyinStatusResetDialog(self)
        dialog.exec_()

    def _start_douyin_data_sync(self):
        if self.worker and self.worker.isRunning():
            self._show_info_dialog("任务运行中", "当前任务还在运行，请等待完成。")
            return
        if self.data_sync_worker and self.data_sync_worker.isRunning():
            self._show_info_dialog("同步运行中", "抖音数据同步还在运行，请等待完成。")
            return
        if (
            QMessageBox.question(
                self,
                "确认数据同步",
                "将从本地 progress/raw 缓存补写视频明细到 SQLite，并重新生成视频评分和 UP 主评分。\n"
                "不会重新抓取网页，也不会删除缓存。是否继续？",
            )
            != QMessageBox.Yes
        ):
            return

        self._append_log("开始抖音数据同步：progress/raw -> douyin_video_state -> video_score_current -> creator_score_current")
        self._start_task_progress("抖音数据同步中，正在补齐本地表数据...")
        _set_button_busy(self.douyin_data_sync_button, "同步中...")
        self.data_sync_worker = DouyinDataSyncThread()
        self.data_sync_worker.done.connect(self._on_douyin_data_sync_done)
        self.data_sync_worker.start()

    def _on_douyin_data_sync_done(self, ok, message):
        _restore_button_busy(self.douyin_data_sync_button)
        self._append_log(message)
        self._finish_task_progress(ok, message)
        if ok:
            self._show_info_dialog("同步完成", message)
        else:
            self._show_error_dialog("同步失败", message)

    def _open_log_center(self):
        if self.log_dialog is None:
            self.log_dialog = LogCenterDialog(self)
            self.log_text = self.log_dialog.log_text
        self.log_dialog.show()
        self.log_dialog.raise_()
        self.log_dialog.activateWindow()

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
        self.cookie_status_label.setStyleSheet("color: #1565c0; font-size: 14px; font-weight: 700;")
        self.cookie_checker = BilibiliCookieCheckThread()
        self.cookie_checker.checked.connect(self._on_bilibili_cookie_checked)
        self.cookie_checker.start()

    def _on_bilibili_cookie_checked(self, ok, message):
        self.cookie_check_button.setEnabled(True)
        self.cookie_check_button.setText("检测 B站 Cookie")
        if ok:
            self.cookie_status_label.setText(f"B站 Cookie：{message}")
            self.cookie_status_label.setStyleSheet("color: #2e7d32; font-size: 14px; font-weight: 700;")
            self._append_log(f"B站 Cookie 状态检测：{message}")
            self._show_info_dialog("B站 Cookie 状态", message)
        else:
            self.cookie_status_label.setText(f"B站 Cookie：{message}")
            self.cookie_status_label.setStyleSheet("color: #c62828; font-size: 14px; font-weight: 700;")
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
