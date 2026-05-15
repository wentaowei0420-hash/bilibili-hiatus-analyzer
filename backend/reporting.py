from __future__ import annotations

import time

from common.reporting import SimpleTable
from common.runtime_control import check_stop


class JobProgress:
    def __init__(self, emit, update, transient: bool = False) -> None:
        self.emit = emit
        self.update_job = update
        self.transient = transient
        self._tasks = {}
        self._next_id = 1

    def __enter__(self) -> "JobProgress":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def add_task(self, description: str, total=None):
        task_id = self._next_id
        self._next_id += 1
        self._tasks[task_id] = {
            "description": description,
            "current": 0,
            "total": total,
        }
        self.emit(f"{description} | 0/{total if total is not None else '?'}")
        self.update_job(message=description, current=0, total=total)
        return task_id

    def advance(self, task_id, advance=1):
        task = self._tasks.get(task_id)
        if not task:
            return
        task["current"] += advance
        total = task.get("total")
        current = task["current"]
        self.update_job(message=task["description"], current=int(current), total=total)
        if total is None or current >= total or int(current) % 10 == 0:
            self.emit(f"{task['description']} | {int(current)}/{total if total is not None else '?'}")

    def update(self, task_id, **kwargs):
        task = self._tasks.get(task_id)
        if not task:
            return
        if "description" in kwargs:
            task["description"] = kwargs["description"]
        if "total" in kwargs:
            task["total"] = kwargs["total"]
        self.update_job(
            message=task["description"],
            current=int(task.get("current", 0)),
            total=task.get("total"),
        )


class JobReporter:
    def __init__(self, emit, update) -> None:
        self.emit = emit
        self.update_job = update

    def message(self, *args, **kwargs) -> None:
        text = kwargs.get("sep", " ").join(str(arg) for arg in args).strip()
        if text:
            self.emit(text)

    def panel(self, title: str, lines, border_style: str = "cyan", subtitle: str | None = None) -> None:
        self.emit(f"[{title}]")
        for line in lines or []:
            text = str(line).strip()
            if text:
                self.emit(text)

    def create_table(self, title: str, columns):
        return SimpleTable(title, columns)

    def render(self, renderable=None) -> None:
        if renderable is None:
            return
        self.emit(str(renderable))

    def progress(self, transient: bool = False):
        return JobProgress(self.emit, self.update_job, transient=transient)

    def wait(self, seconds: float, description: str, transient: bool = True) -> None:
        total = max(float(seconds or 0), 0.0)
        if total <= 0:
            return
        self.emit(f"{description} | waiting {total:.0f}s")
        end_at = time.time() + total
        while time.time() < end_at:
            check_stop()
            time.sleep(min(0.2, max(0.0, end_at - time.time())))
