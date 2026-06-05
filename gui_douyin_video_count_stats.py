from __future__ import annotations

from datetime import datetime

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
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


class DouyinVideoCountStatsDialog(QDialog):
    DEFAULT_THRESHOLD = 1000

    def __init__(self, parent=None, threshold: int = DEFAULT_THRESHOLD):
        super().__init__(parent)
        self.setWindowTitle("数量统计")
        self.resize(860, 620)

        layout = QVBoxLayout(self)

        control_row = QHBoxLayout()
        control_row.addWidget(QLabel("显示视频数量大于"))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(0, 10_000_000)
        self.threshold_spin.setValue(max(int(threshold or self.DEFAULT_THRESHOLD), 0))
        self.threshold_spin.setSingleStep(100)
        control_row.addWidget(self.threshold_spin)
        control_row.addWidget(QLabel("的博主"))
        control_row.addStretch(1)
        self.query_button = QPushButton("查询")
        self.query_button.clicked.connect(self.refresh_data)
        control_row.addWidget(self.query_button)
        layout.addLayout(control_row)

        self.summary_label = QLabel("正在读取数量统计...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setStyleSheet("padding: 4px 2px; color: #444;")
        layout.addWidget(self.summary_label)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["博主名称", "视频数量", "完整模式", "博主等级", "转到主页"])
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
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
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
        self.summary_label.setText("正在后台读取数量统计...")
        self.stats_worker = ApiCallThread(
            "douyin_video_count_stats",
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
        self.summary_label.setText(f"读取数量统计失败：{error}")
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
            final_grade = str((row or {}).get("final_grade") or "无").strip() or "无"
            homepage_url = str((row or {}).get("homepage_url") or "").strip()

            name_item = SortableTableWidgetItem(uploader_name)
            name_item.setData(SORT_ROLE, uploader_name.lower())
            self.table.setItem(row_index, 0, name_item)

            count_item = SortableTableWidgetItem(str(video_count))
            count_item.setTextAlignment(Qt.AlignRight | Qt.AlignVCenter)
            count_item.setData(SORT_ROLE, video_count)
            self.table.setItem(row_index, 1, count_item)

            full_text = "是" if has_full_fetch else "否"
            full_item = SortableTableWidgetItem(full_text)
            full_item.setTextAlignment(Qt.AlignCenter)
            full_item.setData(SORT_ROLE, 1 if has_full_fetch else 0)
            self.table.setItem(row_index, 2, full_item)

            grade_item = SortableTableWidgetItem(final_grade)
            grade_item.setTextAlignment(Qt.AlignCenter)
            grade_order = {"S": 5, "A": 4, "B": 3, "C": 2, "D": 1, "无": 0}
            grade_item.setData(SORT_ROLE, grade_order.get(final_grade.upper(), grade_order.get(final_grade, 0)))
            self.table.setItem(row_index, 3, grade_item)

            button = QPushButton("转到主页")
            button.setEnabled(bool(homepage_url))
            button.setProperty("homepage_url", homepage_url)
            button.clicked.connect(self._open_homepage_from_button)
            self.table.setCellWidget(row_index, 4, button)
            link_item = SortableTableWidgetItem("转到主页")
            link_item.setData(SORT_ROLE, homepage_url.lower())
            link_item.setToolTip(homepage_url)
            self.table.setItem(row_index, 4, link_item)

        self.table.setSortingEnabled(True)
        self.table.sortItems(1, Qt.DescendingOrder)

        if total_followings:
            self.summary_label.setText(
                f"当前关注博主总数：{total_followings} 位\n"
                f"视频数量大于 {threshold} 的博主：{len(rows)} 位"
            )
        else:
            self.summary_label.setText(
                "当前没有可用的抖音关注缓存数据。\n请先运行一次基础统计模式，再查看数量统计。"
            )
        self.refresh_info_label.setText(
            f"最近刷新时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )

    def _open_homepage_from_button(self) -> None:
        button = self.sender()
        url = str(button.property("homepage_url") or "").strip() if button else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))
