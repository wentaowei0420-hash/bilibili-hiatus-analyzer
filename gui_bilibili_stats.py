from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gui_backend_client import ApiCallThread


SORT_ROLE = Qt.UserRole + 1
BUSY_BUTTON_STYLE = (
    "QPushButton { background-color: #2563eb; color: white; font-weight: 700; "
    "border: 1px solid #1d4ed8; border-radius: 4px; }"
    "QPushButton:disabled { background-color: #2563eb; color: white; font-weight: 700; "
    "border: 1px solid #1d4ed8; border-radius: 4px; }"
)
MODE_ROWS = (("basic", "基础模式"), ("full", "完整模式"))
CREATOR_VIDEO_BUCKETS = (
    ("0~50", 0, 50),
    ("51~300", 51, 300),
    ("301~500", 301, 500),
    ("501~1000", 501, 1000),
    ("1001以上", 1001, None),
)
VIDEO_DURATION_BUCKETS = (
    ("0~30s", 0, 30),
    ("31~60s", 31, 60),
    ("61~240s", 61, 240),
    ("241s以上", 241, None),
)


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if other is not None else None
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


def _set_button_busy(button, text: str) -> None:
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


def _restore_button_busy(button) -> None:
    if button is None or not button.property("_busy_state_saved"):
        return
    button.setText(button.property("_busy_old_text") or "")
    button.setStyleSheet(button.property("_busy_old_style") or "")
    button.setEnabled(bool(button.property("_busy_old_enabled")))
    button.setProperty("_busy_old_text", None)
    button.setProperty("_busy_old_style", None)
    button.setProperty("_busy_old_enabled", None)
    button.setProperty("_busy_state_saved", False)


class BilibiliVideoCountStatsDialog(QDialog):
    DEFAULT_THRESHOLD = 1000

    def __init__(self, parent=None, threshold: int = DEFAULT_THRESHOLD):
        super().__init__(parent)
        self.setWindowTitle("数量统计")
        self.resize(820, 600)

        layout = QVBoxLayout(self)

        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("显示视频数量大于"))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 10_000_000)
        self.threshold_spin.setValue(max(int(threshold or self.DEFAULT_THRESHOLD), 0))
        self.threshold_spin.setSingleStep(100)
        control_row.addWidget(self.threshold_spin)
        control_row.addWidget(QLabel("的 UP"))
        control_row.addStretch(1)
        self.query_button = QPushButton("查询")
        self.query_button.clicked.connect(self.refresh_data)
        control_row.addWidget(self.query_button)
        layout.addLayout(control_row)

        self.summary_label = QLabel("正在读取 B站数量统计...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 4px 2px; color: #444;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["UP主名称", "视频数量", "完整模式", "转到主页"])
        self.table.setAlternatingRowColors(True)
        self.table.setEditTriggers(QTableWidget.NoEditTriggers)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        self.table.setWordWrap(False)
        self.table.setShowGrid(True)
        self.table.setSortingEnabled(True)
        self.table.verticalHeader().setVisible(False)
        self.table.verticalHeader().setDefaultSectionSize(42)
        self.table.verticalHeader().setMinimumSectionSize(38)
        self.table.horizontalHeader().setMinimumHeight(42)
        self.table.horizontalHeader().setMinimumSectionSize(88)
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        layout.addWidget(self.table)

        self.refresh_info_label = QLabel("")
        self.refresh_info_label.setStyleSheet("padding: 2px 2px; color: #666;")
        layout.addWidget(self.refresh_info_label)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新数据")
        self.close_button = QPushButton("关闭")
        self.refresh_button.clicked.connect(self.refresh_data)
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_data()

    def refresh_data(self) -> None:
        if getattr(self, "stats_worker", None) and self.stats_worker.isRunning():
            return
        self.threshold_spin.setEnabled(False)
        self.query_button.setEnabled(False)
        self.close_button.setEnabled(False)
        _set_button_busy(self.refresh_button, "刷新中...")
        self.summary_label.setText("正在后台读取 B站数量统计...")
        self.stats_worker = ApiCallThread(
            "bilibili_video_count_stats",
            self.threshold_spin.value(),
            parent=self,
        )
        self.stats_worker.completed.connect(self._on_data_loaded)
        self.stats_worker.start()

    def _on_data_loaded(self, ok, data, error) -> None:
        _restore_button_busy(self.refresh_button)
        self.threshold_spin.setEnabled(True)
        self.query_button.setEnabled(True)
        self.close_button.setEnabled(True)
        if ok:
            self._apply_data(data or {})
            return
        self.table.setRowCount(0)
        self.summary_label.setText(f"读取 B站数量统计失败：{error}")
        self.refresh_info_label.setText(
            f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _apply_data(self, data: dict) -> None:
        threshold = int(data.get("threshold") or self.threshold_spin.value() or 0)
        self.threshold_spin.setValue(threshold)
        rows = data.get("rows") or []
        total_followings = int(data.get("total_followings") or 0)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            uploader_name = str((row or {}).get("uploader_name") or "")
            video_count = int((row or {}).get("published_video_count") or 0)
            has_full_fetch = bool((row or {}).get("has_full_fetch"))
            homepage_url = str((row or {}).get("homepage_url") or "").strip()

            name_item = SortableTableWidgetItem(uploader_name)
            name_item.setData(SORT_ROLE, uploader_name.lower())
            self.table.setItem(row_index, 0, name_item)

            count_item = SortableTableWidgetItem(str(video_count))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_item.setData(SORT_ROLE, video_count)
            self.table.setItem(row_index, 1, count_item)

            full_item = SortableTableWidgetItem("是" if has_full_fetch else "否")
            full_item.setTextAlignment(Qt.AlignCenter)
            full_item.setData(SORT_ROLE, 1 if has_full_fetch else 0)
            self.table.setItem(row_index, 2, full_item)

            button = QPushButton("转到主页")
            button.setEnabled(bool(homepage_url))
            button.setProperty("homepage_url", homepage_url)
            button.clicked.connect(self._open_homepage_from_button)
            self.table.setCellWidget(row_index, 3, button)
            link_item = SortableTableWidgetItem("转到主页")
            link_item.setData(SORT_ROLE, homepage_url.lower())
            link_item.setToolTip(homepage_url)
            self.table.setItem(row_index, 3, link_item)

        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.DescendingOrder)

        if total_followings:
            self.summary_label.setText(
                f"当前关注博主总数：{total_followings} 位\n"
                f"视频数量大于 {threshold} 的 UP：{len(rows)} 位"
            )
        else:
            self.summary_label.setText("当前没有可用的 B站缓存数据。\n请先运行一次基础模式，再查看数量统计。")
        self.refresh_info_label.setText(
            f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _open_homepage_from_button(self) -> None:
        button = self.sender()
        url = str(button.property("homepage_url") or "").strip() if button else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))


class BilibiliStatsDialog(QDialog):
    HIGH_LIKE_THRESHOLD = 10000

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("B站统计")
        self.resize(720, 560)

        layout = QVBoxLayout(self)

        self.summary_label = QLabel("正在读取 B站缓存统计...")
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
        for row_index, (mode, label) in enumerate(MODE_ROWS, start=1):
            title_label = QLabel(label)
            count_label = QLabel("-")
            percent_label = QLabel("-")
            count_label.setWordWrap(True)
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

        video_grid.addWidget(QLabel(f"高赞视频数量（>{self.HIGH_LIKE_THRESHOLD}）"), 2, 0)
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
        for row_index, (label, _lower, _upper) in enumerate(CREATOR_VIDEO_BUCKETS, start=1):
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
        for row_index, (label, _lower, _upper) in enumerate(VIDEO_DURATION_BUCKETS, start=1):
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
        self.count_stats_button = QPushButton("数量统计")
        self.refresh_button = QPushButton("刷新数据")
        self.close_button = QPushButton("关闭")
        self.count_stats_button.clicked.connect(self._open_video_count_stats)
        self.refresh_button.clicked.connect(self.refresh_stats)
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.count_stats_button)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_stats()

    def refresh_stats(self):
        if getattr(self, "stats_worker", None) and self.stats_worker.isRunning():
            return
        _set_button_busy(self.refresh_button, "刷新中...")
        self.close_button.setEnabled(False)
        self.summary_label.setText("正在后台读取 B站缓存统计...")
        self.stats_worker = ApiCallThread("bilibili_stats", self.HIGH_LIKE_THRESHOLD, parent=self)
        self.stats_worker.completed.connect(self._on_stats_loaded)
        self.stats_worker.start()

    def _on_stats_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if ok:
            _apply_bilibili_stats_dialog(self, data or {})
            return
        _show_bilibili_stats_error(self, error)

    def _open_video_count_stats(self):
        dialog = BilibiliVideoCountStatsDialog(self)
        dialog.exec_()


def _show_bilibili_stats_error(dialog, error):
    dialog.summary_label.setText(f"读取 B站统计失败：{error}")
    for mode, _label in MODE_ROWS:
        dialog.mode_count_labels[mode].setText("-")
        dialog.mode_percent_labels[mode].setText("-")
    dialog.cached_video_count_label.setText("-")
    dialog.cached_video_ratio_label.setText("-")
    dialog.high_like_video_count_label.setText("-")
    dialog.high_like_video_ratio_label.setText("-")
    for label, _lower, _upper in CREATOR_VIDEO_BUCKETS:
        dialog.creator_bucket_count_labels[label].setText("-")
        dialog.creator_bucket_ratio_labels[label].setText("-")
    for label, _lower, _upper in VIDEO_DURATION_BUCKETS:
        dialog.duration_bucket_count_labels[label].setText("-")
        dialog.duration_bucket_ratio_labels[label].setText("-")
    dialog.refresh_info_label.setText(
        f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )


def _format_bilibili_mode_count_text(mode, captured_count, mode_data):
    if mode != "full":
        return str(captured_count)
    valid_count = int(mode_data.get("valid_count") or 0)
    expired_count = int(mode_data.get("expired_count") or 0)
    unfetched_count = int(mode_data.get("unfetched_count") or 0)
    return (
        f"{captured_count}<br>"
        "<span style='color:#666; font-size:11px;'>"
        f"有效 {valid_count} / 过期 {expired_count} / 未抓取 {unfetched_count}"
        "</span>"
    )


def _apply_bilibili_stats_dialog(dialog, data):
    total_followings = int(data.get("total_followings") or 0)
    for mode, _label in MODE_ROWS:
        mode_data = (data.get("modes") or {}).get(mode, {})
        captured_count = int(mode_data.get("count") or 0)
        percent = float(mode_data.get("percent") or 0)
        dialog.mode_count_labels[mode].setText(
            _format_bilibili_mode_count_text(mode, captured_count, mode_data)
        )
        dialog.mode_percent_labels[mode].setText(f"{percent:.2f}%")

    cached_video_count = int(data.get("cached_video_count") or 0)
    high_like_video_count = int(data.get("high_like_video_count") or 0)
    dialog.cached_video_count_label.setText(str(cached_video_count))
    dialog.cached_video_ratio_label.setText("100.00%" if cached_video_count else "0.00%")
    dialog.high_like_video_count_label.setText(str(high_like_video_count))
    dialog.high_like_video_ratio_label.setText(f"{float(data.get('high_like_ratio') or 0):.2f}%")

    creator_bucket_counts = data.get("creator_buckets") or {}
    for label, _lower, _upper in CREATOR_VIDEO_BUCKETS:
        bucket_count = int(creator_bucket_counts.get(label) or 0)
        bucket_ratio = (bucket_count / total_followings * 100) if total_followings else 0
        dialog.creator_bucket_count_labels[label].setText(str(bucket_count))
        dialog.creator_bucket_ratio_labels[label].setText(f"{bucket_ratio:.2f}%")

    duration_bucket_counts = data.get("duration_buckets") or {}
    for label, _lower, _upper in VIDEO_DURATION_BUCKETS:
        bucket_count = int(duration_bucket_counts.get(label) or 0)
        bucket_ratio = (bucket_count / cached_video_count * 100) if cached_video_count else 0
        dialog.duration_bucket_count_labels[label].setText(str(bucket_count))
        dialog.duration_bucket_ratio_labels[label].setText(f"{bucket_ratio:.2f}%")

    if total_followings:
        dialog.summary_label.setText(
            f"当前关注博主总数：{total_followings} 位\n"
            f"基础模式缓存时间：{data.get('precise_cached_at') or '暂无'}\n"
            f"进度缓存条目：{data.get('progress_count') or 0} 条\n"
            f"完整模式缓存时间：{data.get('full_cached_at') or '暂无'}\n"
            f"完整模式明细：有效博主 {int(((data.get('modes') or {}).get('full') or {}).get('valid_count') or 0)} 位，"
            f"数据过期 {int(((data.get('modes') or {}).get('full') or {}).get('expired_count') or 0)} 位，"
            f"未抓取博主 {int(((data.get('modes') or {}).get('full') or {}).get('unfetched_count') or 0)} 位"
        )
    else:
        dialog.summary_label.setText("当前没有可用的 B站缓存数据。\n请先运行一次基础模式，再查看统计信息。")

    dialog.refresh_info_label.setText(
        f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    )
