from __future__ import annotations

from PyQt5.QtCore import Qt, QUrl, pyqtSignal
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QApplication,
    QDialog,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from gui_backend_client import ApiCallThread


SORT_ROLE = Qt.UserRole + 1
BUSY_BUTTON_STYLE = "font-weight: 700; color: #1565c0;"


def _set_button_busy(button, text="读取中..."):
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
        if left is None or right is None:
            return super().__lt__(other)
        return left < right


class BilibiliArchiveDialog(QDialog):
    CANDIDATE_COLUMNS = []
    ARCHIVED_COLUMNS = []
    opened = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("B站归档管理")
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
            "归档只记录本地状态，不删除缓存、CSV 或视频数据。active 归档对象会在后续 B站主流程中默认跳过。"
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
        filter_row.addWidget(QLabel("未更新天数 >= "))
        self.threshold_spin = QSpinBox()
        self.threshold_spin.setRange(1, 10000)
        self.threshold_spin.setValue(100)
        self.threshold_spin.setMaximumWidth(120)
        filter_row.addWidget(self.threshold_spin)
        filter_row.addWidget(QLabel("仅展示已有完整模式缓存、且未 active 归档的 UP。等级列预留给后续 B站评分体系。"))
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
        self.opened.emit()

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
        if header_text in {"未更新天数", "粉丝数", "总播放", "作品数", "缓存视频数"}:
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
        self.summary_label.setText("正在后台读取 B站归档数据...")
        self.archive_worker = ApiCallThread("bilibili_archive_state", self.threshold_spin.value(), parent=self)
        self.archive_worker.completed.connect(self._on_archive_data_loaded)
        self.archive_worker.start()

    def _on_archive_data_loaded(self, ok, data, error):
        _restore_button_busy(self.refresh_button)
        self.close_button.setEnabled(True)
        if not ok:
            self.summary_label.setText(f"读取 B站归档数据失败：{error}")
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
            "这只会写入本地 SQLite 归档表，不删除历史数据。",
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
            "archive_bilibili_creators",
            uids=uids,
            threshold=self.threshold_spin.value(),
            parent=self,
        )
        self.archive_action_worker.completed.connect(self._on_archive_action_done)
        self.archive_action_worker.start()

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
        self.restore_worker = ApiCallThread("restore_bilibili_archived_creators", uids, parent=self)
        self.restore_worker.completed.connect(self._on_restore_archived_done)
        self.restore_worker.start()

    def _on_restore_archived_done(self, ok, data, error):
        _restore_button_busy(self.restore_selected_button)
        self.close_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "恢复失败", str(error))
            return
        count = int((data or {}).get("count") or 0)
        QMessageBox.information(self, "恢复完成", f"已恢复 {count} 位 active 归档 UP。")
        self.refresh_data()
