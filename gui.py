import sys
from datetime import datetime
from pathlib import Path

from PyQt5.QtCore import Qt, QTimer, QUrl
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

from gui_business import (
    DEFAULT_AUTO_FULL_INTERVAL_MINUTES,
    DEFAULT_BILIBILI_UID_LIST,
    DEFAULT_DOUYIN_UID_LIST,
    DEFAULT_DOUYIN_UNFOLLOW_LIST,
    GUI_CONFIG_PATH,
    ROOT_DIR,
    bucket_tuples,
    coerce_setting_value,
    column_pairs,
    extract_progress_current,
    extract_progress_total,
    fetch_order_option_map,
    launch_video_downloader_gui,
    load_backend_config_defaults,
    load_backend_gui_metadata,
    load_default_fetch_order_settings,
    load_gui_config,
    normalize_fetch_order_settings,
    option_pairs,
    runtime_field_tuples,
    save_gui_config,
    video_downloader_launch_commands,
)
from gui_backend_client import (
    ApiCallThread,
    BackendApiClient,
    BilibiliCookieCheckThread,
    DouyinLikedVideoCacheThread,
    RatingRefreshThread,
    RunnerThread,
)
from gui_models import RunConfig


BILIBILI_RUNTIME_FIELDS = []
DOUYIN_RUNTIME_FIELDS = []
BILIBILI_FETCH_ORDER_OPTIONS = []
DOUYIN_FETCH_ORDER_OPTIONS = []
FETCH_ORDER_DIRECTION_OPTIONS = []


def _fetch_order_option_map(options):
    return fetch_order_option_map(options)


def _option_pairs(items):
    return option_pairs(items)


def _column_pairs(items):
    return column_pairs(items)


def _runtime_field_tuples(items):
    return runtime_field_tuples(items)


def _bucket_tuples(items):
    return bucket_tuples(items)


def _load_backend_gui_metadata():
    return load_backend_gui_metadata()


def _apply_gui_metadata(metadata):
    global BILIBILI_RUNTIME_FIELDS
    global DOUYIN_RUNTIME_FIELDS
    global BILIBILI_FETCH_ORDER_OPTIONS
    global DOUYIN_FETCH_ORDER_OPTIONS
    global FETCH_ORDER_DIRECTION_OPTIONS

    runtime_fields = metadata.get("runtime_fields") or {}
    fetch_options = metadata.get("fetch_order_options") or {}
    main_window = metadata.get("main_window") or {}
    stats = metadata.get("stats") or {}
    rating = metadata.get("rating") or {}
    tables = metadata.get("tables") or {}

    BILIBILI_RUNTIME_FIELDS = _runtime_field_tuples(runtime_fields.get("bilibili"))
    DOUYIN_RUNTIME_FIELDS = _runtime_field_tuples(runtime_fields.get("douyin"))
    BILIBILI_FETCH_ORDER_OPTIONS = _option_pairs(fetch_options.get("bilibili"))
    DOUYIN_FETCH_ORDER_OPTIONS = _option_pairs(fetch_options.get("douyin"))
    FETCH_ORDER_DIRECTION_OPTIONS = _option_pairs(fetch_options.get("directions"))

    MainWindow.BILIBILI_MODE_OPTIONS = _option_pairs(main_window.get("bilibili_modes"))
    MainWindow.DOUYIN_MODE_OPTIONS = _option_pairs(main_window.get("douyin_modes"))
    MainWindow.BROWSER_BACKEND_OPTIONS = _option_pairs(main_window.get("browser_backends"))
    MainWindow.PLATFORM_OPTIONS = _option_pairs(main_window.get("platforms"))
    MainWindow.ACTION_OPTIONS = _option_pairs(main_window.get("actions"))
    DouyinStatsDialog.MODE_ROWS = [(value, label) for label, value in _option_pairs(stats.get("modes"))]
    DouyinStatsDialogV2.MODE_ROWS = [(value, label) for label, value in _option_pairs(stats.get("modes"))]
    DouyinStatsDialogV2.CREATOR_VIDEO_BUCKETS = _bucket_tuples(stats.get("creator_video_buckets"))
    DouyinStatsDialogV2.VIDEO_DURATION_BUCKETS = _bucket_tuples(stats.get("video_duration_buckets"))
    DouyinRatingOverviewDialog.GRADE_ORDER = tuple(rating.get("grades") or ())
    DouyinRatingOverviewDialog.CREATOR_TOP_COLUMNS = _column_pairs(tables.get("rating_creator_top"))
    DouyinRatingOverviewDialog.CREATOR_LADDER_COLUMNS = _column_pairs(tables.get("rating_creator_ladder"))
    DouyinRatingOverviewDialog.CREATOR_LOW_COLUMNS = _column_pairs(tables.get("rating_creator_low"))
    DouyinRatingOverviewDialog.ARCHIVED_CREATOR_COLUMNS = _column_pairs(tables.get("rating_archived_creator"))
    CreatorDetailDialog.FACTOR_COLUMNS = _column_pairs(rating.get("factor_columns"))
    DouyinStatusResetDialog.COLUMNS = _column_pairs(tables.get("status_reset"))
    DouyinArchiveDialog.CANDIDATE_COLUMNS = _column_pairs(tables.get("archive_candidates"))
    DouyinArchiveDialog.ARCHIVED_COLUMNS = _column_pairs(tables.get("archived"))


def _coerce_setting_value(value, field_type, fallback):
    return coerce_setting_value(value, field_type, fallback)


def _load_default_fetch_order_settings():
    return load_default_fetch_order_settings()


def _load_backend_config_defaults():
    return load_backend_config_defaults(
        bilibili_options=BILIBILI_FETCH_ORDER_OPTIONS,
        douyin_options=DOUYIN_FETCH_ORDER_OPTIONS,
        direction_options=FETCH_ORDER_DIRECTION_OPTIONS,
    )


def _normalize_fetch_order_settings(settings):
    return normalize_fetch_order_settings(
        settings,
        bilibili_options=BILIBILI_FETCH_ORDER_OPTIONS,
        douyin_options=DOUYIN_FETCH_ORDER_OPTIONS,
        direction_options=FETCH_ORDER_DIRECTION_OPTIONS,
    )


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
        bilibili_field = self._fetch_order_settings["bilibili"]["field"]
        douyin_field = self._fetch_order_settings["douyin"]["field"]
        bilibili_direction = self._fetch_order_settings["bilibili"]["direction"]
        douyin_direction = self._fetch_order_settings["douyin"]["direction"]
        bilibili_text = f"B\u7ad9\uff1a\u6309 {bilibili_labels.get(bilibili_field, bilibili_field)} {direction_labels.get(bilibili_direction, bilibili_direction)}"
        douyin_text = f"\u6296\u97f3\uff1a\u6309 {douyin_labels.get(douyin_field, douyin_field)} {direction_labels.get(douyin_direction, douyin_direction)}"
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
    MODE_ROWS = []

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
        if getattr(self, "stats_worker", None) and self.stats_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "刷新中...")
        self.close_button.setEnabled(False)
        self.summary_label.setText("正在后台读取抖音缓存统计...")
        self.stats_worker = ApiCallThread("douyin_stats", self.high_like_threshold, parent=self)
        self.stats_worker.completed.connect(self._on_stats_loaded)
        self.stats_worker.start()
        return
        _set_button_busy(self.refresh_button, "刷新中...")
        try:
            data = BackendApiClient().douyin_stats(self.high_like_threshold)
            total_followings = int(data.get("total_followings") or 0)
            for mode, _ in self.MODE_ROWS:
                mode_data = (data.get("modes") or {}).get(mode, {})
                captured_count = int(mode_data.get("count") or 0)
                self.mode_count_labels[mode].setText(str(captured_count))
                self.mode_percent_labels[mode].setText(f"{float(mode_data.get('percent') or 0):.2f}%")

            cached_video_count = int(data.get("cached_video_count") or 0)
            high_like_video_count = int(data.get("high_like_video_count") or 0)
            self.cached_video_count_label.setText(str(cached_video_count))
            self.cached_video_ratio_label.setText("100.00%" if cached_video_count else "0.00%")
            self.high_like_video_count_label.setText(str(high_like_video_count))
            self.high_like_video_ratio_label.setText(f"{float(data.get('high_like_ratio') or 0):.2f}%")

            if total_followings:
                self.summary_label.setText(
                    f"当前关注博主总数：{total_followings} 位\n"
                    f"关注列表缓存时间：{data.get('followings_cached_at') or '暂无'}\n"
                    f"进度缓存条目：{data.get('progress_count') or 0} 条"
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

    def _on_stats_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if ok:
            try:
                _apply_douyin_stats_dialog(self, data or {})
            except Exception as exc:
                _show_douyin_stats_error(self, str(exc))
            return
        _show_douyin_stats_error(self, error)


class DouyinStatsDialogV2(QDialog):
    MODE_ROWS = []
    CREATOR_VIDEO_BUCKETS = []
    VIDEO_DURATION_BUCKETS = []

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
        if getattr(self, "stats_worker", None) and self.stats_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "刷新中...")
        self.close_button.setEnabled(False)
        self.summary_label.setText("正在后台读取抖音缓存统计...")
        self.stats_worker = ApiCallThread("douyin_stats", self.high_like_threshold, parent=self)
        self.stats_worker.completed.connect(self._on_stats_loaded)
        self.stats_worker.start()
        return
        _set_button_busy(self.refresh_button, "刷新中...")
        try:
            data = BackendApiClient().douyin_stats(self.high_like_threshold)
            total_followings = int(data.get("total_followings") or 0)
            for mode, _ in self.MODE_ROWS:
                mode_data = (data.get("modes") or {}).get(mode, {})
                captured_count = int(mode_data.get("count") or 0)
                percent = float(mode_data.get("percent") or 0)
                self.mode_count_labels[mode].setText(str(captured_count))
                self.mode_percent_labels[mode].setText(f"{percent:.2f}%")

            creator_bucket_counts = data.get("creator_buckets") or {}
            duration_bucket_counts = data.get("duration_buckets") or {}
            cached_video_count = int(data.get("cached_video_count") or 0)
            high_like_video_count = int(data.get("high_like_video_count") or 0)
            self.cached_video_count_label.setText(str(cached_video_count))
            self.cached_video_ratio_label.setText("100.00%" if cached_video_count else "0.00%")
            self.high_like_video_count_label.setText(str(high_like_video_count))
            self.high_like_video_ratio_label.setText(f"{float(data.get('high_like_ratio') or 0):.2f}%")

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
                    f"关注列表缓存时间：{data.get('followings_cached_at') or '暂无'}\n"
                    f"进度缓存条目：{data.get('progress_count') or 0} 条"
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

    def _on_stats_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if ok:
            try:
                _apply_douyin_stats_dialog(self, data or {})
            except Exception as exc:
                _show_douyin_stats_error(self, str(exc))
            return
        _show_douyin_stats_error(self, error)


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


def _show_douyin_stats_error(dialog, error):
    dialog.summary_label.setText(f"读取抖音统计失败：{error}")
    for mode, _ in dialog.MODE_ROWS:
        dialog.mode_count_labels[mode].setText("-")
        dialog.mode_percent_labels[mode].setText("-")
    dialog.cached_video_count_label.setText("-")
    dialog.cached_video_ratio_label.setText("-")
    dialog.high_like_video_count_label.setText("-")
    dialog.high_like_video_ratio_label.setText("-")
    if hasattr(dialog, "creator_bucket_count_labels"):
        for label, _, _ in dialog.CREATOR_VIDEO_BUCKETS:
            dialog.creator_bucket_count_labels[label].setText("-")
            dialog.creator_bucket_ratio_labels[label].setText("-")
    if hasattr(dialog, "duration_bucket_count_labels"):
        for label, _, _ in dialog.VIDEO_DURATION_BUCKETS:
            dialog.duration_bucket_count_labels[label].setText("-")
            dialog.duration_bucket_ratio_labels[label].setText("-")
    dialog.refresh_info_label.setText(
        f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _apply_douyin_stats_dialog(dialog, data):
    total_followings = int(data.get("total_followings") or 0)
    for mode, _ in dialog.MODE_ROWS:
        mode_data = (data.get("modes") or {}).get(mode, {})
        captured_count = int(mode_data.get("count") or 0)
        percent = float(mode_data.get("percent") or 0)
        dialog.mode_count_labels[mode].setText(str(captured_count))
        dialog.mode_percent_labels[mode].setText(f"{percent:.2f}%")

    cached_video_count = int(data.get("cached_video_count") or 0)
    high_like_video_count = int(data.get("high_like_video_count") or 0)
    dialog.cached_video_count_label.setText(str(cached_video_count))
    dialog.cached_video_ratio_label.setText("100.00%" if cached_video_count else "0.00%")
    dialog.high_like_video_count_label.setText(str(high_like_video_count))
    dialog.high_like_video_ratio_label.setText(f"{float(data.get('high_like_ratio') or 0):.2f}%")

    if hasattr(dialog, "creator_bucket_count_labels"):
        creator_bucket_counts = data.get("creator_buckets") or {}
        for label, _, _ in dialog.CREATOR_VIDEO_BUCKETS:
            bucket_count = creator_bucket_counts.get(label, 0)
            bucket_ratio = (bucket_count / total_followings * 100) if total_followings else 0
            dialog.creator_bucket_count_labels[label].setText(str(bucket_count))
            dialog.creator_bucket_ratio_labels[label].setText(f"{bucket_ratio:.2f}%")

    if hasattr(dialog, "duration_bucket_count_labels"):
        duration_bucket_counts = data.get("duration_buckets") or {}
        for label, _, _ in dialog.VIDEO_DURATION_BUCKETS:
            bucket_count = duration_bucket_counts.get(label, 0)
            bucket_ratio = (bucket_count / cached_video_count * 100) if cached_video_count else 0
            dialog.duration_bucket_count_labels[label].setText(str(bucket_count))
            dialog.duration_bucket_ratio_labels[label].setText(f"{bucket_ratio:.2f}%")

    if total_followings:
        dialog.summary_label.setText(
            f"当前关注博主总数：{total_followings} 位\n"
            f"关注列表缓存时间：{data.get('followings_cached_at') or '暂无'}\n"
            f"进度缓存条目：{data.get('progress_count') or 0} 条"
        )
    else:
        dialog.summary_label.setText(
            "当前没有可用的抖音关注缓存数据。\n请先运行一次基础统计模式，再查看统计信息。"
        )

    dialog.refresh_info_label.setText(
        f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


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
    GRADE_ORDER = ()
    CREATOR_TOP_COLUMNS = []
    CREATOR_LADDER_COLUMNS = []
    CREATOR_LOW_COLUMNS = []
    ARCHIVED_CREATOR_COLUMNS = []

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
        headers = ["对象", "总数", *self.GRADE_ORDER, "低/中置信度"]
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
        self.creator_top_table = self._make_table([label for label, _ in self.CREATOR_TOP_COLUMNS])
        self.creator_ladder_table = self._make_table([label for label, _ in self.CREATOR_LADDER_COLUMNS])
        self.creator_low_table = self._make_table([label for label, _ in self.CREATOR_LOW_COLUMNS])
        self.archived_creator_table = self._make_table([label for label, _ in self.ARCHIVED_CREATOR_COLUMNS])
        self.tabs.addTab(self.creator_top_table, "抖音排行表")
        self.tabs.addTab(self.creator_ladder_table, "天梯榜")
        self.tabs.addTab(self.creator_low_table, "低分/风险UP")
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
            if header in {"详情", "设为S级", "取消资格"}:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
            elif column == len(headers) - 1 or header in {"UP主主页链接", "视频链接", "视频标题", "归档原因"}:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
            else:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
        table.verticalHeader().setVisible(False)
        return table

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
                if header_text == "设为S级":
                    uid = str(value or "").strip()
                    button = QPushButton("设为S级")
                    button.setEnabled(bool(uid))
                    button.setProperty("uploader_id", uid)
                    button.clicked.connect(self._set_ladder_creator_s_from_button)
                    table.setCellWidget(row_index, column_index, button)
                    item = SortableTableWidgetItem("设为S级")
                    item.setData(SORT_ROLE, uid.lower())
                    table.setItem(row_index, column_index, item)
                    continue
                if header_text == "取消资格":
                    uid = str(value or "").strip()
                    button = QPushButton("取消资格")
                    button.setEnabled(bool(uid))
                    button.setProperty("uploader_id", uid)
                    button.clicked.connect(self._exclude_ladder_creator_from_button)
                    table.setCellWidget(row_index, column_index, button)
                    item = SortableTableWidgetItem("取消资格")
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
        uploader_id = str(uploader_id or "").strip()
        if not uploader_id:
            return
        if getattr(self, "detail_worker", None) and self.detail_worker.isRunning():
            return
        self.summary_label.setText("正在后台读取 UP 详情...")
        self.detail_worker = ApiCallThread("creator_detail", uploader_id, parent=self)
        self.detail_worker.completed.connect(self._on_creator_detail_loaded)
        self.detail_worker.start()
        return
        try:
            detail = BackendApiClient().creator_detail(str(uploader_id or "").strip())
        except Exception as exc:
            QMessageBox.warning(self, "读取详情失败", str(exc))
            return
        if not detail.get("creator"):
            QMessageBox.information(self, "没有详情", "未在当前评分快照中找到该 UP 主。")
            return
        CreatorDetailDialog(detail, self).exec_()

    def _on_creator_detail_loaded(self, ok, detail, error):
        if not ok:
            QMessageBox.warning(self, "读取详情失败", str(error))
            return
        if not detail.get("creator"):
            QMessageBox.information(self, "没有详情", "未在当前评分快照中找到该 UP 主。")
            return
        CreatorDetailDialog(detail, self).exec_()

    def _clear_tables(self):
        for table in (
            self.creator_top_table,
            self.creator_ladder_table,
            self.creator_low_table,
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
        search_uid = self._current_search_uid()
        if getattr(self, "overview_worker", None) and self.overview_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "读取中...")
        self.close_button.setEnabled(False)
        self.summary_label.setText("正在后台读取评分数据...")
        self.overview_worker = ApiCallThread("rating_overview", search_uid, parent=self)
        self.overview_worker.completed.connect(self._on_rating_data_loaded)
        self.overview_worker.start()
        return

        try:
            data = BackendApiClient().rating_overview(search_uid)
            if not data.get("exists", True):
                self.summary_label.setText(data.get("message") or "未找到评分数据库")
                self._clear_tables()
                return
            if not data.get("tables"):
                self.summary_label.setText(data.get("message") or "未找到评分表，请先运行抖音视频评分或 UP 主评分。")
                self._clear_tables()
                return

            summary = data.get("summary") or {}
            for key in ("creator", "video"):
                item = summary.get(key) or {}
                self._set_summary(key, item.get("total", 0), item.get("counts", {}), item.get("low_confidence", 0))

            tables = data.get("tables") or {}
            self._populate_table(self.creator_top_table, tables.get("creator_top") or [])
            self._populate_table(self.creator_ladder_table, tables.get("creator_ladder") or [])
            self._populate_table(self.creator_low_table, tables.get("creator_low") or [])
            self._populate_table(self.archived_creator_table, tables.get("archived_creator") or [])

            warning_parts = data.get("warning_parts") or []
            warning_text = f"\n范围：{'；'.join(warning_parts)}" if warning_parts else ""
            self.summary_label.setText(f"{data.get('message') or '评分数据已加载'}{warning_text}")
            self.refresh_info_label.setText(
                f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取评分数据失败：{exc}")
            self._clear_tables()

    def _on_rating_data_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if not ok:
            self.summary_label.setText(f"读取评分数据失败：{error}")
            self._clear_tables()
            return
        if not data.get("exists", True):
            self.summary_label.setText(data.get("message") or "未找到评分数据库")
            self._clear_tables()
            return
        if not data.get("tables"):
            self.summary_label.setText(data.get("message") or "未找到评分表，请先运行抖音视频评分或 UP 主评分。")
            self._clear_tables()
            return

        summary = data.get("summary") or {}
        for key in ("creator", "video"):
            item = summary.get(key) or {}
            self._set_summary(key, item.get("total", 0), item.get("counts", {}), item.get("low_confidence", 0))

        tables = data.get("tables") or {}
        self._populate_table(self.creator_top_table, tables.get("creator_top") or [])
        self._populate_table(self.creator_ladder_table, tables.get("creator_ladder") or [])
        self._populate_table(self.creator_low_table, tables.get("creator_low") or [])
        self._populate_table(self.archived_creator_table, tables.get("archived_creator") or [])

        warning_parts = data.get("warning_parts") or []
        warning_text = f"\n范围：{'；'.join(warning_parts)}" if warning_parts else ""
        self.summary_label.setText(f"{data.get('message') or '评分数据已加载'}{warning_text}")
        self.refresh_info_label.setText(
            f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _set_summary(self, key, total, counts, low_confidence):
        self.summary_cells[(key, "total")].setText(str(total))
        for grade in self.GRADE_ORDER:
            self.summary_cells[(key, grade)].setText(str(int((counts or {}).get(grade, 0))))
        self.summary_cells[(key, "low_confidence")].setText(str(low_confidence))

    def _set_ladder_creator_s_from_button(self):
        button = self.sender()
        uid = str(button.property("uploader_id") or "").strip() if button else ""
        if not uid:
            return
        if getattr(self, "action_worker", None) and self.action_worker.isRunning():
            return
        _set_button_busy(button, "处理中...")
        self.action_worker_button = button
        self.action_worker = ApiCallThread("save_creator_manual_grade", uid, "S", "从天梯榜设为S级", parent=self)
        self.action_worker.completed.connect(self._on_ladder_creator_s_done)
        self.action_worker.start()
        return
        try:
            BackendApiClient().save_creator_manual_grade(uid, "S", "从天梯榜设为S级")
        except Exception as exc:
            QMessageBox.warning(self, "设置失败", f"设为S级失败：{exc}")
            return
        self.refresh_data()
        self.tabs.setCurrentWidget(self.creator_ladder_table)

    def _on_ladder_creator_s_done(self, ok, _data, error):
        _restore_button_busy(getattr(self, "action_worker_button", None))
        if not ok:
            QMessageBox.warning(self, "设置失败", f"设为S级失败：{error}")
            return
        self.refresh_data()
        self.tabs.setCurrentWidget(self.creator_ladder_table)

    def _exclude_ladder_creator_from_button(self):
        button = self.sender()
        uid = str(button.property("uploader_id") or "").strip() if button else ""
        if not uid:
            return
        if getattr(self, "action_worker", None) and self.action_worker.isRunning():
            return
        _set_button_busy(button, "处理中...")
        self.action_worker_button = button
        self.action_worker = ApiCallThread("exclude_creator_from_ladder", uid, parent=self)
        self.action_worker.completed.connect(self._on_exclude_ladder_creator_done)
        self.action_worker.start()
        return
        try:
            BackendApiClient().exclude_creator_from_ladder(uid)
        except Exception as exc:
            QMessageBox.warning(self, "取消资格失败", f"取消资格失败：{exc}")
            return
        self.refresh_data()
        self.tabs.setCurrentWidget(self.creator_ladder_table)

    def _on_exclude_ladder_creator_done(self, ok, _data, error):
        _restore_button_busy(getattr(self, "action_worker_button", None))
        if not ok:
            QMessageBox.warning(self, "取消资格失败", f"取消资格失败：{error}")
            return
        self.refresh_data()
        self.tabs.setCurrentWidget(self.creator_ladder_table)


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
    FACTOR_COLUMNS = []

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
        if getattr(self, "manual_grade_worker", None) and self.manual_grade_worker.isRunning():
            return
        self._pending_manual_grade = (uploader_name, grade)
        self.manual_grade_worker = ApiCallThread(
            "save_creator_manual_grade",
            uploader_id,
            grade,
            note,
            parent=self,
        )
        self.manual_grade_worker.completed.connect(self._on_manual_grade_saved)
        self.manual_grade_worker.start()
        return
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
        BackendApiClient().save_creator_manual_grade(uploader_id, grade, note)

    def _on_manual_grade_saved(self, ok, _data, error):
        if not ok:
            QMessageBox.warning(self, "保存失败", f"手动等级保存失败：{error}")
            return
        uploader_name, grade = getattr(self, "_pending_manual_grade", ("", ""))
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

    def _open_homepage(self):
        url = str(self.creator.get("UP主主页链接") or "").strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))


class DouyinStatusResetDialog(QDialog):
    COLUMNS = []

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

    def refresh_data(self):
        if getattr(self, "status_worker", None) and self.status_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "读取中...")
        self.close_button.setEnabled(False)
        self.summary_label.setText("正在后台读取异常 full 状态列表...")
        self.status_worker = ApiCallThread("status_reset_candidates", self.threshold_spin.value(), parent=self)
        self.status_worker.completed.connect(self._on_status_reset_data_loaded)
        self.status_worker.start()
        return
        try:
            data = BackendApiClient().status_reset_candidates(self.threshold_spin.value())
            self.rows = data.get("rows") or []
            db_path = data.get("db_path") or ""
        except Exception as exc:
            self.summary_label.setText(f"读取异常 full 状态列表失败：{exc}")
            self.table.setRowCount(0)
            return
        self._populate_table(self.rows)
        self.summary_label.setText(
            f"数据库：{db_path}\n"
            f"候选：{len(self.rows)} 位；重置只会撤销 full 状态并置为过期，不删除视频缓存和评分数据。"
        )

    def _on_status_reset_data_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if not ok:
            self.summary_label.setText(f"读取异常 full 状态列表失败：{error}")
            self.table.setRowCount(0)
            return
        self.rows = (data or {}).get("rows") or []
        db_path = (data or {}).get("db_path") or ""
        self._populate_table(self.rows)
        self.summary_label.setText(
            f"数据库：{db_path}\n"
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
        if getattr(self, "reset_worker", None) and self.reset_worker.isRunning():
            return
        _set_button_busy(self.reset_selected_button, "重置中...")
        self.close_button.setEnabled(False)
        self.reset_worker = ApiCallThread("reset_full_status", uids, parent=self)
        self.reset_worker.completed.connect(self._on_reset_full_status_done)
        self.reset_worker.start()
        return
        try:
            count = int((BackendApiClient().reset_full_status(uids) or {}).get("count") or 0)
        except Exception as exc:
            QMessageBox.warning(self, "重置失败", str(exc))
            return
        QMessageBox.information(self, "重置完成", f"已重置 {count} 位 UP 的 full 状态。")
        self.refresh_data()

    def _on_reset_full_status_done(self, ok, data, error):
        _restore_button_busy(self.reset_selected_button)
        self.close_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "重置失败", str(error))
            return
        count = int((data or {}).get("count") or 0)
        QMessageBox.information(self, "重置完成", f"已重置 {count} 位 UP 的 full 状态。")
        self.refresh_data()

class DouyinArchiveDialog(QDialog):
    CANDIDATE_COLUMNS = []
    ARCHIVED_COLUMNS = []

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
        if getattr(self, "archive_worker", None) and self.archive_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "读取中...")
        self.close_button.setEnabled(False)
        self.summary_label.setText("正在后台读取归档数据...")
        self.archive_worker = ApiCallThread("archive_state", self.threshold_spin.value(), parent=self)
        self.archive_worker.completed.connect(self._on_archive_data_loaded)
        self.archive_worker.start()
        return
        _set_button_busy(self.refresh_button, "刷新中...")
        try:
            data = BackendApiClient().archive_state(self.threshold_spin.value())
            db_path = data.get("db_path") or ""
            self.candidates = data.get("candidates") or []
            self.archived_rows = data.get("archived_rows") or []
            self._populate_table(self.candidate_table, self.CANDIDATE_COLUMNS, self.candidates)
            self._populate_table(self.archived_table, self.ARCHIVED_COLUMNS, self.archived_rows)
            active_count = sum(1 for row in self.archived_rows if str(row.get("archive_status") or "") == "active")
            self.summary_label.setText(
                f"数据库：{db_path}\n"
                f"候选归档：{len(self.candidates)} 位；active 已归档：{active_count} 位；"
                "归档不会删除任何历史数据，后续主流程会跳过 active 归档对象。"
            )
        except Exception as exc:
            self.summary_label.setText(f"读取归档数据失败：{exc}")
            self.candidate_table.setRowCount(0)
            self.archived_table.setRowCount(0)
        finally:
            _restore_button_busy(self.refresh_button)

    def _on_archive_data_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if not ok:
            self.summary_label.setText(f"读取归档数据失败：{error}")
            self.candidate_table.setRowCount(0)
            self.archived_table.setRowCount(0)
            return
        db_path = (data or {}).get("db_path") or ""
        self.candidates = (data or {}).get("candidates") or []
        self.archived_rows = (data or {}).get("archived_rows") or []
        self._populate_table(self.candidate_table, self.CANDIDATE_COLUMNS, self.candidates)
        self._populate_table(self.archived_table, self.ARCHIVED_COLUMNS, self.archived_rows)
        active_count = sum(1 for row in self.archived_rows if str(row.get("archive_status") or "") == "active")
        self.summary_label.setText(
            f"数据库：{db_path}\n"
            f"候选归档：{len(self.candidates)} 位；active 已归档：{active_count} 位；归档不会删除历史数据。"
        )

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
        uids = [str(row.get("uploader_id") or "").strip() for row in rows]
        if getattr(self, "archive_action_worker", None) and self.archive_action_worker.isRunning():
            return
        _set_button_busy(self.archive_selected_button, "归档中...")
        _set_button_busy(self.archive_all_button, "归档中...")
        self.close_button.setEnabled(False)
        self.archive_action_worker = ApiCallThread(
            "archive_douyin_creators",
            uids=uids,
            threshold=self.threshold_spin.value(),
            parent=self,
        )
        self.archive_action_worker.completed.connect(self._on_archive_action_done)
        self.archive_action_worker.start()
        return
        count = int(
            (BackendApiClient().archive_douyin_creators(
                uids=uids,
                threshold=self.threshold_spin.value(),
            ) or {}).get("count") or 0
        )
        QMessageBox.information(self, "归档完成", f"已归档 {count} 位 UP。")
        self.refresh_data()

    def _on_archive_action_done(self, ok, data, error):
        _restore_button_busy(self.archive_selected_button)
        _restore_button_busy(self.archive_all_button)
        self.close_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "归档失败", str(error))
            return
        count = int((data or {}).get("count") or 0)
        QMessageBox.information(self, "归档完成", f"已归档 {count} 位 UP。")
        self.refresh_data()

    def restore_selected(self):
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
        if getattr(self, "restore_worker", None) and self.restore_worker.isRunning():
            return
        _set_button_busy(self.restore_selected_button, "恢复中...")
        self.close_button.setEnabled(False)
        self.restore_worker = ApiCallThread("restore_archived_creators", uids, parent=self)
        self.restore_worker.completed.connect(self._on_restore_archived_done)
        self.restore_worker.start()
        return
        count = int((BackendApiClient().restore_archived_creators(uids) or {}).get("count") or 0)
        QMessageBox.information(self, "恢复完成", f"已恢复 {count} 位 active 归档 UP。")
        self.refresh_data()

    def _on_restore_archived_done(self, ok, data, error):
        _restore_button_busy(self.restore_selected_button)
        self.close_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        count = int((data or {}).get("count") or 0)
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
    BILIBILI_MODE_OPTIONS = []
    DOUYIN_MODE_OPTIONS = []
    BROWSER_BACKEND_OPTIONS = []
    PLATFORM_OPTIONS = []
    ACTION_OPTIONS = []

    def __init__(self):
        super().__init__()
        self.worker = None
        self.cookie_checker = None
        self.liked_video_cache_worker = None
        self.log_dialog = None
        self.config_locked = False
        self.unfollow_list_path = str(DEFAULT_DOUYIN_UNFOLLOW_LIST)
        self.bilibili_uid_list_path = str(DEFAULT_BILIBILI_UID_LIST)
        self.douyin_uid_list_path = str(DEFAULT_DOUYIN_UID_LIST)
        try:
            _apply_gui_metadata(_load_backend_gui_metadata())
            self._gui_metadata_error = ""
        except Exception as exc:
            self._gui_metadata_error = str(exc)
        try:
            config_defaults = _load_backend_config_defaults()
            self.bilibili_runtime_settings = config_defaults["bilibili_runtime_settings"]
            self.douyin_runtime_settings = config_defaults["douyin_runtime_settings"]
            self.fetch_order_settings = config_defaults["fetch_order_settings"]
            self.douyin_full_fetch_retry_on_mismatch = bool(
                config_defaults.get("douyin_full_fetch_retry_on_mismatch", True)
            )
            self._config_defaults_error = ""
        except Exception as exc:
            self.bilibili_runtime_settings = {}
            self.douyin_runtime_settings = {}
            self.fetch_order_settings = _load_default_fetch_order_settings()
            self.douyin_full_fetch_retry_on_mismatch = True
            self._config_defaults_error = str(exc)
        self.auto_full_enabled = False
        self.auto_full_next_run_at = None
        self._loading_gui_config = False
        self.auto_full_timer = QTimer(self)
        self.auto_full_timer.timeout.connect(self._on_auto_full_timer)
        self._progress_current = 0
        self._progress_total = 0
        self._progress_running = False
        self.setWindowTitle("B站/抖音数据分析系统")
        self.resize(1320, 840)
        self.setMinimumSize(1180, 760)
        self._apply_readable_style()
        self._build_ui()
        self._load_gui_config()
        self._sync_visible_options()
        self._sync_auto_full_timer()
        if self._gui_metadata_error:
            self._append_log(f"读取后端 GUI 元数据失败：{self._gui_metadata_error}")
            self._show_warning_dialog(
                "GUI 元数据读取失败",
                "无法从 /api/gui/metadata 读取界面元数据。GUI 已尝试自动启动后端，请检查 runtime/logs/backend_gui_autostart.log。",
            )
        if self._config_defaults_error:
            self._append_log(f"读取后端默认配置失败：{self._config_defaults_error}")
            self._show_warning_dialog(
                "默认配置读取失败",
                "无法从 /api/config/defaults 读取默认配置。请先启动后端：python -m backend",
            )

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
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(14)
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
        for label, mode in self.DOUYIN_MODE_OPTIONS:
            self.douyin_mode_combo.addItem(label, mode)
        default_douyin_index = self.douyin_mode_combo.findData("monitor")
        self.douyin_mode_combo.setCurrentIndex(default_douyin_index if default_douyin_index >= 0 else 0)
        self.douyin_mode_combo.currentIndexChanged.connect(self._sync_visible_options)
        add_setting(1, "B站抓取模式", self.bilibili_mode_combo)

        self.monitor_video_limit_spin = QSpinBox()
        self.monitor_video_limit_spin.setRange(1, 500)
        self.monitor_video_limit_spin.setValue(10)
        self.monitor_video_limit_spin.setToolTip("监控/增量模式下，每位博主最多抓取最近 N 条视频。基础统计和完整模式会忽略该参数。")

        self.backend_combo = QComboBox()
        for label, value in self.BROWSER_BACKEND_OPTIONS:
            self.backend_combo.addItem(label, value)

        self.uid_fetch_mode_button = QPushButton()
        self.uid_fetch_mode_button.setCheckable(True)
        self.uid_fetch_mode_button.setChecked(False)
        self.uid_fetch_mode_button.setToolTip(
            "点击切换全抓取/部分抓取。部分抓取只处理排序后的前 N 个 UID。"
        )
        self.uid_fetch_mode_button.toggled.connect(self._on_uid_fetch_mode_toggled)
        self._sync_uid_fetch_mode_button()
        self.uid_limit_spin = QSpinBox()
        self.uid_limit_spin.setRange(1, 100000)
        self.uid_limit_spin.setValue(100)
        self.uid_limit_spin.setToolTip("部分抓取模式下生效；全抓取模式会忽略该数量。")

        self.high_like_spin = QSpinBox()
        self.high_like_spin.setRange(1, 100000000)
        self.high_like_spin.setValue(10000)
        self.high_like_spin.setToolTip("抖音统计与精简表导出中的高赞判定阈值。")
        self.full_fetch_retry_button = QPushButton()
        self.full_fetch_retry_button.setCheckable(True)
        self.full_fetch_retry_button.setChecked(bool(self.douyin_full_fetch_retry_on_mismatch))
        self.full_fetch_retry_button.setToolTip(
            "仅对抖音 full 模式生效。开启后，主页作品数与全量抓取结果不一致时，会自动重新进入主页再抓一次。"
        )
        self.full_fetch_retry_button.toggled.connect(self._on_full_fetch_retry_toggle)
        self._sync_full_fetch_retry_button()
        self.auto_full_button = QPushButton("自动 full：关闭")
        self.auto_full_button.setToolTip("开启后按左侧间隔自动运行一次抖音 full 模式；任务运行中会跳过当次触发。")
        self.auto_full_button.clicked.connect(self._toggle_auto_full_mode)
        self.auto_full_interval_spin = QSpinBox()
        self.auto_full_interval_spin.setRange(1, 10080)
        self.auto_full_interval_spin.setValue(DEFAULT_AUTO_FULL_INTERVAL_MINUTES)
        self.auto_full_interval_spin.setSuffix(" 分钟")
        self.auto_full_interval_spin.setToolTip("自动 full 的触发间隔。修改后会立即保存，并在已开启时重新计算下一次触发时间。")
        self.auto_full_interval_spin.valueChanged.connect(self._on_auto_full_interval_changed)
        layout.addWidget(settings_group)

        self.log_dialog = LogCenterDialog(self)
        self.log_text = self.log_dialog.log_text

        controls_group = QGroupBox("快捷操作")
        controls_group.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        controls_layout = QVBoxLayout(controls_group)
        controls_layout.setContentsMargins(16, 18, 16, 14)
        controls_layout.setSpacing(12)
        button_grid = QGridLayout()
        button_grid.setHorizontalSpacing(10)
        button_grid.setVerticalSpacing(8)
        for column in range(8):
            button_grid.setColumnStretch(column, 1)
        self.start_button = QPushButton("开始运行")
        self.start_button.setStyleSheet("font-weight: 700;")
        self.start_button.clicked.connect(self._start)
        self.stop_button = QPushButton("终止运行")
        self.stop_button.setEnabled(False)
        self.stop_button.clicked.connect(self._request_stop)
        self.video_download_button = QPushButton("视频下载")
        self.video_download_button.clicked.connect(self._open_video_downloader_gui)
        self.douyin_stats_button = QPushButton("抖音统计")
        self.douyin_stats_button.clicked.connect(self._open_douyin_stats)
        self.rating_overview_button = QPushButton("评分概览")
        self.rating_overview_button.clicked.connect(self._open_rating_overview)
        self.archive_button = QPushButton("归档管理")
        self.archive_button.clicked.connect(self._open_archive_manager)
        self.douyin_status_reset_button = QPushButton("状态重置")
        self.douyin_status_reset_button.clicked.connect(self._open_douyin_status_reset)
        self.liked_video_cache_button = QPushButton("\u7f13\u5b58\u559c\u6b22S\u7ea7")
        self.liked_video_cache_button.setToolTip("\u6293\u53d6\u5f53\u524d\u767b\u5f55\u8d26\u53f7\u4e3b\u9875\u559c\u6b22\u9875\u7684\u89c6\u9891\uff0c\u5199\u5165\u672c\u5730\u7f13\u5b58\u5e76\u8bbe\u4e3a S \u7ea7\u3002")
        self.liked_video_cache_button.clicked.connect(self._start_liked_video_cache)
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
            self.video_download_button,
            self.log_center_button,
            self.cookie_check_button,
            self.advanced_button,
            self.lock_button,
            self.clear_button,
        )
        douyin_quick_buttons = (
            self.douyin_stats_button,
            self.rating_overview_button,
            self.archive_button,
            self.douyin_status_reset_button,
            self.liked_video_cache_button,
            self.full_fetch_retry_button,
            self.auto_full_button,
            self.uid_fetch_mode_button,
        )
        for button in toolbar_buttons + douyin_quick_buttons:
            button.setMinimumWidth(0)
            button.setMaximumWidth(16777215)
            button.setMinimumHeight(28)
            button.setStyleSheet((button.styleSheet() + " " if button.styleSheet() else "") + "font-size: 13px; padding: 3px 8px;")
            button.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
        for index, button in enumerate(toolbar_buttons):
            button_grid.addWidget(button, index // 8, index % 8)
        controls_layout.addLayout(button_grid)

        douyin_quick_group = QGroupBox("抖音快捷操作")
        douyin_quick_layout = QVBoxLayout(douyin_quick_group)
        douyin_quick_layout.setContentsMargins(12, 18, 12, 12)
        douyin_quick_layout.setSpacing(10)
        douyin_settings_grid = QGridLayout()
        douyin_settings_grid.setHorizontalSpacing(12)
        douyin_settings_grid.setVerticalSpacing(8)
        douyin_settings_grid.setColumnMinimumWidth(0, 92)
        douyin_settings_grid.setColumnMinimumWidth(2, 116)
        douyin_settings_grid.setColumnStretch(1, 1)
        douyin_settings_grid.setColumnStretch(3, 1)

        def add_douyin_setting(row, left_label, left_widget, right_label=None, right_widget=None):
            douyin_settings_grid.addWidget(QLabel(left_label), row, 0)
            douyin_settings_grid.addWidget(left_widget, row, 1)
            if right_label is not None and right_widget is not None:
                douyin_settings_grid.addWidget(QLabel(right_label), row, 2)
                douyin_settings_grid.addWidget(right_widget, row, 3)

        add_douyin_setting(0, "抖音抓取模式", self.douyin_mode_combo, "抖音浏览器后端", self.backend_combo)
        metric_grid = QGridLayout()
        metric_grid.setHorizontalSpacing(10)
        metric_grid.setVerticalSpacing(0)
        metric_items = (
            ("监控视频数", self.monitor_video_limit_spin),
            ("UID 数量", self.uid_limit_spin),
            ("高赞阈值", self.high_like_spin),
            ("自动间隔", self.auto_full_interval_spin),
        )
        for column, (label_text, widget) in enumerate(metric_items):
            label = QLabel(label_text)
            label.setAlignment(Qt.AlignCenter)
            label.setMinimumHeight(28)
            widget.setMinimumWidth(0)
            widget.setMaximumWidth(16777215)
            widget.setMinimumHeight(28)
            widget.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)
            metric_grid.setColumnStretch(column * 2, 0)
            metric_grid.setColumnStretch(column * 2 + 1, 1)
            metric_grid.addWidget(label, 0, column * 2)
            metric_grid.addWidget(widget, 0, column * 2 + 1)
        douyin_settings_grid.addLayout(metric_grid, 1, 0, 1, 4)
        douyin_quick_layout.addLayout(douyin_settings_grid)

        douyin_button_grid = QGridLayout()
        douyin_button_grid.setHorizontalSpacing(10)
        douyin_button_grid.setVerticalSpacing(8)
        for column in range(8):
            douyin_button_grid.setColumnStretch(column, 1)
        for index, button in enumerate(douyin_quick_buttons):
            douyin_button_grid.addWidget(button, index // 8, index % 8)
        douyin_quick_layout.addLayout(douyin_button_grid)
        controls_layout.addWidget(douyin_quick_group)
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

    def _on_uid_fetch_mode_toggled(self, _checked):
        self._sync_uid_fetch_mode_button()
        self._sync_visible_options()

    def _sync_uid_fetch_mode_button(self):
        if not hasattr(self, "uid_fetch_mode_button"):
            return
        base_style = "font-size: 13px; padding: 3px 8px;"
        if self.uid_fetch_mode_button.isChecked():
            self.uid_fetch_mode_button.setText("部分抓取")
            self.uid_fetch_mode_button.setStyleSheet(f"{base_style} font-weight: 700; color: #1565c0;")
        else:
            self.uid_fetch_mode_button.setText("全抓取")
            self.uid_fetch_mode_button.setStyleSheet(base_style)

    def _sync_visible_options(self):
        platform = self.platform_combo.currentData()
        is_normal = platform in {"both", "bilibili", "douyin"}
        is_bilibili = platform in {"both", "bilibili"}
        is_douyin = platform in {"both", "douyin", "douyin_unfollow", "douyin_uid"}
        is_recent_video_mode = self.douyin_mode_combo.currentData() in {"monitor", "delta"}
        supports_full_fetch_retry = platform in {"both", "douyin"} and self.douyin_mode_combo.currentData() == "full"

        editable = not self.config_locked
        self.action_combo.setEnabled(editable and is_normal)
        self.bilibili_mode_combo.setEnabled(editable and is_bilibili)
        self.douyin_mode_combo.setEnabled(editable and is_douyin and platform != "douyin_unfollow")
        self.monitor_video_limit_spin.setEnabled(editable and is_douyin and is_recent_video_mode)
        self.backend_combo.setEnabled(editable and is_douyin)
        self.platform_combo.setEnabled(editable)
        self.uid_fetch_mode_button.setEnabled(editable)
        self.uid_limit_spin.setEnabled(editable and self.uid_fetch_mode_button.isChecked())
        self.high_like_spin.setEnabled(editable)
        self.full_fetch_retry_button.setEnabled(editable and supports_full_fetch_retry)
        self.auto_full_interval_spin.setEnabled(editable)
        self.liked_video_cache_button.setEnabled(editable)
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
            uid_limit_enabled=self.uid_fetch_mode_button.isChecked(),
            uid_limit=self.uid_limit_spin.value(),
            high_like_threshold=self.high_like_spin.value(),
            douyin_full_fetch_retry_on_mismatch=self.full_fetch_retry_button.isChecked(),
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
            "uid_limit_enabled": self.uid_fetch_mode_button.isChecked(),
            "uid_limit": self.uid_limit_spin.value(),
            "high_like_threshold": self.high_like_spin.value(),
            "douyin_full_fetch_retry_on_mismatch": self.full_fetch_retry_button.isChecked(),
            "unfollow_list_path": self.unfollow_list_path,
            "bilibili_uid_list_path": self.bilibili_uid_list_path,
            "douyin_uid_list_path": self.douyin_uid_list_path,
            "bilibili_runtime_settings": self.bilibili_runtime_settings,
            "douyin_runtime_settings": self.douyin_runtime_settings,
            "fetch_order_settings": self.fetch_order_settings,
            "auto_full_enabled": self.auto_full_enabled,
            "auto_full_interval_minutes": self.auto_full_interval_spin.value(),
        }

    def _save_gui_config(self):
        save_gui_config(self._snapshot_gui_config())

    def _load_gui_config(self):
        self._loading_gui_config = True
        try:
            data = load_gui_config()
        except Exception:
            self._loading_gui_config = False
            return
        if not data:
            self._loading_gui_config = False
            return

        try:
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

            self.uid_fetch_mode_button.setChecked(bool(data.get("uid_limit_enabled", False)))
            self._sync_uid_fetch_mode_button()
            self.uid_limit_spin.setValue(int(data.get("uid_limit", self.uid_limit_spin.value()) or self.uid_limit_spin.value()))
            self.monitor_video_limit_spin.setValue(
                int(data.get("monitor_video_limit", self.monitor_video_limit_spin.value()) or self.monitor_video_limit_spin.value())
            )
            self.high_like_spin.setValue(
                int(data.get("high_like_threshold", self.high_like_spin.value()) or self.high_like_spin.value())
            )
            if "douyin_full_fetch_retry_on_mismatch" in data:
                self.full_fetch_retry_button.setChecked(bool(data.get("douyin_full_fetch_retry_on_mismatch")))
            self.auto_full_interval_spin.setValue(
                int(data.get("auto_full_interval_minutes", self.auto_full_interval_spin.value()) or self.auto_full_interval_spin.value())
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
            self.auto_full_enabled = bool(data.get("auto_full_enabled", False))
        finally:
            self._loading_gui_config = False

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

    def _auto_full_interval_minutes(self):
        if not hasattr(self, "auto_full_interval_spin"):
            return DEFAULT_AUTO_FULL_INTERVAL_MINUTES
        return max(1, int(self.auto_full_interval_spin.value() or DEFAULT_AUTO_FULL_INTERVAL_MINUTES))

    def _auto_full_interval_ms(self):
        return self._auto_full_interval_minutes() * 60 * 1000

    def _auto_full_interval_text(self):
        minutes = self._auto_full_interval_minutes()
        if minutes % 60 == 0:
            hours = minutes // 60
            return f"{hours} 小时"
        if minutes > 60:
            hours = minutes // 60
            rest = minutes % 60
            return f"{hours} 小时 {rest} 分钟"
        return f"{minutes} 分钟"

    def _schedule_next_auto_full_run(self):
        self.auto_full_timer.setInterval(self._auto_full_interval_ms())
        self.auto_full_next_run_at = datetime.now().timestamp() + self._auto_full_interval_minutes() * 60
        if self.auto_full_timer.isActive():
            self.auto_full_timer.stop()
        if self.auto_full_enabled:
            self.auto_full_timer.start()

    def _on_auto_full_interval_changed(self, _value):
        if self._loading_gui_config:
            return
        if self.auto_full_enabled:
            self._schedule_next_auto_full_run()
            self._append_log(f"自动 full 间隔已改为 {self._auto_full_interval_text()}，下一次触发时间已重新计算。")
        self._save_gui_config()
        self._sync_auto_full_timer()

    def _on_full_fetch_retry_toggle(self, checked):
        self.douyin_full_fetch_retry_on_mismatch = bool(checked)
        self._sync_full_fetch_retry_button()
        if self._loading_gui_config:
            return
        self._save_gui_config()

    def _sync_full_fetch_retry_button(self):
        if not hasattr(self, "full_fetch_retry_button"):
            return
        if self.douyin_full_fetch_retry_on_mismatch:
            self.full_fetch_retry_button.setText("full校验：开启")
            self.full_fetch_retry_button.setStyleSheet("font-weight: 700; color: #1565c0;")
        else:
            self.full_fetch_retry_button.setText("full校验：关闭")
            self.full_fetch_retry_button.setStyleSheet("")

    def _toggle_auto_full_mode(self):
        self.auto_full_enabled = not self.auto_full_enabled
        if self.auto_full_enabled:
            self._schedule_next_auto_full_run()
            self._append_log(f"自动 full 模式已开启：每 {self._auto_full_interval_text()} 按当前界面参数运行一次。")
        else:
            self.auto_full_next_run_at = None
            self.auto_full_timer.stop()
            self._append_log("自动 full 模式已关闭。")
        self._save_gui_config()
        self._sync_auto_full_timer()

    def _sync_auto_full_timer(self):
        if not hasattr(self, "auto_full_button"):
            return
        if self.auto_full_enabled:
            self.auto_full_timer.setInterval(self._auto_full_interval_ms())
            if not self.auto_full_timer.isActive():
                self.auto_full_timer.start()
            if self.auto_full_next_run_at is None:
                self.auto_full_next_run_at = datetime.now().timestamp() + self._auto_full_interval_minutes() * 60
            next_time = datetime.fromtimestamp(self.auto_full_next_run_at).strftime("%H:%M")
            self.auto_full_button.setText(f"自动 full：开启（{next_time} / {self._auto_full_interval_minutes()}分）")
            self.auto_full_button.setStyleSheet("font-weight: 700; color: #1565c0;")
        else:
            self.auto_full_timer.stop()
            self.auto_full_button.setText("自动 full：关闭")
            self.auto_full_button.setStyleSheet("")

    def _on_auto_full_timer(self):
        if not self.auto_full_enabled:
            return
        self.auto_full_next_run_at = datetime.now().timestamp() + self._auto_full_interval_minutes() * 60
        self._sync_auto_full_timer()
        self._start_auto_full_run()

    def _start_auto_full_run(self):
        if self.worker and self.worker.isRunning():
            self._append_log("自动 full 触发时已有任务运行，本次跳过。")
            return

        config = self._collect_config()
        config.platform = "douyin"
        config.action = "fetch"
        config.douyin_fetch_mode = "full"
        if not self._validate_config(config):
            self._append_log("自动 full 触发失败：当前配置校验未通过。")
            return
        if self.config_locked:
            self._save_gui_config()

        self.log_text.clear()
        self._append_log("自动 full 定时触发：开始运行抖音完整模式。")
        self._start_task_progress("自动 full 已启动，正在等待抓取总数...")
        self.start_button.setEnabled(False)
        self.start_button.setText("运行中...")
        self.liked_video_cache_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("终止运行")
        self.stop_button.setStyleSheet("")
        self.worker = RunnerThread(config)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

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

    def _start_liked_video_cache(self):
        if self.worker and self.worker.isRunning():
            self._show_info_dialog("任务运行中", "当前任务还在运行，请等待完成。")
            return
        if self.liked_video_cache_worker and self.liked_video_cache_worker.isRunning():
            return
        if (
            QMessageBox.question(
                self,
                "\u786e\u8ba4\u7f13\u5b58\u559c\u6b22\u89c6\u9891",
                "\u5c06\u6253\u5f00\u6296\u97f3\u4e3b\u9875\u559c\u6b22\u9875\uff0c\u628a\u6293\u5230\u7684\u89c6\u9891\u5199\u5165\u672c\u5730\u7f13\u5b58\uff0c\u5e76\u7edf\u4e00\u8bbe\u7f6e\u4e3a S \u7ea7\u3002\u662f\u5426\u7ee7\u7eed\uff1f",
            )
            != QMessageBox.Yes
        ):
            return

        self.log_text.clear()
        self._append_log("\u5f00\u59cb\u7f13\u5b58\u6296\u97f3\u4e3b\u9875\u559c\u6b22\u89c6\u9891\uff0c\u6293\u5230\u7684\u89c6\u9891\u5c06\u7edf\u4e00\u8bbe\u4e3a S \u7ea7\u3002")
        self._start_task_progress("\u559c\u6b22\u89c6\u9891\u7f13\u5b58\u4e2d\uff0c\u6b63\u5728\u6253\u5f00\u6296\u97f3\u4e3b\u9875...")
        _set_button_busy(self.liked_video_cache_button, "\u7f13\u5b58\u4e2d...")
        self.liked_video_cache_worker = DouyinLikedVideoCacheThread()
        self.liked_video_cache_worker.log_line.connect(self._append_log)
        self.liked_video_cache_worker.done.connect(self._on_liked_video_cache_done)
        self.liked_video_cache_worker.start()

    def _on_liked_video_cache_done(self, ok, message):
        _restore_button_busy(self.liked_video_cache_button)
        self._append_log(message)
        self._finish_task_progress(ok, message)
        if ok:
            self._show_info_dialog("\u7f13\u5b58\u5b8c\u6210", message)
        else:
            self._show_error_dialog("\u7f13\u5b58\u5931\u8d25", message)

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
        if self.liked_video_cache_worker and self.liked_video_cache_worker.isRunning():
            self._show_info_dialog("\u7f13\u5b58\u8fd0\u884c\u4e2d", "\u559c\u6b22\u89c6\u9891\u7f13\u5b58\u8fd8\u5728\u8fd0\u884c\uff0c\u8bf7\u7b49\u5f85\u5b8c\u6210\u3002")
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
        self.liked_video_cache_button.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.stop_button.setText("终止运行")
        self.stop_button.setStyleSheet("")
        self.worker = RunnerThread(config)
        self.worker.log_line.connect(self._append_log)
        self.worker.done.connect(self._on_done)
        self.worker.start()

    def _video_downloader_launch_commands(self):
        return video_downloader_launch_commands()

    def _open_video_downloader_gui(self):
        ok, message = launch_video_downloader_gui()
        if ok:
            self._append_log(message)
        else:
            self._show_error_dialog("启动失败", message)

    def _request_stop(self):
        if not self.worker or not self.worker.isRunning():
            return
        self.worker.request_cancel()
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
        if self._gui_metadata_error:
            self._show_warning_dialog(
                "GUI 元数据未就绪",
                "界面字段、选项和表格列定义必须从后端 /api/gui/metadata 获取。GUI 已尝试自动启动后端，请检查 runtime/logs/backend_gui_autostart.log。",
            )
            return False
        if not self.PLATFORM_OPTIONS or not self.ACTION_OPTIONS or not self.DOUYIN_MODE_OPTIONS:
            self._show_warning_dialog(
                "GUI 元数据缺失",
                "后端未返回完整的界面元数据，无法安全启动任务。",
            )
            return False
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
        return extract_progress_total(text)

    def _extract_progress_current(self, text):
        return extract_progress_current(text)

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
        self.liked_video_cache_button.setEnabled(not self.config_locked)
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
