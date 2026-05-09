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
from storage import Database
from utils.logger import set_console_log_level


PROJECT_ROOT = Path(__file__).resolve().parent


class HighLikeDownloaderGUI:
    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("抖音高赞视频下载")
        self.root.geometry("820x560")
        self.root.minsize(760, 520)

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

        self.csv_path = tk.StringVar(value=str(PROJECT_ROOT / HIGH_LIKE_CSV))
        self.failed_csv_path = tk.StringVar(value=str(PROJECT_ROOT / HIGH_LIKE_FAILED_CSV))
        self.config_path = tk.StringVar(value=str(PROJECT_ROOT / "config.yml"))
        self.download_path = tk.StringVar(
            value=self._load_config_download_path(str(PROJECT_ROOT / "config.yml"))
        )
        self.batch_count = tk.IntVar(value=20)
        self.skip_failed_records = tk.BooleanVar(value=False)
        self.filename_template = tk.StringVar(value="{date}_{title}_{aweme_id}")

        self.total_count = tk.StringVar(value="0")
        self.completed_count = tk.StringVar(value="0")
        self.pending_count = tk.StringVar(value="0")
        self.failed_skipped_count = tk.StringVar(value="0")
        self.current_batch_count = tk.StringVar(value="0")
        self.downloading_count = tk.StringVar(value="0")
        self.success_count = tk.StringVar(value="0")
        self.failed_count = tk.StringVar(value="0")
        self.skipped_count = tk.StringVar(value="0")
        self.status = tk.StringVar(value="请选择 CSV 后点击刷新统计")

        self._build_ui()
        self.root.after(150, self._drain_events)

    def _build_ui(self):
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

        filename_frame = ttk.Frame(outer)
        filename_frame.pack(fill=tk.X, pady=(10, 0))
        ttk.Label(filename_frame, text="视频命名格式").pack(side=tk.LEFT)
        ttk.Entry(filename_frame, textvariable=self.filename_template).pack(
            side=tk.LEFT,
            fill=tk.X,
            expand=True,
            padx=(8, 8),
        )
        ttk.Label(
            filename_frame,
            text="可用：{date} {title} {author} {aweme_id} {like_count}",
        ).pack(side=tk.LEFT)

        stats = ttk.LabelFrame(outer, text="下载统计", padding=10)
        stats.pack(fill=tk.X, pady=(10, 0))

        items = [
            ("CSV 视频总数", self.total_count),
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

    def _choose_download_dir(self):
        current = self._resolve_download_path(self.download_path.get())
        initial_dir = current if current.exists() else current.parent
        path = filedialog.askdirectory(
            title="选择下载输出目录",
            initialdir=str(initial_dir if initial_dir.exists() else PROJECT_ROOT),
        )
        if path:
            self.download_path.set(path)

    def refresh_stats(self):
        if self._is_busy():
            return
        self._snapshot_inputs()
        self._set_busy(True, allow_stop=False)
        self._run_worker(self._refresh_stats_worker)

    def start_download(self):
        if self._is_busy():
            return
        self._snapshot_inputs()
        self.stop_requested.clear()
        self._reset_run_stats()
        self._set_busy(True, allow_stop=True)
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
        except Exception as exc:
            self.status.set("重置失败")
            self._log(f"一键重置失败：{exc}")
            messagebox.showerror("重置失败", str(exc))
            return

        self.completed_count.set("0")
        self.pending_count.set(self.total_count.get())
        self.status.set("已清空已下载视频记录")
        self._log(
            f"已清空已下载视频记录：SQLite 删除 {deleted} 条，清空清单 {cleared_manifests} 个；本地视频文件未删除。"
        )
        self.refresh_stats()

    def _is_busy(self) -> bool:
        return self.worker is not None and self.worker.is_alive()

    def _set_busy(self, busy: bool, allow_stop: bool = False):
        state = tk.DISABLED if busy else tk.NORMAL
        self.refresh_button.configure(state=state)
        self.start_button.configure(state=state)
        self.skip_failed_button.configure(state=state)
        self.reset_button.configure(state=state)
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
        self.active_filename_template = self.filename_template.get().strip()

    def _refresh_stats_worker(self):
        try:
            stats = asyncio.run(self._collect_stats())
            self.events.put(("stats", stats))
            self.events.put(
                (
                    "log",
                    f"统计完成：总数 {stats['total']}，已完成 {stats['completed']}，"
                    f"失败跳过 {stats['failed_skipped']}，待下载 {stats['pending']}",
                )
            )
        except Exception as exc:
            self.events.put(("error", str(exc)))
        finally:
            self.events.put(("idle", None))

    async def _collect_stats(self) -> dict[str, int]:
        rows = _load_high_like_video_rows(self.active_csv_path)
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
            "total": len(rows),
            "completed": completed,
            "pending": max(len(rows) - completed - failed_skipped, 0),
            "failed_skipped": failed_skipped,
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
        rows = _load_high_like_video_rows(self.active_csv_path)
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
                        "total": len(rows),
                        "completed": completed,
                        "pending": len(pending_rows),
                        "failed_skipped": failed_skipped,
                        "batch": len(selected_rows),
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

    def _apply_stats(self, stats: dict[str, int]):
        self.total_count.set(str(stats.get("total", 0)))
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
