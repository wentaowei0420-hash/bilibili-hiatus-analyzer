import asyncio
import queue
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
import tkinter as tk
from tkinter.scrolledtext import ScrolledText
from typing import Any

from auth import CookieManager
from cli.main import (
    HIGH_LIKE_CSV,
    HIGH_LIKE_FAILED_CSV,
    _append_failed_high_like_rows,
    _load_high_like_video_rows,
    download_url,
)
from config import ConfigLoader
from core.downloader_base import _FilenameTemplateValues, _normalize_filename_template
from storage import Database
from utils.logger import set_console_log_level
from utils.validators import sanitize_filename


PROJECT_ROOT = Path(__file__).resolve().parent


class HighLikeDownloaderGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("抖音高赞视频下载")
        self.root.geometry("920x650")
        self.root.minsize(860, 620)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_requested = threading.Event()
        self.active_csv_path = ""
        self.active_failed_csv_path = ""
        self.active_config_path = ""
        self.active_download_path = ""
        self.active_batch_count = 1
        self.active_skip_failed = False
        self.active_filename_template = ""
        self.active_filter_mode = "全部CSV"
        self.active_filter_grade = "A"
        self.active_like_threshold = 10000
        self.preview_after_id = None

        self.csv_path = tk.StringVar(value=str(PROJECT_ROOT / HIGH_LIKE_CSV))
        self.failed_csv_path = tk.StringVar(value=str(PROJECT_ROOT / HIGH_LIKE_FAILED_CSV))
        self.config_path = tk.StringVar(value=str(PROJECT_ROOT / "config.yml"))
        self.download_path = tk.StringVar(
            value=self._load_config_download_path(str(PROJECT_ROOT / "config.yml"))
        )
        self.batch_count = tk.IntVar(value=20)
        self.skip_failed_records = tk.BooleanVar(value=False)
        self.saved_filename_template = self._load_config_filename_template(str(PROJECT_ROOT / "config.yml"))
        self.filename_template = tk.StringVar(value=self.saved_filename_template)
        self.download_filter_mode = tk.StringVar(value="全部CSV")
        self.download_filter_grade = tk.StringVar(value="A")
        self.download_like_threshold = tk.IntVar(value=10000)

        self.total_count = tk.StringVar(value="0")
        self.filtered_count = tk.StringVar(value="0")
        self.completed_count = tk.StringVar(value="0")
        self.pending_count = tk.StringVar(value="0")
        self.failed_skipped_count = tk.StringVar(value="0")
        self.current_batch_count = tk.StringVar(value="0")
        self.downloading_count = tk.StringVar(value="0")
        self.success_count = tk.StringVar(value="0")
        self.failed_count = tk.StringVar(value="0")
        self.skipped_count = tk.StringVar(value="0")
        self.status = tk.StringVar(value="请选择 CSV 后点击刷新统计")
        self.filename_preview = tk.StringVar(value="命名示例：请选择 CSV")

        self._build_ui()
        self.filename_template.trace_add("write", self._mark_filename_template_dirty)
        self.csv_path.trace_add("write", self._schedule_filename_preview_update)
        self.root.after(0, self._update_filename_preview)
        self.root.after(150, self._drain_events)

    def _build_ui(self):
        self._configure_styles()
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        paths = ttk.LabelFrame(outer, text="文件设置", padding=10)
        paths.pack(fill=tk.X)

        self._path_row(paths, "视频 CSV", self.csv_path, self._choose_csv, 0)
        self._path_row(paths, "失败 CSV", self.failed_csv_path, self._choose_failed_csv, 1)
        self._path_row(paths, "配置文件", self.config_path, self._choose_config, 2)
        self._path_row(paths, "下载目录", self.download_path, self._choose_download_dir, 3)

        controls = ttk.Frame(outer)
        controls.pack(fill=tk.X, pady=(10, 0))

        ttk.Label(controls, text="单次下载数量").pack(side=tk.LEFT)
        ttk.Spinbox(
            controls,
            from_=1,
            to=10000,
            textvariable=self.batch_count,
            width=8,
        ).pack(side=tk.LEFT, padx=(8, 18))

        self.skip_failed_button = ttk.Checkbutton(
            controls,
            text="跳过失败记录",
            variable=self.skip_failed_records,
        )
        self.skip_failed_button.pack(side=tk.LEFT, padx=(0, 18))

        self.refresh_button = ttk.Button(controls, text="刷新统计", command=self.refresh_stats)
        self.refresh_button.pack(side=tk.LEFT, padx=(0, 8))
        self.start_button = ttk.Button(controls, text="开始下载", command=self.start_download)
        self.start_button.pack(side=tk.LEFT, padx=(0, 8))
        self.stop_button = ttk.Button(
            controls,
            text="停止",
            command=self.request_stop,
            state=tk.DISABLED,
        )
        self.stop_button.pack(side=tk.LEFT, padx=(0, 8))
        self.reset_button = ttk.Button(
            controls,
            text="一键重置",
            command=self.reset_download_records,
        )
        self.reset_button.pack(side=tk.LEFT)

        filter_frame = ttk.LabelFrame(outer, text="下载筛选", padding=10)
        filter_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(filter_frame, text="下载范围").pack(side=tk.LEFT)
        self.filter_mode_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.download_filter_mode,
            values=("全部CSV", "高赞视频", "指定等级"),
            state="readonly",
            width=10,
        )
        self.filter_mode_combo.pack(side=tk.LEFT, padx=(8, 18))
        self.filter_mode_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)

        ttk.Label(filter_frame, text="高赞阈值 ≥").pack(side=tk.LEFT)
        self.like_threshold_spin = ttk.Spinbox(
            filter_frame,
            from_=0,
            to=100000000,
            textvariable=self.download_like_threshold,
            width=10,
            command=self._on_filter_changed,
        )
        self.like_threshold_spin.pack(side=tk.LEFT, padx=(8, 18))

        ttk.Label(filter_frame, text="视频等级").pack(side=tk.LEFT)
        self.grade_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.download_filter_grade,
            values=("S", "A", "B", "C", "D"),
            state="readonly",
            width=6,
        )
        self.grade_combo.pack(side=tk.LEFT, padx=(8, 18))
        self.grade_combo.bind("<<ComboboxSelected>>", self._on_filter_changed)
        ttk.Label(
            filter_frame,
            text="提示：选择“指定等级”后可单独下载 A 级等视频。",
            foreground="#4b5563",
        ).pack(side=tk.LEFT)

        filename_frame = ttk.Frame(outer)
        filename_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(filename_frame, text="视频命名格式").pack(side=tk.LEFT)
        ttk.Entry(filename_frame, textvariable=self.filename_template).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=(8, 8),
        )
        self.save_filename_button = ttk.Button(
            filename_frame,
            text="保存命名设置",
            command=self.save_filename_template,
        )
        self.save_filename_button.pack(side=tk.LEFT, padx=(0, 8))
        available_fields = (
            "可用字段：等级/视频等级、UP主/UP主姓名/作者、视频标题/标题、点赞数、"
            "发布时间/发表时间/发布日期、视频ID/作品ID；英文：{grade} {video_grade} {final_grade} {level} "
            "{author} {author_name} {author_id} {uid} {title} {desc} {like_count} "
            "{digg_count} {comment_count} {share_count} {collect_count} {date} "
            "{create_time} {aweme_id}。说明：视频ID会始终保留用于本地去重；写入模板可控制位置，不写则自动追加到末尾。"
        )
        ttk.Label(
            filename_frame,
            text=available_fields,
            wraplength=520,
            justify=tk.LEFT,
        ).pack(side=tk.LEFT)

        preview_frame = ttk.Frame(outer)
        preview_frame.pack(fill=tk.X, pady=(4, 0))
        ttk.Label(preview_frame, text="命名效果").pack(side=tk.LEFT)
        ttk.Label(
            preview_frame,
            textvariable=self.filename_preview,
            foreground="#4b5563",
            wraplength=720,
        ).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(8, 0))

        stats = ttk.LabelFrame(outer, text="下载统计", padding=10)
        stats.pack(fill=tk.X, pady=(10, 0))

        items = [
            ("CSV 视频总数", self.total_count),
            ("筛选后视频数量", self.filtered_count),
            ("已完成下载视频数量", self.completed_count),
            ("待下载视频数量", self.pending_count),
            ("失败跳过", self.failed_skipped_count),
            ("本次下载数量", self.current_batch_count),
            ("下载中数量", self.downloading_count),
            ("本次成功", self.success_count),
            ("本次失败", self.failed_count),
            ("本次跳过", self.skipped_count),
        ]
        for index, (label, value) in enumerate(items):
            row = index // 4
            col = (index % 4) * 2
            ttk.Label(stats, text=label).grid(row=row, column=col, sticky=tk.W, padx=(0, 8), pady=4)
            ttk.Label(stats, textvariable=value, font=("", 11, "bold")).grid(
                row=row,
                column=col + 1,
                sticky=tk.W,
                padx=(0, 24),
                pady=4,
            )

        progress_frame = ttk.Frame(outer)
        progress_frame.pack(fill=tk.X, pady=(10, 0))
        self.progress = ttk.Progressbar(progress_frame, mode="determinate")
        self.progress.pack(fill=tk.X)
        ttk.Label(progress_frame, textvariable=self.status).pack(anchor=tk.W, pady=(4, 0))

        log_frame = ttk.LabelFrame(outer, text="运行日志", padding=8)
        log_frame.pack(fill=tk.BOTH, expand=True, pady=(10, 0))
        self.log_text = ScrolledText(log_frame, height=12, wrap=tk.WORD)
        self.log_text.pack(fill=tk.BOTH, expand=True)
        self.log_text.configure(state=tk.DISABLED)

    def _configure_styles(self):
        style = ttk.Style(self.root)
        style.configure("Busy.TButton", foreground="white", background="#2563eb")
        style.map(
            "Busy.TButton",
            foreground=[("active", "white"), ("!disabled", "white")],
            background=[("active", "#1d4ed8"), ("!disabled", "#2563eb")],
        )

    def _path_row(self, parent, label: str, variable: tk.StringVar, command, row: int):
        ttk.Label(parent, text=label).grid(row=row, column=0, sticky=tk.W, pady=4)
        ttk.Entry(parent, textvariable=variable).grid(
            row=row,
            column=1,
            sticky=tk.EW,
            padx=8,
            pady=4,
        )
        ttk.Button(parent, text="选择", command=command).grid(row=row, column=2, pady=4)
        parent.columnconfigure(1, weight=1)

    def _load_config_download_path(self, config_path: str) -> str:
        try:
            config = ConfigLoader(config_path)
            configured_path = config.get("path", "./Downloaded/")
        except Exception:
            configured_path = "./Downloaded/"
        return str(self._resolve_download_path(configured_path))

    def _load_config_filename_template(self, config_path: str) -> str:
        try:
            config = ConfigLoader(config_path)
            template = str(config.get("filename_template", "") or "").strip()
            if template:
                return template
        except Exception:
            pass
        return "等级_UP主_视频标题_点赞数"

    @staticmethod
    def _resolve_download_path(path_value: str) -> Path:
        path = Path(str(path_value or "./Downloaded/")).expanduser()
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    def _choose_csv(self):
        path = filedialog.askopenfilename(
            title="选择视频 CSV",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.csv_path.set(path)

    def _choose_failed_csv(self):
        path = filedialog.asksaveasfilename(
            title="选择失败 CSV",
            defaultextension=".csv",
            filetypes=[("CSV 文件", "*.csv"), ("所有文件", "*.*")],
        )
        if path:
            self.failed_csv_path.set(path)

    def _choose_config(self):
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("YAML 文件", "*.yml *.yaml"), ("所有文件", "*.*")],
        )
        if path:
            self.config_path.set(path)
            self.download_path.set(self._load_config_download_path(path))
            self.saved_filename_template = self._load_config_filename_template(path)
            self.filename_template.set(self.saved_filename_template)
            self._schedule_filename_preview_update()

    def _choose_download_dir(self):
        current = self._resolve_download_path(self.download_path.get())
        initial_dir = current if current.exists() else current.parent
        path = filedialog.askdirectory(
            title="选择下载输出目录",
            initialdir=str(initial_dir if initial_dir.exists() else PROJECT_ROOT),
        )
        if path:
            self.download_path.set(path)

    def _schedule_filename_preview_update(self, *_args):
        if self.preview_after_id is not None:
            try:
                self.root.after_cancel(self.preview_after_id)
            except tk.TclError:
                pass
        self.preview_after_id = self.root.after(250, self._update_filename_preview)

    def _mark_filename_template_dirty(self, *_args):
        if self.filename_template.get().strip() != self.saved_filename_template:
            self.status.set("命名格式已修改，点击“保存命名设置”后生效")

    def save_filename_template(self):
        template = self.filename_template.get().strip() or "等级_UP主_视频标题_点赞数"
        config_path = self.config_path.get().strip() or str(PROJECT_ROOT / "config.yml")
        try:
            config = ConfigLoader(config_path)
            config.update(filename_template=template)
            config.save(config_path)
        except Exception as exc:
            messagebox.showerror("保存失败", f"命名设置保存失败：{exc}")
            return

        self.saved_filename_template = template
        self.filename_template.set(template)
        self.status.set("命名设置已保存，后续将自动复用")
        self._log(f"命名设置已保存：{template}")
        self._update_filename_preview()

    def _update_filename_preview(self):
        self.preview_after_id = None
        try:
            self.filename_preview.set(self._build_filename_preview())
        except Exception as exc:
            self.filename_preview.set(f"命名示例生成失败：{exc}")

    def _build_filename_preview(self) -> str:
        config = ConfigLoader(self.config_path.get())
        db_path = self._resolve_database_path(config)
        row = {}
        csv_first_row = None
        csv_path = self.csv_path.get().strip()
        if csv_path:
            try:
                rows = _load_high_like_video_rows(csv_path)
                csv_first_row = rows[0] if rows else None
            except Exception:
                csv_first_row = None

        if csv_first_row:
            aweme_id = str(csv_first_row.get("aweme_id") or csv_first_row.get("视频ID") or "").strip()
            row = self._lookup_video_context(db_path, aweme_id) or {}
            row = {**csv_first_row, **row} if row else csv_first_row
        else:
            row = self._lookup_video_context(db_path, "") or {}

        if not row:
            return "命名示例：SQLite 暂无视频评分数据，请先生成视频评分或选择 CSV"

        try:
            row = self._filename_context_for_row(row, config)
        except Exception:
            row = self._filename_context_for_row(row)
        aweme_id = str(row.get("aweme_id") or "").strip()
        template = self.saved_filename_template or "等级_UP主_视频标题_点赞数"
        values = {
            "date": row.get("date") or row.get("发布时间") or "",
            "title": row.get("video_title") or "",
            "desc": row.get("video_title") or "",
            "aweme_id": aweme_id,
            "author": row.get("uploader_name") or "",
            "author_name": row.get("uploader_name") or "",
            "like_count": row.get("like_count") or "",
            "digg_count": row.get("like_count") or "",
            "grade": row.get("video_grade") or "",
            "level": row.get("video_grade") or "",
            "video_grade": row.get("video_grade") or "",
            "final_grade": row.get("video_grade") or "",
            "等级": row.get("video_grade") or "",
            "视频等级": row.get("video_grade") or "",
            "UP主": row.get("uploader_name") or "",
            "UP主姓名": row.get("uploader_name") or "",
            "作者": row.get("uploader_name") or "",
            "视频标题": row.get("video_title") or "",
            "标题": row.get("video_title") or "",
            "点赞数": row.get("like_count") or "",
            "视频ID": aweme_id,
            "视频id": aweme_id,
            "视频Id": aweme_id,
            "作品ID": aweme_id,
            "作品id": aweme_id,
            "发布时间": row.get("发布时间") or row.get("date") or "",
            "发表时间": row.get("发表时间") or row.get("发布时间") or row.get("date") or "",
            "发布日期": row.get("发布日期") or row.get("发布时间") or row.get("date") or "",
            "create_time": row.get("create_time") or "",
        }
        raw_name = _normalize_filename_template(template).format_map(_FilenameTemplateValues(values))
        safe_name = sanitize_filename(raw_name)
        if aweme_id and aweme_id not in safe_name:
            safe_name = sanitize_filename(f"{safe_name}_{aweme_id}")
        return f"命名示例：{safe_name}.mp4"

    def _filename_context_for_row(self, row: dict[str, Any], config: ConfigLoader | None = None) -> dict[str, Any]:
        context = dict(row)
        if config is not None:
            aweme_id = str(context.get("aweme_id") or context.get("视频ID") or "").strip()
            sql_context = self._lookup_video_context(self._resolve_database_path(config), aweme_id)
            if sql_context:
                context = {**context, **sql_context}
        return context

    @staticmethod
    def _lookup_video_context(db_path: Path, aweme_id: str) -> dict[str, Any]:
        if not db_path.exists():
            return {}

        try:
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='video_score_current'"
                ).fetchone()
                if not exists:
                    return {}

                columns = [row[1] for row in conn.execute('PRAGMA table_info("video_score_current")').fetchall()]
                id_column = next((name for name in ("视频ID", "aweme_id", "video_id") if name in columns), None)
                if aweme_id and id_column:
                    result = conn.execute(
                        f'SELECT * FROM "video_score_current" WHERE "{id_column}" = ? LIMIT 1',
                        (aweme_id,),
                    ).fetchone()
                else:
                    order_column = "点赞数" if "点赞数" in columns else (id_column or columns[0])
                    result = conn.execute(
                        f'SELECT * FROM "video_score_current" ORDER BY CAST("{order_column}" AS INTEGER) DESC LIMIT 1'
                    ).fetchone()
                if not result:
                    return {}

                row = dict(result)
                video_id = str(row.get("视频ID") or row.get("aweme_id") or row.get("video_id") or "").strip()
                publish_time = str(row.get("发布日期") or row.get("发布时间") or row.get("date") or "").strip()
                publish_date = publish_time.split(" ", 1)[0] if publish_time else ""
                return {
                    **row,
                    "aweme_id": video_id,
                    "video_id": video_id,
                    "视频ID": video_id,
                    "视频id": video_id,
                    "视频Id": video_id,
                    "作品ID": video_id,
                    "作品id": video_id,
                    "video_url": row.get("视频链接") or row.get("video_url") or "",
                    "uploader_name": row.get("UP主姓名") or row.get("UP主") or row.get("uploader_name") or "",
                    "UP主": row.get("UP主姓名") or row.get("UP主") or row.get("uploader_name") or "",
                    "UP主姓名": row.get("UP主姓名") or row.get("UP主") or row.get("uploader_name") or "",
                    "video_title": row.get("视频标题") or row.get("video_title") or row.get("title") or "",
                    "视频标题": row.get("视频标题") or row.get("video_title") or row.get("title") or "",
                    "like_count": row.get("点赞数") or row.get("like_count") or row.get("digg_count") or "",
                    "点赞数": row.get("点赞数") or row.get("like_count") or row.get("digg_count") or "",
                    "video_grade": row.get("视频最终等级") or row.get("视频等级") or row.get("final_grade") or "",
                    "视频等级": row.get("视频最终等级") or row.get("视频等级") or row.get("final_grade") or "",
                    "等级": row.get("视频最终等级") or row.get("视频等级") or row.get("final_grade") or "",
                    "发布时间": publish_time,
                    "发表时间": publish_time,
                    "发布日期": publish_time,
                    "date": publish_date,
                    "create_time": row.get("发布时间戳") or row.get("create_time") or "",
                }
        except sqlite3.Error:
            return {}

    def refresh_stats(self):
        if self._is_busy():
            return
        self._snapshot_inputs()
        self.status.set("正在刷新统计...")
        self._set_busy(True, allow_stop=False, refresh_text="刷新中...")
        self._run_worker(self._refresh_stats_worker)

    def start_download(self):
        if self._is_busy():
            return
        self._snapshot_inputs()
        self.stop_requested.clear()
        self._reset_run_stats()
        self.status.set("正在准备下载...")
        self._set_busy(True, allow_stop=True, refresh_text="下载中...")
        self._run_worker(self._download_worker)

    def request_stop(self):
        self.stop_requested.set()
        self.status.set("正在停止，当前视频处理完成后会退出")
        self._log("收到停止请求，等待当前下载任务收尾")

    def reset_download_records(self):
        if self._is_busy():
            return

        self._snapshot_inputs()
        try:
            config = ConfigLoader(self.active_config_path)
            db_path = self._resolve_database_path(config)
            manifest_paths = self._download_manifest_paths(config)
        except Exception as exc:
            messagebox.showerror("重置失败", f"读取配置失败：{exc}")
            return

        detail_lines = [
            "将清空 SQLite 中 aweme 表的已下载视频记录。",
            "将清空当前下载目录下的 download_manifest.jsonl 下载清单。",
            "将同步清空评分概览快照里的下载状态标记。",
            "不会删除本地已下载的视频文件。",
            "",
            "注意：如果本地视频文件还在，下载器仍会通过文件名识别并跳过已存在视频。",
            "",
            f"数据库：{db_path}",
        ]
        if manifest_paths:
            detail_lines.append("下载清单：")
            detail_lines.extend(str(path) for path in manifest_paths)
        if not messagebox.askyesno("确认一键重置", "\n".join(detail_lines)):
            return

        try:
            deleted = self._clear_aweme_records(db_path)
            cleared_manifests = self._clear_download_manifests(manifest_paths)
            cleared_score_flags = self._clear_video_score_download_flags(db_path)
        except Exception as exc:
            self.status.set("重置失败")
            self._log(f"一键重置失败：{exc}")
            messagebox.showerror("重置失败", str(exc))
            return

        self.completed_count.set("0")
        self.pending_count.set(self.filtered_count.get())
        self.status.set("已清空已下载视频记录")
        self._log(
            f"已清空已下载视频记录：SQLite 删除 {deleted} 条，清空清单 {cleared_manifests} 个，"
            f"清空评分快照下载标记 {cleared_score_flags} 条；本地视频文件未删除。"
        )
        self.refresh_stats()

    def _is_busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _set_busy(self, busy: bool, allow_stop: bool = False, refresh_text: str = "运行中..."):
        state = tk.DISABLED if busy else tk.NORMAL
        if busy:
            self.refresh_button.configure(state=tk.NORMAL, text=refresh_text, style="Busy.TButton")
        else:
            self.refresh_button.configure(state=tk.NORMAL, text="刷新统计", style="TButton")
        self.start_button.configure(state=state)
        self.skip_failed_button.configure(state=state)
        self.reset_button.configure(state=state)
        self.save_filename_button.configure(state=state)
        self.filter_mode_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.grade_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.like_threshold_spin.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if allow_stop else tk.DISABLED)

    def _run_worker(self, target):
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _snapshot_inputs(self):
        self.active_csv_path = self.csv_path.get()
        self.active_failed_csv_path = self.failed_csv_path.get()
        self.active_config_path = self.config_path.get()
        self.active_download_path = self.download_path.get().strip()
        self.active_batch_count = max(int(self.batch_count.get()), 1)
        self.active_skip_failed = bool(self.skip_failed_records.get())
        self.active_filename_template = self.saved_filename_template
        self.active_filter_mode = self.download_filter_mode.get().strip() or "全部CSV"
        self.active_filter_grade = self._normalize_grade(self.download_filter_grade.get()) or "A"
        try:
            self.active_like_threshold = max(int(self.download_like_threshold.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            self.active_like_threshold = 10000

    def _on_filter_changed(self, *_args):
        mode = self.download_filter_mode.get().strip() or "全部CSV"
        grade = self._normalize_grade(self.download_filter_grade.get()) or "A"
        try:
            threshold = max(int(self.download_like_threshold.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            threshold = 10000
        self.status.set(f"下载筛选已切换：{self._describe_filter(mode, grade, threshold)}，点击刷新统计后生效")

    @staticmethod
    def _normalize_grade(value: Any) -> str:
        text = str(value or "").strip().upper().replace("级", "")
        return text[:1] if text[:1] in {"S", "A", "B", "C", "D"} else ""

    @staticmethod
    def _safe_int(value: Any, default: int = 0) -> int:
        try:
            text = str(value or "").replace(",", "").strip()
            return int(float(text)) if text else default
        except (TypeError, ValueError):
            return default

    def _row_video_grade(self, row: dict[str, Any]) -> str:
        for key in ("video_grade", "视频最终等级", "视频等级", "等级", "final_grade", "grade", "level"):
            grade = self._normalize_grade(row.get(key))
            if grade:
                return grade
        return ""

    def _row_like_count(self, row: dict[str, Any]) -> int:
        for key in ("like_count", "点赞数", "digg_count"):
            if str(row.get(key) or "").strip():
                return self._safe_int(row.get(key), 0)
        return 0

    def _describe_filter(self, mode: str | None = None, grade: str | None = None, threshold: int | None = None) -> str:
        mode = mode or self.active_filter_mode
        grade = grade or self.active_filter_grade
        threshold = self.active_like_threshold if threshold is None else threshold
        if mode == "指定等级":
            return f"仅下载 {grade or 'A'} 级视频"
        if mode == "高赞视频":
            return f"仅下载点赞数 ≥ {threshold} 的视频"
        return "下载 CSV 中全部视频"

    def _filter_rows_for_download(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mode = self.active_filter_mode
        context_by_id = self._load_filter_contexts(rows) if mode in {"指定等级", "高赞视频"} else {}
        enriched_rows = []
        for row in rows:
            aweme_id = str(row.get("aweme_id") or row.get("视频ID") or "").strip()
            context = context_by_id.get(aweme_id, {})
            enriched_rows.append({**row, **context} if context else row)

        if mode == "指定等级":
            target_grade = self.active_filter_grade
            return [row for row in enriched_rows if self._row_video_grade(row) == target_grade]
        if mode == "高赞视频":
            threshold = self.active_like_threshold
            return [row for row in enriched_rows if self._row_like_count(row) >= threshold]
        return list(enriched_rows)

    def _load_filter_contexts(self, rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        ids = []
        seen = set()
        for row in rows:
            aweme_id = str(row.get("aweme_id") or row.get("视频ID") or "").strip()
            if aweme_id and aweme_id not in seen:
                ids.append(aweme_id)
                seen.add(aweme_id)
        if not ids:
            return {}

        try:
            config = ConfigLoader(self.active_config_path)
            db_path = self._resolve_database_path(config)
        except Exception:
            return {}
        if not db_path.exists():
            return {}

        try:
            with sqlite3.connect(str(db_path), timeout=5) as conn:
                conn.row_factory = sqlite3.Row
                exists = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name='video_score_current'"
                ).fetchone()
                if not exists:
                    return {}
                columns = [row[1] for row in conn.execute('PRAGMA table_info("video_score_current")').fetchall()]
                id_column = next((name for name in ("视频ID", "aweme_id", "video_id") if name in columns), None)
                if not id_column:
                    return {}
                select_columns = [
                    name
                    for name in (
                        id_column,
                        "视频最终等级",
                        "视频等级",
                        "final_grade",
                        "点赞数",
                        "like_count",
                        "digg_count",
                    )
                    if name in columns
                ]
                if len(select_columns) <= 1:
                    return {}
                contexts: dict[str, dict[str, Any]] = {}
                for start in range(0, len(ids), 800):
                    batch = ids[start : start + 800]
                    placeholders = ",".join("?" for _ in batch)
                    sql = (
                        f'SELECT {", ".join(f"""\"{name}\"""" for name in select_columns)} '
                        f'FROM "video_score_current" WHERE "{id_column}" IN ({placeholders})'
                    )
                    for result in conn.execute(sql, batch).fetchall():
                        data = dict(result)
                        aweme_id = str(data.get(id_column) or "").strip()
                        if not aweme_id:
                            continue
                        contexts[aweme_id] = {
                            **data,
                            "aweme_id": aweme_id,
                            "video_grade": data.get("视频最终等级")
                            or data.get("视频等级")
                            or data.get("final_grade")
                            or "",
                            "like_count": data.get("点赞数")
                            or data.get("like_count")
                            or data.get("digg_count")
                            or "",
                        }
                return contexts
        except sqlite3.Error:
            return {}

    def _refresh_stats_worker(self):
        try:
            stats = asyncio.run(self._collect_stats())
            self.events.put(("stats", stats))
            self.events.put(
                (
                    "log",
                    f"统计完成：CSV 总数 {stats['csv_total']}，筛选后 {stats['total']}，"
                    f"筛选条件 {stats['filter_desc']}，已完成 {stats['completed']}，"
                    f"失败跳过 {stats['failed_skipped']}，待下载 {stats['pending']}",
                )
            )
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("idle", None))

    async def _collect_stats(self) -> dict[str, Any]:
        all_rows = _load_high_like_video_rows(self.active_csv_path)
        rows = self._filter_rows_for_download(all_rows)
        config = ConfigLoader(self.active_config_path)
        self._apply_runtime_config(config)
        database = await self._open_database(config)
        completed = 0
        failed_keys = self._load_failed_video_keys() if self.active_skip_failed else set()
        failed_skipped = 0
        try:
            for row in rows:
                aweme_id = row.get("aweme_id")
                if aweme_id and await database.is_downloaded(aweme_id):
                    completed += 1
                elif self._row_matches_failed(row, failed_keys):
                    failed_skipped += 1
        finally:
            await database.close()
        return {
            "csv_total": len(all_rows),
            "total": len(rows),
            "completed": completed,
            "pending": max(len(rows) - completed - failed_skipped, 0),
            "failed_skipped": failed_skipped,
            "filter_desc": self._describe_filter(),
        }

    def _download_worker(self):
        try:
            set_console_log_level(50)
            result = asyncio.run(self._download_selected_rows())
            self.events.put(("done", result))
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("idle", None))

    async def _download_selected_rows(self) -> dict[str, int]:
        all_rows = _load_high_like_video_rows(self.active_csv_path)
        rows = self._filter_rows_for_download(all_rows)
        config = ConfigLoader(self.active_config_path)
        self._apply_runtime_config(config)
        database = await self._open_database(config)
        failed_rows: list[dict[str, Any]] = []

        try:
            pending_rows = []
            completed = 0
            failed_keys = self._load_failed_video_keys() if self.active_skip_failed else set()
            failed_skipped = 0
            for row in rows:
                aweme_id = row.get("aweme_id")
                if aweme_id and await database.is_downloaded(aweme_id):
                    completed += 1
                    continue
                if self._row_matches_failed(row, failed_keys):
                    failed_skipped += 1
                    continue
                pending_rows.append(row)

            selected_rows = pending_rows[: self.active_batch_count]
            config.update(link=[row["video_url"] for row in selected_rows if row.get("video_url")])
            if selected_rows and not config.validate():
                raise RuntimeError("配置无效：请检查 config.yml 中的 path、cookie 等设置")

            cookie_manager = CookieManager()
            cookie_manager.set_cookies(config.get_cookies())

            self.events.put(
                (
                    "batch",
                    {
                        "csv_total": len(all_rows),
                        "total": len(rows),
                        "completed": completed,
                        "pending": len(pending_rows),
                        "failed_skipped": failed_skipped,
                        "batch": len(selected_rows),
                        "filter_desc": self._describe_filter(),
                    },
                )
            )

            success = 0
            failed = 0
            skipped = 0
            for index, row in enumerate(selected_rows, 1):
                if self.stop_requested.is_set():
                    break

                url = row.get("video_url")
                aweme_id = row.get("aweme_id") or "unknown"
                if not url:
                    failed += 1
                    failed_rows.append(self._failed_row(row, "missing video url"))
                    self.events.put(("progress", {"index": index, "success": success, "failed": failed, "skipped": skipped, "current": aweme_id}))
                    continue

                self.events.put(("current", f"正在下载 {index}/{len(selected_rows)}：{aweme_id}"))
                config.update(filename_context=self._filename_context_for_row(row, config))
                result = await download_url(url, config, cookie_manager, database, progress_reporter=None)
                if result and result.success > 0:
                    success += result.success
                    completed += result.success
                elif result and result.skipped > 0:
                    skipped += result.skipped
                else:
                    failed += 1
                    reason = "download returned no result"
                    if result:
                        reason = f"success={result.success}, failed={result.failed}, skipped={result.skipped}"
                    failed_rows.append(self._failed_row(row, reason))

                self.events.put(
                    (
                        "progress",
                        {
                            "index": index,
                            "success": success,
                            "failed": failed,
                            "skipped": skipped,
                            "completed": completed,
                            "current": aweme_id,
                        },
                    )
                )

            if failed_rows:
                _append_failed_high_like_rows(self.active_failed_csv_path, failed_rows)

            return {
                "success": success,
                "failed": failed,
                "skipped": skipped,
                "completed": completed,
                "failed_rows": len(failed_rows),
                "stopped": int(self.stop_requested.is_set()),
            }
        finally:
            await database.close()

    async def _open_database(self, config: ConfigLoader) -> Database:
        database = Database(db_path=str(self._resolve_database_path(config)))
        await database.initialize()
        return database

    @staticmethod
    def _resolve_database_path(config: ConfigLoader) -> Path:
        db_path = config.get("database_path", "dy_downloader.db") or "dy_downloader.db"
        path = Path(str(db_path)).expanduser()
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    def _download_manifest_paths(self, config: ConfigLoader) -> list[Path]:
        configured_path = self.active_download_path or str(config.get("path", "./Downloaded/"))
        current_manifest = self._resolve_download_path(configured_path) / "download_manifest.jsonl"
        default_manifest = PROJECT_ROOT / "Downloaded" / "download_manifest.jsonl"
        paths = [current_manifest]
        if default_manifest != current_manifest:
            paths.append(default_manifest)
        return paths

    @staticmethod
    def _clear_aweme_records(db_path: Path) -> int:
        if not db_path.exists():
            return 0

        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='aweme'"
            )
            if cursor.fetchone() is None:
                return 0

            cursor = conn.execute("DELETE FROM aweme")
            deleted = cursor.rowcount if cursor.rowcount is not None else 0
            try:
                conn.execute("DELETE FROM sqlite_sequence WHERE name='aweme'")
            except sqlite3.OperationalError:
                pass
            conn.commit()
            return max(deleted, 0)

    @staticmethod
    def _clear_video_score_download_flags(db_path: Path) -> int:
        if not db_path.exists():
            return 0

        with sqlite3.connect(str(db_path)) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='video_score_current'"
            )
            if cursor.fetchone() is None:
                return 0

            columns = {
                row[1]
                for row in conn.execute('PRAGMA table_info("video_score_current")').fetchall()
            }
            target_columns = [name for name in ("下载状态", "下载时间", "下载路径") if name in columns]
            if not target_columns:
                return 0

            assignments = ", ".join(f'"{name}" = \'\'' for name in target_columns)
            cursor = conn.execute(f'UPDATE "video_score_current" SET {assignments}')
            updated = cursor.rowcount if cursor.rowcount is not None else 0
            conn.commit()
            return max(updated, 0)

    @staticmethod
    def _clear_download_manifests(paths: list[Path]) -> int:
        cleared = 0
        seen: set[Path] = set()
        for path in paths:
            resolved = path.resolve()
            if resolved in seen or not path.exists():
                continue
            seen.add(resolved)
            path.write_text("", encoding="utf-8")
            cleared += 1
        return cleared

    def _apply_runtime_config(self, config: ConfigLoader) -> None:
        if self.active_download_path:
            config.update(path=str(self._resolve_download_path(self.active_download_path)))
        # The GUI downloader should place media directly in the selected folder,
        # without the original author/mode/work subdirectories.
        config.update(folderstyle=False, flat_output=True)
        if self.active_filename_template:
            config.update(filename_template=self.active_filename_template)

    def _load_failed_video_keys(self) -> set[str]:
        failed_path = Path(self.active_failed_csv_path)
        if not failed_path.exists():
            return set()

        try:
            failed_rows = _load_high_like_video_rows(str(failed_path))
        except Exception as exc:
            self.events.put(("log", f"读取失败 CSV 失败，将不跳过失败记录：{exc}"))
            return set()

        keys: set[str] = set()
        for row in failed_rows:
            aweme_id = (row.get("aweme_id") or "").strip()
            video_url = (row.get("video_url") or "").strip()
            if aweme_id:
                keys.add(f"id:{aweme_id}")
            if video_url:
                keys.add(f"url:{video_url}")
        return keys

    @staticmethod
    def _row_matches_failed(row: dict[str, Any], failed_keys: set[str]) -> bool:
        if not failed_keys:
            return False

        aweme_id = (row.get("aweme_id") or "").strip()
        video_url = (row.get("video_url") or "").strip()
        return (bool(aweme_id) and f"id:{aweme_id}" in failed_keys) or (
            bool(video_url) and f"url:{video_url}" in failed_keys
        )

    @staticmethod
    def _failed_row(row: dict[str, Any], reason: str) -> dict[str, Any]:
        return {
            **row,
            "failed_time": datetime.now().isoformat(timespec="seconds"),
            "failure_reason": reason,
        }

    def _drain_events(self):
        while True:
            try:
                event, payload = self.events.get_nowait()
            except queue.Empty:
                break

            if event == "stats":
                self._apply_stats(payload)
                self.status.set("统计已刷新")
            elif event == "batch":
                self._apply_stats(payload)
                self.current_batch_count.set(str(payload["batch"]))
                self.progress.configure(maximum=max(payload["batch"], 1), value=0)
                self.status.set("开始下载")
            elif event == "current":
                self.downloading_count.set("1")
                self.status.set(payload)
                self._log(payload)
            elif event == "progress":
                self.progress.configure(value=payload["index"])
                self.success_count.set(str(payload["success"]))
                self.failed_count.set(str(payload["failed"]))
                self.skipped_count.set(str(payload["skipped"]))
                if "completed" in payload:
                    self.completed_count.set(str(payload["completed"]))
                self.downloading_count.set("0")
                self._log(
                    f"完成处理：{payload['current']}，成功 {payload['success']}，失败 {payload['failed']}，跳过 {payload['skipped']}"
                )
            elif event == "done":
                suffix = "，已手动停止" if payload.get("stopped") else ""
                self.status.set(
                    f"下载完成：成功 {payload['success']}，失败 {payload['failed']}，跳过 {payload['skipped']}{suffix}"
                )
                if payload.get("failed_rows"):
                    self._log(f"失败记录已写入：{self.failed_csv_path.get()}")
            elif event == "log":
                self._log(payload)
            elif event == "error":
                self.status.set("发生错误")
                self._log(f"错误：{payload}")
                messagebox.showerror("运行失败", payload)
            elif event == "idle":
                self.downloading_count.set("0")
                self._set_busy(False)

        self.root.after(150, self._drain_events)

    def _apply_stats(self, stats: dict[str, Any]):
        self.total_count.set(str(stats.get("csv_total", stats.get("total", 0))))
        self.filtered_count.set(str(stats.get("total", 0)))
        self.completed_count.set(str(stats.get("completed", 0)))
        self.pending_count.set(str(stats.get("pending", 0)))
        self.failed_skipped_count.set(str(stats.get("failed_skipped", 0)))

    def _reset_run_stats(self):
        self.current_batch_count.set("0")
        self.downloading_count.set("0")
        self.success_count.set("0")
        self.failed_count.set("0")
        self.skipped_count.set("0")
        self.progress.configure(value=0)

    def _log(self, message: str):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, f"[{datetime.now().strftime('%H:%M:%S')}] {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)


def main():
    root = tk.Tk()
    HighLikeDownloaderGUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
