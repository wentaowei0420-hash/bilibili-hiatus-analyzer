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
from cli.main import download_url
from config import ConfigLoader
from core.downloader_base import _FilenameTemplateValues, _normalize_filename_template
from storage import Database
from utils.logger import set_console_log_level
from utils.validators import sanitize_filename


PROJECT_ROOT = Path(__file__).resolve().parent
DEFAULT_FILENAME_TEMPLATE = "等级_UP主_视频标题_点赞数"
GUI_SETTINGS_KEY = "high_like_gui"


class HighLikeDownloaderGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("抖音高赞视频下载")
        self.root.geometry("920x650")
        self.root.minsize(860, 620)

        self.events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self.worker: threading.Thread | None = None
        self.stop_requested = threading.Event()
        self.active_config_path = ""
        self.active_download_path = ""
        self.active_batch_count = 1
        self.active_filename_template = ""
        self.active_filter_mode = "高赞视频"
        self.active_filter_grade = "A"
        self.active_like_threshold = 10000
        self.active_min_duration = 0
        self.active_max_duration = 0
        self.preview_after_id = None

        default_config_path = str(PROJECT_ROOT / "config.yml")
        saved_gui_settings = self._load_gui_settings(default_config_path)
        self.config_path = tk.StringVar(value=default_config_path)
        self.download_path = tk.StringVar(
            value=str(
                self._resolve_download_path(
                    saved_gui_settings.get("download_path")
                    or self._load_config_download_path(default_config_path)
                )
            )
        )
        self.batch_count = tk.IntVar(
            value=self._coerce_int(saved_gui_settings.get("batch_count"), 20, minimum=1)
        )
        self.saved_filename_template = self._load_config_filename_template(default_config_path)
        self.filename_template = tk.StringVar(value=self.saved_filename_template)
        self.download_filter_mode = tk.StringVar(
            value=self._normalize_filter_mode(saved_gui_settings.get("download_filter_mode"))
        )
        self.download_filter_grade = tk.StringVar(
            value=self._normalize_grade(saved_gui_settings.get("download_filter_grade")) or "A"
        )
        self.download_like_threshold = tk.IntVar(
            value=self._coerce_int(saved_gui_settings.get("download_like_threshold"), 10000, minimum=0)
        )
        self.min_duration_seconds = tk.IntVar(
            value=self._coerce_int(saved_gui_settings.get("min_duration_seconds"), 0, minimum=0)
        )
        self.max_duration_seconds = tk.IntVar(
            value=self._coerce_int(saved_gui_settings.get("max_duration_seconds"), 0, minimum=0)
        )

        self.total_count = tk.StringVar(value="0")
        self.filtered_count = tk.StringVar(value="0")
        self.completed_count = tk.StringVar(value="0")
        self.pending_count = tk.StringVar(value="0")
        self.current_batch_count = tk.StringVar(value="0")
        self.downloading_count = tk.StringVar(value="0")
        self.success_count = tk.StringVar(value="0")
        self.failed_count = tk.StringVar(value="0")
        self.skipped_count = tk.StringVar(value="0")
        self.status = tk.StringVar(value="点击刷新统计后从 SQL 库读取候选视频")
        self.filename_preview = tk.StringVar(value="命名示例：点击刷新统计后从 SQL 库读取候选视频")

        self._build_ui()
        self.filename_template.trace_add("write", self._mark_filename_template_dirty)
        self.root.after(0, self._update_filename_preview)
        self.root.after(150, self._drain_events)

    def _build_ui(self):
        self._configure_styles()
        outer = ttk.Frame(self.root, padding=12)
        outer.pack(fill=tk.BOTH, expand=True)

        paths = ttk.LabelFrame(outer, text="文件设置", padding=10)
        paths.pack(fill=tk.X)

        self._path_row(paths, "配置文件", self.config_path, self._choose_config, 0)
        self._path_row(paths, "下载目录", self.download_path, self._choose_download_dir, 1)

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
        self.reset_button.pack(side=tk.LEFT, padx=(0, 8))
        self.save_settings_button = ttk.Button(
            controls,
            text="保存当前设置",
            command=self.save_current_settings,
        )
        self.save_settings_button.pack(side=tk.LEFT)

        filter_frame = ttk.LabelFrame(outer, text="下载筛选", padding=10)
        filter_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(filter_frame, text="下载范围").pack(side=tk.LEFT)
        self.filter_mode_combo = ttk.Combobox(
            filter_frame,
            textvariable=self.download_filter_mode,
            values=("全部视频", "高赞视频", "指定等级"),
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
        ttk.Label(filter_frame, text="时长").pack(side=tk.LEFT)
        self.min_duration_spin = ttk.Spinbox(
            filter_frame,
            from_=0,
            to=86400,
            textvariable=self.min_duration_seconds,
            width=7,
            command=self._on_filter_changed,
        )
        self.min_duration_spin.pack(side=tk.LEFT, padx=(8, 4))
        ttk.Label(filter_frame, text="~").pack(side=tk.LEFT)
        self.max_duration_spin = ttk.Spinbox(
            filter_frame,
            from_=0,
            to=86400,
            textvariable=self.max_duration_seconds,
            width=7,
            command=self._on_filter_changed,
        )
        self.max_duration_spin.pack(side=tk.LEFT, padx=(4, 18))
        ttk.Label(
            filter_frame,
            text="提示：SQL库会直接读取评分表；时长填 0 表示不限。",
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
            ("候选视频总数", self.total_count),
            ("筛选后视频数量", self.filtered_count),
            ("已完成下载视频数量", self.completed_count),
            ("待下载视频数量", self.pending_count),
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
        return DEFAULT_FILENAME_TEMPLATE

    @staticmethod
    def _coerce_int(value: Any, default: int, minimum: int | None = None) -> int:
        try:
            result = int(float(value))
        except (TypeError, ValueError, tk.TclError):
            result = default
        if minimum is not None:
            result = max(result, minimum)
        return result

    def _load_gui_settings(self, config_path: str) -> dict[str, Any]:
        try:
            config = ConfigLoader(config_path)
            settings = config.get(GUI_SETTINGS_KEY, {})
        except Exception:
            return {}
        return settings if isinstance(settings, dict) else {}

    @staticmethod
    def _resolve_download_path(path_value: str) -> Path:
        path = Path(str(path_value or "./Downloaded/")).expanduser()
        if path.is_absolute():
            return path
        return PROJECT_ROOT / path

    def _choose_config(self):
        path = filedialog.askopenfilename(
            title="选择配置文件",
            filetypes=[("YAML 文件", "*.yml *.yaml"), ("所有文件", "*.*")],
        )
        if path:
            self.config_path.set(path)
            settings = self._load_gui_settings(path)
            self.download_path.set(str(self._resolve_download_path(settings.get("download_path") or self._load_config_download_path(path))))
            self.batch_count.set(self._coerce_int(settings.get("batch_count"), self.batch_count.get(), minimum=1))
            self.download_filter_mode.set(self._normalize_filter_mode(settings.get("download_filter_mode")))
            self.download_filter_grade.set(self._normalize_grade(settings.get("download_filter_grade")) or self.download_filter_grade.get())
            self.download_like_threshold.set(
                self._coerce_int(settings.get("download_like_threshold"), self.download_like_threshold.get(), minimum=0)
            )
            self.min_duration_seconds.set(
                self._coerce_int(settings.get("min_duration_seconds"), self.min_duration_seconds.get(), minimum=0)
            )
            self.max_duration_seconds.set(
                self._coerce_int(settings.get("max_duration_seconds"), self.max_duration_seconds.get(), minimum=0)
            )
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
        template = self.filename_template.get().strip() or DEFAULT_FILENAME_TEMPLATE
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

    def save_current_settings(self):
        config_path = self.config_path.get().strip() or str(PROJECT_ROOT / "config.yml")
        template = self.filename_template.get().strip() or DEFAULT_FILENAME_TEMPLATE
        try:
            download_path = str(self._resolve_download_path(self.download_path.get()))
            settings = {
                "download_path": download_path,
                "batch_count": self._coerce_int(self.batch_count.get(), 20, minimum=1),
                "download_filter_mode": self._normalize_filter_mode(self.download_filter_mode.get()),
                "download_filter_grade": self._normalize_grade(self.download_filter_grade.get()) or "A",
                "download_like_threshold": self._coerce_int(
                    self.download_like_threshold.get(),
                    10000,
                    minimum=0,
                ),
                "min_duration_seconds": self._coerce_int(self.min_duration_seconds.get(), 0, minimum=0),
                "max_duration_seconds": self._coerce_int(self.max_duration_seconds.get(), 0, minimum=0),
            }
            config = ConfigLoader(config_path)
            config.update(
                path=download_path,
                filename_template=template,
                **{GUI_SETTINGS_KEY: settings},
            )
            config.save(config_path)
        except Exception as exc:
            messagebox.showerror("保存失败", f"当前设置保存失败：{exc}")
            return

        self.saved_filename_template = template
        self.filename_template.set(template)
        self.status.set("当前下载设置已保存，后续打开会自动复用")
        self._log(f"当前下载设置已保存：{config_path}")
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
        row = self._lookup_video_context(db_path, "") or {}

        if not row:
            return "命名示例：SQLite 暂无视频评分数据，请先运行视频评分"

        try:
            row = self._filename_context_for_row(row, config)
        except Exception:
            row = self._filename_context_for_row(row)
        aweme_id = str(row.get("aweme_id") or "").strip()
        template = self.saved_filename_template or DEFAULT_FILENAME_TEMPLATE
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
        self.reset_button.configure(state=state)
        self.save_filename_button.configure(state=state)
        self.save_settings_button.configure(state=state)
        self.filter_mode_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.grade_combo.configure(state=tk.DISABLED if busy else "readonly")
        self.like_threshold_spin.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.min_duration_spin.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.max_duration_spin.configure(state=tk.DISABLED if busy else tk.NORMAL)
        self.stop_button.configure(state=tk.NORMAL if allow_stop else tk.DISABLED)

    def _run_worker(self, target):
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _snapshot_inputs(self):
        self.active_config_path = self.config_path.get()
        self.active_download_path = self.download_path.get().strip()
        self.active_batch_count = max(int(self.batch_count.get()), 1)
        self.active_filename_template = self.saved_filename_template
        self.active_filter_mode = self._normalize_filter_mode(self.download_filter_mode.get())
        self.active_filter_grade = self._normalize_grade(self.download_filter_grade.get()) or "A"
        try:
            self.active_like_threshold = max(int(self.download_like_threshold.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            self.active_like_threshold = 10000
        try:
            self.active_min_duration = max(int(self.min_duration_seconds.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            self.active_min_duration = 0
        try:
            self.active_max_duration = max(int(self.max_duration_seconds.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            self.active_max_duration = 0

    def _on_filter_changed(self, *_args):
        mode = self._normalize_filter_mode(self.download_filter_mode.get())
        grade = self._normalize_grade(self.download_filter_grade.get()) or "A"
        try:
            threshold = max(int(self.download_like_threshold.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            threshold = 10000
        try:
            min_duration = max(int(self.min_duration_seconds.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            min_duration = 0
        try:
            max_duration = max(int(self.max_duration_seconds.get()), 0)
        except (TypeError, ValueError, tk.TclError):
            max_duration = 0
        self.status.set(
            f"下载筛选已切换：{self._describe_filter(mode, grade, threshold, min_duration, max_duration)}，点击刷新统计后生效"
        )

    @staticmethod
    def _normalize_filter_mode(value: Any) -> str:
        text = str(value or "").strip()
        return text if text in {"全部视频", "高赞视频", "指定等级"} else "高赞视频"

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

    def _row_duration_seconds(self, row: dict[str, Any]) -> int:
        for key in ("duration_seconds", "视频时长(秒)", "video_duration", "duration"):
            if str(row.get(key) or "").strip():
                return self._safe_int(row.get(key), 0)
        return 0

    def _describe_filter(
        self,
        mode: str | None = None,
        grade: str | None = None,
        threshold: int | None = None,
        min_duration: int | None = None,
        max_duration: int | None = None,
    ) -> str:
        mode = mode or self.active_filter_mode
        grade = grade or self.active_filter_grade
        threshold = self.active_like_threshold if threshold is None else threshold
        min_duration = self.active_min_duration if min_duration is None else min_duration
        max_duration = self.active_max_duration if max_duration is None else max_duration
        if mode == "指定等级":
            desc = f"仅下载 {grade or 'A'} 级视频"
        elif mode == "高赞视频":
            desc = f"仅下载点赞数 ≥ {threshold} 的视频"
        else:
            desc = "下载全部候选视频"
        if min_duration and max_duration:
            desc += f"，时长 {min_duration}~{max_duration} 秒"
        elif min_duration:
            desc += f"，时长 ≥ {min_duration} 秒"
        elif max_duration:
            desc += f"，时长 ≤ {max_duration} 秒"
        return desc

    def _filter_rows_for_download(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        mode = self.active_filter_mode
        enriched_rows = list(rows)

        if mode == "指定等级":
            target_grade = self.active_filter_grade
            enriched_rows = [row for row in enriched_rows if self._row_video_grade(row) == target_grade]
        elif mode == "高赞视频":
            threshold = self.active_like_threshold
            enriched_rows = [row for row in enriched_rows if self._row_like_count(row) >= threshold]
        if self.active_min_duration:
            enriched_rows = [
                row for row in enriched_rows if self._row_duration_seconds(row) >= self.active_min_duration
            ]
        if self.active_max_duration:
            enriched_rows = [
                row
                for row in enriched_rows
                if self._row_duration_seconds(row) > 0
                and self._row_duration_seconds(row) <= self.active_max_duration
            ]
        return list(enriched_rows)

    def _load_candidate_rows(self, config: ConfigLoader) -> list[dict[str, Any]]:
        return self._load_sql_video_rows(config)

    def _load_sql_video_rows(self, config: ConfigLoader) -> list[dict[str, Any]]:
        db_path = self._resolve_rating_database_path(config)
        if not db_path.exists():
            raise FileNotFoundError(f"未找到评分数据库：{db_path}")

        with sqlite3.connect(str(db_path), timeout=10) as conn:
            conn.row_factory = sqlite3.Row
            exists = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='video_score_current'"
            ).fetchone()
            if not exists:
                raise RuntimeError(f"评分数据库缺少 video_score_current 表：{db_path}")

            columns = [row[1] for row in conn.execute('PRAGMA table_info("video_score_current")').fetchall()]

            def pick(*names: str) -> str:
                return next((name for name in names if name in columns), "")

            id_column = pick("视频ID", "aweme_id", "video_id")
            if not id_column:
                raise RuntimeError("video_score_current 缺少视频ID列")

            selected = [
                name
                for name in (
                    pick("UP主姓名", "UP主", "uploader_name"),
                    pick("UP主UID", "uploader_id"),
                    pick("视频标题", "video_title", "title"),
                    id_column,
                    pick("视频链接", "video_url", "url"),
                    pick("发布日期", "发布时间", "date"),
                    pick("发布时间戳", "create_time"),
                    pick("视频时长(秒)", "duration_seconds", "duration"),
                    pick("时长分类", "duration_category"),
                    pick("点赞数", "like_count", "digg_count"),
                    pick("视频最终等级", "视频等级", "final_grade"),
                    pick("视频最终分", "final_score"),
                    pick("下载状态", "download_status"),
                    pick("下载时间", "download_time"),
                    pick("下载路径", "download_path"),
                )
                if name
            ]
            selected = list(dict.fromkeys(selected))
            order_parts = []
            score_column = pick("视频最终分", "final_score")
            like_column = pick("点赞数", "like_count", "digg_count")
            if score_column:
                order_parts.append(f'CAST(COALESCE("{score_column}", 0) AS REAL) DESC')
            if like_column:
                order_parts.append(f'CAST(COALESCE("{like_column}", 0) AS REAL) DESC')
            order_sql = "ORDER BY " + ", ".join(order_parts) if order_parts else ""
            sql = f'SELECT {", ".join(f"""\"{name}\"""" for name in selected)} FROM "video_score_current" {order_sql}'
            raw_rows = conn.execute(sql).fetchall()

        rows = []
        for raw in raw_rows:
            row = dict(raw)
            aweme_id = str(row.get(id_column) or "").strip()
            if not aweme_id:
                continue
            video_url = str(row.get("视频链接") or row.get("video_url") or row.get("url") or "").strip()
            if not video_url:
                video_url = f"https://www.douyin.com/video/{aweme_id}"
            grade = row.get("视频最终等级") or row.get("视频等级") or row.get("final_grade") or ""
            like_count = row.get("点赞数") or row.get("like_count") or row.get("digg_count") or ""
            duration = row.get("视频时长(秒)") or row.get("duration_seconds") or row.get("duration") or ""
            uploader_name = row.get("UP主姓名") or row.get("UP主") or row.get("uploader_name") or ""
            video_title = row.get("视频标题") or row.get("video_title") or row.get("title") or ""
            publish_time = row.get("发布日期") or row.get("发布时间") or row.get("date") or ""
            normalized = {
                **row,
                "aweme_id": aweme_id,
                "video_id": aweme_id,
                "视频ID": aweme_id,
                "video_url": video_url,
                "视频链接": video_url,
                "video_grade": grade,
                "视频等级": grade,
                "等级": grade,
                "like_count": like_count,
                "点赞数": like_count,
                "duration_seconds": duration,
                "视频时长(秒)": duration,
                "uploader_name": uploader_name,
                "UP主": uploader_name,
                "UP主姓名": uploader_name,
                "video_title": video_title,
                "视频标题": video_title,
                "发布时间": publish_time,
                "发表时间": publish_time,
                "发布日期": publish_time,
                "date": str(publish_time or "").split(" ", 1)[0],
                "create_time": row.get("发布时间戳") or row.get("create_time") or "",
            }
            rows.append(normalized)
        return rows

    def _resolve_rating_database_path(self, config: ConfigLoader) -> Path:
        raw_config = config.config if isinstance(config.config, dict) else {}
        for key in ("rating_database_path", "rating_store_db", "rating_db_path"):
            value = raw_config.get(key)
            if value:
                path = Path(str(value)).expanduser()
                return path if path.is_absolute() else PROJECT_ROOT / path

        export_db = self._resolve_database_path(config)
        candidates = []
        if export_db.name == "douyin_export_store.db":
            candidates.append(export_db.with_name("douyin_rating_store.db"))
        candidates.append(export_db.parent / "douyin_rating_store.db")
        candidates.append(PROJECT_ROOT.parent / "data" / "douyin" / "state" / "douyin_rating_store.db")
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    def _refresh_stats_worker(self):
        try:
            stats = asyncio.run(self._collect_stats())
            self.events.put(("stats", stats))
            self.events.put(
                (
                    "log",
                    f"统计完成：候选总数 {stats['candidate_total']}，筛选后 {stats['total']}，"
                    f"筛选条件 {stats['filter_desc']}，已完成 {stats['completed']}，"
                    f"待下载 {stats['pending']}",
                )
            )
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("idle", None))

    async def _collect_stats(self) -> dict[str, Any]:
        config = ConfigLoader(self.active_config_path)
        self._apply_runtime_config(config)
        all_rows = self._load_candidate_rows(config)
        rows = self._filter_rows_for_download(all_rows)
        database = await self._open_database(config)
        completed = 0
        try:
            for row in rows:
                aweme_id = row.get("aweme_id")
                if aweme_id and await database.is_downloaded(aweme_id):
                    completed += 1
        finally:
            await database.close()
        return {
            "candidate_total": len(all_rows),
            "total": len(rows),
            "completed": completed,
            "pending": max(len(rows) - completed, 0),
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
        config = ConfigLoader(self.active_config_path)
        self._apply_runtime_config(config)
        all_rows = self._load_candidate_rows(config)
        rows = self._filter_rows_for_download(all_rows)
        database = await self._open_database(config)

        try:
            pending_rows = []
            completed = 0
            for row in rows:
                aweme_id = row.get("aweme_id")
                if aweme_id and await database.is_downloaded(aweme_id):
                    completed += 1
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
                        "candidate_total": len(all_rows),
                        "total": len(rows),
                        "completed": completed,
                        "pending": len(pending_rows),
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

            return {
                "success": success,
                "failed": failed,
                "skipped": skipped,
                "completed": completed,
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
        self.total_count.set(str(stats.get("candidate_total", stats.get("total", 0))))
        self.filtered_count.set(str(stats.get("total", 0)))
        self.completed_count.set(str(stats.get("completed", 0)))
        self.pending_count.set(str(stats.get("pending", 0)))

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
