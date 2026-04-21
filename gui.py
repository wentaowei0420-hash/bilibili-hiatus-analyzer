import os
import json
import sys
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from pathlib import Path

import requests
from PyQt5.QtCore import QThread, Qt, pyqtSignal
from PyQt5.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QFileDialog,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from common.runtime_control import OperationCancelled, clear_stop, request_stop


ROOT_DIR = Path(__file__).resolve().parent
DEFAULT_DOUYIN_UNFOLLOW_LIST = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_unfollow_list.txt"
DEFAULT_BILIBILI_UID_LIST = ROOT_DIR / "data" / "bilibili" / "ops" / "bilibili_uid_fetch_list.txt"
DEFAULT_DOUYIN_UID_LIST = ROOT_DIR / "data" / "douyin" / "ops" / "douyin_uid_fetch_list.txt"
GUI_CONFIG_PATH = ROOT_DIR / "data" / "state" / "gui_config.json"


@dataclass
class RunConfig:
    platform: str
    action: str
    bilibili_mode: str
    douyin_fetch_mode: str
    douyin_backend: str
    uid_limit_enabled: bool
    uid_limit: int
    high_like_threshold: int
    unfollow_list_path: Path
    bilibili_uid_list_path: Path
    douyin_uid_list_path: Path


class SignalWriter:
    def __init__(self, signal):
        self.signal = signal
        self._buffer = ""
        self.encoding = "utf-8"

    def write(self, text):
        if not text:
            QMessageBox.information(self, "任务运行中", "当前任务还在运行，请等待完成。")
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
        elif self.config.platform == "bilibili_uid":
            from bilibili_analyzer.app import run_fetch_uid_videos

            run_fetch_uid_videos(self.config.bilibili_uid_list_path, max_targets=uid_limit)
        elif self.config.platform == "douyin_uid":
            from douyin_analyzer.app import run_fetch_uid_videos

            run_fetch_uid_videos(self.config.douyin_uid_list_path, max_targets=uid_limit)
        elif self.config.platform == "douyin_high_like":
            from douyin_analyzer.app import run_export_high_like_videos_from_cache

            run_export_high_like_videos_from_cache(threshold=self.config.high_like_threshold)
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

    def _run_bilibili_main(self):
        from bilibili_analyzer.app import run_analysis, run_feishu_upload

        if self.config.action == "fetch":
            result = run_analysis(trigger_upload=False)
            if result is None:
                raise RuntimeError("B站分析未成功完成，请检查 BILIBILI_COOKIE 是否已失效。")
        elif self.config.action == "fetch_upload":
            result = run_analysis(trigger_upload=True)
            if result is None:
                raise RuntimeError("B站分析未成功完成，请检查 BILIBILI_COOKIE 是否已失效。")
        elif self.config.action == "upload":
            run_feishu_upload()
        else:
            raise ValueError(f"未知动作: {self.config.action}")

    def _run_douyin_main(self):
        from douyin_analyzer.app import run_analysis, run_feishu_upload

        if self.config.action == "fetch":
            result = run_analysis(trigger_upload=False, fetch_mode_override=self.config.douyin_fetch_mode)
            if result is None:
                raise RuntimeError("抖音分析未成功完成，请检查登录状态或抓取配置。")
        elif self.config.action == "fetch_upload":
            result = run_analysis(trigger_upload=True, fetch_mode_override=self.config.douyin_fetch_mode)
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


class AdvancedSettingsDialog(QDialog):
    def __init__(self, parent, current_paths):
        super().__init__(parent)
        self.setWindowTitle("高级设置")
        self.resize(760, 220)
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)

        self.unfollow_path_edit = self._path_row(form, "取消关注名单", current_paths["unfollow"])
        self.bilibili_uid_path_edit = self._path_row(form, "B站 UID 名单", current_paths["bilibili_uid"])
        self.douyin_uid_path_edit = self._path_row(form, "抖音 UID 名单", current_paths["douyin_uid"])

        button_row = QHBoxLayout()
        self.save_button = QPushButton("保存")
        self.cancel_button = QPushButton("取消")
        self.save_button.clicked.connect(self.accept)
        self.cancel_button.clicked.connect(self.reject)
        button_row.addStretch(1)
        button_row.addWidget(self.save_button)
        button_row.addWidget(self.cancel_button)
        layout.addLayout(button_row)

    def _path_row(self, form, label, value):
        row = QHBoxLayout()
        edit = QLineEdit(str(value))
        browse_button = QPushButton("选择")
        browse_button.clicked.connect(lambda: self._browse_file(edit))
        row.addWidget(edit, stretch=1)
        row.addWidget(browse_button)
        form.addRow(label, row)
        return edit

    def _browse_file(self, edit):
        selected, _ = QFileDialog.getOpenFileName(self, "选择名单文件", str(ROOT_DIR), "Text Files (*.txt);;All Files (*)")
        if selected:
            edit.setText(selected)

    def paths(self):
        return {
            "unfollow": self.unfollow_path_edit.text(),
            "bilibili_uid": self.bilibili_uid_path_edit.text(),
            "douyin_uid": self.douyin_uid_path_edit.text(),
        }


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
            ("监控模式（推荐日常使用）", "monitor"),
            ("增量模式（只补变化数据）", "delta"),
            ("完整模式（抓取视频明细）", "full"),
        ):
            self.douyin_mode_combo.addItem(label, mode)
        self.douyin_mode_combo.setCurrentIndex(1)
        run_form.addRow("抖音抓取模式", self.douyin_mode_combo)

        self.backend_combo = QComboBox()
        self.backend_combo.addItem("DrissionPage", "drission")
        self.backend_combo.addItem("Playwright", "playwright")
        run_form.addRow("抖音浏览器后端", self.backend_combo)

        config_layout.addWidget(run_group, 0, 0)

        uid_group = QGroupBox("UID 与筛选参数")
        uid_form = QFormLayout(uid_group)
        self.uid_limit_check = QCheckBox("只抓取前 N 个 UID")
        self.uid_limit_check.setToolTip("仅在 B站/抖音 UID 全量视频模式下生效。未勾选时抓取全部 UID。")
        self.uid_limit_check.toggled.connect(self._sync_visible_options)
        self.uid_limit_spin = QSpinBox()
        self.uid_limit_spin.setRange(1, 100000)
        self.uid_limit_spin.setValue(100)
        self.uid_limit_spin.setToolTip("可提前填写；仅在勾选“只抓取前 N 个 UID”且运行 UID 全量视频模式时生效。")
        limit_row = QHBoxLayout()
        limit_row.addWidget(self.uid_limit_check)
        limit_row.addWidget(self.uid_limit_spin)
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
        button_row.addWidget(self.cookie_check_button)
        button_row.addWidget(self.advanced_button)
        button_row.addWidget(self.lock_button)
        button_row.addWidget(self.clear_button)
        layout.addLayout(button_row)

        self.cookie_status_label = QLabel("B站 Cookie：未检测")
        self.cookie_status_label.setStyleSheet("padding: 2px 0 8px 2px; color: #666;")
        layout.addWidget(self.cookie_status_label)

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

        editable = not self.config_locked
        self.action_combo.setEnabled(editable and is_normal)
        self.bilibili_mode_combo.setEnabled(editable and is_bilibili)
        self.douyin_mode_combo.setEnabled(editable and is_douyin and platform != "douyin_unfollow")
        self.backend_combo.setEnabled(editable and is_douyin)
        self.platform_combo.setEnabled(editable)
        self.uid_limit_check.setEnabled(editable)
        self.uid_limit_spin.setEnabled(editable)
        self.high_like_spin.setEnabled(editable)
        self.high_like_export_button.setEnabled(editable)
        self.advanced_button.setEnabled(editable)

        self.lock_button.setText("解除锁定" if self.config_locked else "锁定配置")

    def _collect_config(self):
        return RunConfig(
            platform=self.platform_combo.currentData(),
            action=self.action_combo.currentData(),
            bilibili_mode=self.bilibili_mode_combo.currentData(),
            douyin_fetch_mode=self.douyin_mode_combo.currentData(),
            douyin_backend=self.backend_combo.currentData(),
            uid_limit_enabled=self.uid_limit_check.isChecked(),
            uid_limit=self.uid_limit_spin.value(),
            high_like_threshold=self.high_like_spin.value(),
            unfollow_list_path=Path(self.unfollow_list_path).expanduser(),
            bilibili_uid_list_path=Path(self.bilibili_uid_list_path).expanduser(),
            douyin_uid_list_path=Path(self.douyin_uid_list_path).expanduser(),
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
            "uid_limit_enabled": self.uid_limit_check.isChecked(),
            "uid_limit": self.uid_limit_spin.value(),
            "high_like_threshold": self.high_like_spin.value(),
            "unfollow_list_path": self.unfollow_list_path,
            "bilibili_uid_list_path": self.bilibili_uid_list_path,
            "douyin_uid_list_path": self.douyin_uid_list_path,
        }

    def _save_gui_config(self):
        GUI_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with GUI_CONFIG_PATH.open("w", encoding="utf-8") as config_file:
            json.dump(self._snapshot_gui_config(), config_file, ensure_ascii=False, indent=2)

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

        self.uid_limit_check.setChecked(bool(data.get("uid_limit_enabled", False)))
        self.uid_limit_spin.setValue(int(data.get("uid_limit", self.uid_limit_spin.value()) or self.uid_limit_spin.value()))
        self.high_like_spin.setValue(
            int(data.get("high_like_threshold", self.high_like_spin.value()) or self.high_like_spin.value())
        )
        self.unfollow_list_path = data.get("unfollow_list_path") or str(DEFAULT_DOUYIN_UNFOLLOW_LIST)
        self.bilibili_uid_list_path = data.get("bilibili_uid_list_path") or str(DEFAULT_BILIBILI_UID_LIST)
        self.douyin_uid_list_path = data.get("douyin_uid_list_path") or str(DEFAULT_DOUYIN_UID_LIST)
        self.config_locked = bool(data.get("locked", False))

    def _open_advanced_settings(self):
        dialog = AdvancedSettingsDialog(
            self,
            {
                "unfollow": self.unfollow_list_path,
                "bilibili_uid": self.bilibili_uid_list_path,
                "douyin_uid": self.douyin_uid_list_path,
            },
        )
        if dialog.exec_() == QDialog.Accepted:
            paths = dialog.paths()
            self.unfollow_list_path = paths["unfollow"]
            self.bilibili_uid_list_path = paths["bilibili_uid"]
            self.douyin_uid_list_path = paths["douyin_uid"]
            self._save_gui_config()
            self._append_log("高级设置已保存。")

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

    def _start(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "任务运行中", "当前任务还在运行，请等待完成。")
            return

        config = self._collect_config()
        if not self._validate_config(config):
            return
        if self.config_locked:
            self._save_gui_config()

        self.log_text.clear()
        self.start_button.setEnabled(False)
        self.start_button.setText("运行中...")
        self.high_like_export_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("终止运行")
        self.stop_button.setStyleSheet("")
        self.worker = RunnerThread(config)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _start_high_like_export(self):
        if self.worker and self.worker.isRunning():
            QMessageBox.information(self, "任务运行中", "当前任务还在运行，请等待完成。")
            return

        config = self._collect_config()
        config.platform = "douyin_high_like"
        config.action = "fetch"
        if self.config_locked:
            self._save_gui_config()

        self.log_text.clear()
        self.start_button.setEnabled(False)
        self.high_like_export_button.setEnabled(False)
        self.high_like_export_button.setText("导出中...")
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
            QMessageBox.information(self, "B站 Cookie 状态", message)
        else:
            self.cookie_status_label.setText(f"B站 Cookie：{message}")
            self.cookie_status_label.setStyleSheet("padding: 2px 0 8px 2px; color: #c62828; font-weight: 700;")
            self._append_log(f"B站 Cookie 状态检测：{message}")
            QMessageBox.warning(self, "B站 Cookie 状态", message)

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
            QMessageBox.warning(self, "名单文件不存在", "\n".join(missing))
            return False
        return True

    def _append_log(self, line):
        self.log_text.append(line)
        scrollbar = self.log_text.verticalScrollBar()
        scrollbar.setValue(scrollbar.maximum())

    def _on_done(self, ok, message):
        self.start_button.setEnabled(True)
        self.start_button.setText("开始运行")
        self.high_like_export_button.setEnabled(not self.config_locked)
        self.high_like_export_button.setText("导出高赞视频")
        self.stop_button.setEnabled(False)
        if message.startswith("已终止运行"):
            self.stop_button.setText("保存完成，可以关闭")
            self.stop_button.setStyleSheet("background-color: #2e7d32; color: white; font-weight: 700;")
        else:
            self.stop_button.setText("终止运行")
            self.stop_button.setStyleSheet("")
        if ok:
            self._append_log("-" * 60)
            self._append_log(message)
        else:
            QMessageBox.critical(self, "任务失败", message)


def main():
    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()
