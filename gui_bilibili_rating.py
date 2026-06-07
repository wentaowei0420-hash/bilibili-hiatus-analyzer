from __future__ import annotations

from PyQt5.QtCore import Qt, QUrl
from PyQt5.QtGui import QDesktopServices
from PyQt5.QtWidgets import (
    QComboBox,
    QDialog,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QTabWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from gui_backend_client import ApiCallThread


SORT_ROLE = Qt.UserRole + 99

GRADE_ORDER = ("S", "A", "B", "C", "D")
CURRENT_COLUMNS = [
    ("UP主", "uploader_name"),
    ("等级", "final_grade"),
    ("分数", "final_score"),
    ("置信度", "confidence"),
    ("粉丝数", "follower_count"),
    ("总播放", "total_view_count"),
    ("作品数(缓存)", "published_video_count"),
    ("详情", "uploader_id"),
    ("打开主页", "homepage_url"),
]
WATCH_COLUMNS = [
    ("UP主", "uploader_name"),
    ("等级", "final_grade"),
    ("分数", "final_score"),
    ("置信度", "confidence"),
    ("未更新天数", "inactive_days"),
    ("低等级比例", "low_grade_ratio"),
    ("详情", "uploader_id"),
    ("打开主页", "homepage_url"),
]
ARCHIVED_COLUMNS = [
    ("UP主", "uploader_name"),
    ("状态", "archive_status"),
    ("未更新天数", "inactive_days"),
    ("最后发布时间", "latest_publish_time"),
    ("等级", "final_grade"),
    ("归档时间", "archived_at"),
    ("归档原因", "archive_reason"),
    ("详情", "uploader_id"),
    ("打开主页", "homepage_url"),
]
DETAIL_VIDEO_COLUMNS = [
    ("视频标题", "video_title"),
    ("等级", "final_grade"),
    ("分数", "final_score"),
    ("播放", "view_count"),
    ("点赞", "like_count"),
    ("投币", "coin_count"),
    ("收藏", "favorite_count"),
    ("发布时间", "publish_date"),
]


class SortableTableWidgetItem(QTableWidgetItem):
    def __lt__(self, other):
        left = self.data(SORT_ROLE)
        right = other.data(SORT_ROLE) if other is not None else None
        if left is not None and right is not None:
            return left < right
        return super().__lt__(other)


class BilibiliRatingOverviewDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("B站评分概览")
        self.resize(1260, 800)
        self.setMinimumSize(1080, 700)
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
        self.detail_worker = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 14, 16, 14)
        layout.setSpacing(10)

        self.summary_label = QLabel("正在读取 B 站评分数据...")
        self.summary_label.setWordWrap(True)
        self.summary_label.setMinimumHeight(54)
        self.summary_label.setStyleSheet(
            "padding: 10px 12px; color: #263241; background: #f8fafc; "
            "border: 1px solid #d8dde6; border-radius: 8px;"
        )
        layout.addWidget(self.summary_label)

        summary_group = QGroupBox("等级分布")
        summary_grid = QGridLayout(summary_group)
        summary_grid.setHorizontalSpacing(28)
        headers = ["对象", "总数", *GRADE_ORDER, "低/中置信度"]
        for column, header in enumerate(headers):
            label = QLabel(header)
            label.setStyleSheet("font-weight: 700; font-size: 15px; color: #111827;")
            summary_grid.addWidget(label, 0, column)
        self.summary_cells = {}
        for row, key in enumerate(("creator", "video"), start=1):
            title = QLabel("UP主" if key == "creator" else "视频")
            title.setStyleSheet("font-weight: 700; font-size: 15px; color: #111827;")
            summary_grid.addWidget(title, row, 0)
            for column, name in enumerate(("total", *GRADE_ORDER, "low_confidence"), start=1):
                cell = QLabel("-")
                cell.setAlignment(Qt.AlignRight | Qt.AlignVCenter)
                cell.setMinimumWidth(72)
                cell.setStyleSheet("font-size: 16px; font-weight: 600; color: #111827;")
                summary_grid.addWidget(cell, row, column)
                self.summary_cells[(key, name)] = cell
        layout.addWidget(summary_group)

        search_group = QGroupBox("UP搜索")
        search_layout = QHBoxLayout(search_group)
        search_layout.addWidget(QLabel("UP主ID"))
        self.creator_search_input = QLineEdit()
        self.creator_search_input.setPlaceholderText("输入 UP 主 UID 后筛选单个 UP")
        self.creator_search_input.returnPressed.connect(self._search_creator)
        search_layout.addWidget(self.creator_search_input, stretch=1)
        self.creator_search_button = QPushButton("筛选UP")
        self.creator_search_button.clicked.connect(self._search_creator)
        self.clear_creator_search_button = QPushButton("清空筛选")
        self.clear_creator_search_button.clicked.connect(self._clear_creator_search)
        search_layout.addWidget(self.creator_search_button)
        search_layout.addWidget(self.clear_creator_search_button)
        layout.addWidget(search_group)

        self.current_table = self._make_table(CURRENT_COLUMNS)
        self.watch_table = self._make_table(WATCH_COLUMNS)
        self.archived_table = self._make_table(ARCHIVED_COLUMNS)

        self.tabs = QTabWidget()
        self.tabs.addTab(self.current_table, "B站排行榜")
        self.tabs.addTab(self.watch_table, "待观察UP")
        self.tabs.addTab(self.archived_table, "归档UP")
        layout.addWidget(self.tabs, stretch=1)

        self.refresh_info_label = QLabel("")
        self.refresh_info_label.setStyleSheet("padding: 4px 2px; color: #666; font-size: 13px;")
        layout.addWidget(self.refresh_info_label)

        button_row = QHBoxLayout()
        self.refresh_button = QPushButton("刷新")
        self.refresh_button.clicked.connect(self.refresh_scores)
        self.close_button = QPushButton("关闭")
        self.close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(self.refresh_button)
        button_row.addWidget(self.close_button)
        layout.addLayout(button_row)

        self.refresh_data()

    def _make_table(self, columns):
        table = QTableWidget()
        table.setColumnCount(len(columns))
        table.setHorizontalHeaderLabels([label for label, _key in columns])
        table.setAlternatingRowColors(True)
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setWordWrap(False)
        table.setShowGrid(True)
        table.setSortingEnabled(True)
        table.itemClicked.connect(self._open_link_item)
        table.verticalHeader().setDefaultSectionSize(42)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setMinimumHeight(42)
        for column, (label, _key) in enumerate(columns):
            if label in {"详情", "打开主页"}:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
            elif label in {"视频标题", "归档原因"}:
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
            return {"高": 3, "中": 2, "低": 1}.get(text, 0)
        if header_text in {"分数", "粉丝数", "总播放", "作品数(缓存)", "未更新天数", "低等级比例"}:
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
            QMessageBox.information(self, "请输入 UP 主ID", "请先输入要筛选的 UP 主 UID。")
            return
        self.refresh_data()

    def _clear_creator_search(self):
        if self._current_search_uid():
            self.creator_search_input.clear()
            self.refresh_data()

    def _populate_table(self, table, columns, rows):
        table.setSortingEnabled(False)
        table.setRowCount(len(rows))
        for row_index, row in enumerate(rows):
            for column_index, (header_text, key) in enumerate(columns):
                value = row.get(key)
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
                if header_text == "打开主页":
                    url = str(value or "").strip()
                    button = QPushButton("打开主页")
                    button.setEnabled(bool(url))
                    button.setProperty("homepage_url", url)
                    button.clicked.connect(self._open_creator_homepage_from_button)
                    table.setCellWidget(row_index, column_index, button)
                    item = SortableTableWidgetItem("打开主页")
                    item.setToolTip(url)
                    item.setData(SORT_ROLE, url.lower())
                    table.setItem(row_index, column_index, item)
                    continue
                text = self._fmt(value)
                item = SortableTableWidgetItem(text)
                item.setToolTip(text)
                item.setTextAlignment(Qt.AlignLeft | Qt.AlignVCenter)
                item.setData(SORT_ROLE, self._sort_value(header_text, value))
                table.setItem(row_index, column_index, item)
            table.setRowHeight(row_index, 42)
        table.setSortingEnabled(True)

    def _show_creator_detail_from_button(self):
        button = self.sender()
        uid = str(button.property("uploader_id") or "").strip() if button else ""
        if uid:
            self._show_creator_detail(uid)

    def _open_creator_homepage_from_button(self):
        button = self.sender()
        url = str(button.property("homepage_url") or "").strip() if button else ""
        if url:
            QDesktopServices.openUrl(QUrl(url))

    def _open_link_item(self, item):
        url = item.data(Qt.UserRole) if item else ""
        if url:
            QDesktopServices.openUrl(QUrl(str(url)))

    def _show_creator_detail(self, uploader_id):
        if self.detail_worker and self.detail_worker.isRunning():
            return
        self.summary_label.setText("正在后台读取 B 站 UP 详情...")
        self.detail_worker = ApiCallThread("bilibili_creator_detail", uploader_id, parent=self)
        self.detail_worker.completed.connect(self._on_creator_detail_loaded)
        self.detail_worker.start()

    def _on_creator_detail_loaded(self, ok, detail, error):
        if not ok:
            QMessageBox.warning(self, "读取详情失败", str(error))
            return
        if not detail.get("creator"):
            QMessageBox.information(self, "没有详情", "未在当前评分快照中找到该 UP 主。")
            return
        BilibiliCreatorDetailDialog(detail, self).exec_()

    def _clear_tables(self):
        for table in (self.current_table, self.watch_table, self.archived_table):
            table.setRowCount(0)

    def refresh_scores(self):
        if self.refresh_worker and self.refresh_worker.isRunning():
            return
        self.refresh_button.setEnabled(False)
        self.summary_label.setText("正在重新生成 B 站评分，请稍候...")
        from gui_backend_client import BilibiliRatingRefreshThread

        self.refresh_worker = BilibiliRatingRefreshThread(self)
        self.refresh_worker.done.connect(self._on_scores_refreshed)
        self.refresh_worker.start()

    def _on_scores_refreshed(self, ok, message):
        self.refresh_button.setEnabled(True)
        if not ok:
            QMessageBox.warning(self, "评分刷新失败", message)
            return
        self.refresh_data()

    def refresh_data(self):
        if getattr(self, "overview_worker", None) and self.overview_worker.isRunning():
            return
        self.summary_label.setText("正在后台读取 B 站评分数据...")
        self.overview_worker = ApiCallThread(
            "bilibili_rating_overview",
            self._current_search_uid(),
            parent=self,
        )
        self.overview_worker.completed.connect(self._on_rating_data_loaded)
        self.overview_worker.start()

    def _on_rating_data_loaded(self, ok, data, error):
        if not ok:
            self.summary_label.setText(f"读取评分数据失败：{error}")
            self._clear_tables()
            return
        if not data.get("ok"):
            self.summary_label.setText(data.get("message") or "未找到 B 站评分数据。")
            self._clear_tables()
            return

        summary = data.get("summary") or {}
        creator_summary = summary.get("creator") or {}
        video_summary = summary.get("video") or {}
        self._fill_summary_row("creator", creator_summary)
        self._fill_summary_row("video", video_summary)
        tables = data.get("tables") or {}
        self._populate_table(self.current_table, CURRENT_COLUMNS, tables.get("current") or [])
        self._populate_table(self.watch_table, WATCH_COLUMNS, tables.get("watch") or [])
        self._populate_table(self.archived_table, ARCHIVED_COLUMNS, tables.get("archived") or [])
        self.summary_label.setText(data.get("message") or "B站评分数据已加载")
        self.refresh_info_label.setText(f"最近刷新时间：{data.get('updated_at') or ''}")

    def _fill_summary_row(self, row_key, payload):
        counts = payload.get("counts") or {}
        self.summary_cells[(row_key, "total")].setText(str(payload.get("total") or 0))
        for grade in GRADE_ORDER:
            self.summary_cells[(row_key, grade)].setText(str(counts.get(grade) or 0))
        self.summary_cells[(row_key, "low_confidence")].setText(str(payload.get("low_confidence") or 0))


class BilibiliCreatorDetailDialog(QDialog):
    def __init__(self, detail, parent=None):
        super().__init__(parent)
        self.detail = detail or {}
        self.creator = self.detail.get("creator") or {}
        self.setWindowTitle(f"UP主详情 - {self.creator.get('uploader_name') or ''}")
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
            f"{self._fmt(self.creator.get('uploader_name'))}  |  "
            f"等级 {self._fmt(self.creator.get('final_grade'))}  |  "
            f"分数 {self._fmt(self.creator.get('final_score'))}  |  "
            f"置信度 {self._fmt(self.creator.get('confidence'))}"
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
        body_layout.addWidget(self._recent_videos_group())

        reason_group = QGroupBox("评分说明")
        reason_layout = QVBoxLayout(reason_group)
        reason = QLabel(
            f"评分来源：{self._fmt(self.creator.get('score_source'))}\n"
            f"评分原因：{self._fmt(self.creator.get('score_reasons'))}\n"
            f"缺失指标：{self._fmt(self.creator.get('missing_metrics'))}"
        )
        reason.setWordWrap(True)
        reason_layout.addWidget(reason)
        body_layout.addWidget(reason_group)

        scroll.setWidget(body)
        layout.addWidget(scroll, stretch=1)

        button_row = QHBoxLayout()
        grade_button = QPushButton("等级设置")
        grade_button.clicked.connect(self._set_manual_grade)
        homepage_button = QPushButton("打开主页")
        homepage_button.setEnabled(bool(str(self.creator.get("homepage_url") or "").strip()))
        homepage_button.clicked.connect(self._open_homepage)
        close_button = QPushButton("关闭")
        close_button.clicked.connect(self.accept)
        button_row.addStretch(1)
        button_row.addWidget(grade_button)
        button_row.addWidget(homepage_button)
        button_row.addWidget(close_button)
        layout.addLayout(button_row)

    def _basic_group(self):
        group = QGroupBox("基础信息")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        items = [
            ("粉丝数", self.creator.get("follower_count")),
            ("总播放", self.creator.get("total_view_count")),
            ("获赞总数", self.creator.get("total_like_count")),
            ("作品数", self.creator.get("published_video_count")),
            ("缓存视频数", self.creator.get("cached_video_count")),
            ("已评分视频数", self.creator.get("scored_video_count")),
            ("最近更新时间", self.creator.get("latest_publish_date")),
            ("未更新天数", self.creator.get("inactive_days")),
            ("平均几天一更", self.creator.get("avg_update_days")),
            ("最早视频时间", self.creator.get("earliest_publish_date")),
            ("创作跨度(天)", self.creator.get("creator_span_days")),
            ("低等级视频比例", f"{float(self.creator.get('low_grade_ratio') or 0) * 100:.2f}%"),
        ]
        for index, (label, value) in enumerate(items):
            row = index // 2
            col = (index % 2) * 2
            name = QLabel(label)
            name.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(self._fmt(value))
            grid.addWidget(name, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _factor_group(self):
        group = QGroupBox("各因子分数")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        labels = {
            "follower_score": "粉丝量分",
            "total_view_score": "总播放分",
            "total_like_score": "获赞总数分",
            "recent_update_score": "最近更新时间分",
            "update_frequency_score": "平均几天一更分",
            "video_count_score": "视频数量分",
            "history_span_score": "创作跨度分",
            "avg_view_score": "平均播放分",
            "avg_like_score": "平均点赞分",
            "avg_coin_score": "平均投币分",
            "avg_favorite_score": "平均收藏分",
            "video_grade_score": "视频等级分布分",
            "low_grade_ratio_score": "低等级比例分",
            "recent_trend_score": "最近10条趋势分",
            "risk_penalty": "风险扣分",
        }
        factor_rows = self.detail.get("factor_rows") or []
        for index, (key, value) in enumerate(factor_rows):
            row = index // 2
            col = (index % 2) * 2
            name = QLabel(labels.get(key, key))
            name.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(self._fmt(value))
            grid.addWidget(name, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _distribution_group(self, title, rows):
        group = QGroupBox(title)
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(26)
        grid.setVerticalSpacing(10)
        total = sum(count for _name, count in rows)
        if not rows:
            grid.addWidget(QLabel("暂无明细数据"), 0, 0)
            return group
        for index, (name, count) in enumerate(rows):
            percent = f"{(count / total * 100):.1f}%" if total else "0.0%"
            row = index // 2
            col = (index % 2) * 2
            name_label = QLabel(str(name))
            name_label.setStyleSheet("font-weight: 700; color: #374151;")
            value_label = QLabel(f"{count} ({percent})")
            grid.addWidget(name_label, row, col)
            grid.addWidget(value_label, row, col + 1)
        return group

    def _recent_videos_group(self):
        group = QGroupBox("最近视频表现")
        layout = QVBoxLayout(group)
        table = QTableWidget()
        rows = self.detail.get("recent_videos") or []
        table.setColumnCount(len(DETAIL_VIDEO_COLUMNS))
        table.setHorizontalHeaderLabels([label for label, _key in DETAIL_VIDEO_COLUMNS])
        table.setRowCount(len(rows))
        table.setEditTriggers(QTableWidget.NoEditTriggers)
        table.setSelectionBehavior(QTableWidget.SelectRows)
        table.setAlternatingRowColors(True)
        table.verticalHeader().setVisible(False)
        table.horizontalHeader().setMinimumHeight(38)
        for column, (label, key) in enumerate(DETAIL_VIDEO_COLUMNS):
            if label == "视频标题":
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.Stretch)
            else:
                table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeToContents)
            for row_index, row in enumerate(rows):
                item = QTableWidgetItem(self._fmt(row.get(key)))
                table.setItem(row_index, column, item)
        for row_index in range(len(rows)):
            table.setRowHeight(row_index, 38)
        layout.addWidget(table)
        return group

    def _set_manual_grade(self):
        uploader_id = str(self.creator.get("uploader_id") or "").strip()
        uploader_name = str(self.creator.get("uploader_name") or "").strip() or uploader_id
        if not uploader_id:
            QMessageBox.warning(self, "无法设置等级", "当前详情缺少 UP 主 UID。")
            return

        dialog = QDialog(self)
        dialog.setWindowTitle(f"等级设置 - {uploader_name}")
        dialog.resize(420, 180)
        layout = QVBoxLayout(dialog)
        hint = QLabel(
            "手动等级会写入本地 SQLite，并在重新评分后覆盖自动等级。\n"
            "选择“自动评分”会清除该 UP 的手动等级。"
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        form = QFormLayout()
        grade_combo = QComboBox()
        grade_combo.addItem("自动评分（清除手动等级）", "")
        for grade in GRADE_ORDER:
            grade_combo.addItem(grade, grade)
        current_grade = str(self.creator.get("manual_grade") or "").strip().upper()
        if current_grade in GRADE_ORDER:
            grade_combo.setCurrentIndex(GRADE_ORDER.index(current_grade) + 1)
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
        self.manual_grade_worker = ApiCallThread(
            "save_bilibili_creator_manual_grade",
            uploader_id,
            grade,
            note,
            parent=self,
        )
        self._pending_manual_grade = (uploader_name, grade)
        self.manual_grade_worker.completed.connect(self._on_manual_grade_saved)
        self.manual_grade_worker.start()

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
        url = str(self.creator.get("homepage_url") or "").strip()
        if url:
            QDesktopServices.openUrl(QUrl(url))
