from __future__ import annotations

import time
from contextlib import contextmanager
from typing import Iterator, Protocol

from common.runtime_control import check_stop


class ProgressLike(Protocol):
    def add_task(self, description: str, total=None): ...
    def advance(self, task_id, advance=1): ...
    def update(self, task_id, **kwargs): ...


class AnalyzerReporter(Protocol):
    def message(self, *args, **kwargs) -> None: ...
    def panel(self, title: str, lines, border_style: str = "cyan", subtitle: str | None = None) -> None: ...
    def create_table(self, title: str, columns): ...
    def render(self, renderable=None) -> None: ...
    def progress(self, transient: bool = False): ...
    def wait(self, seconds: float, description: str, transient: bool = True) -> None: ...


class NullProgress:
    def __enter__(self) -> "NullProgress":
        return self

    def __exit__(self, exc_type, exc, traceback) -> None:
        return None

    def add_task(self, description: str, total=None):
        return description

    def advance(self, task_id, advance=1):
        return None

    def update(self, task_id, **kwargs):
        return None


class NullReporter:
    def message(self, *args, **kwargs) -> None:
        return None

    def panel(self, title: str, lines, border_style: str = "cyan", subtitle: str | None = None) -> None:
        return None

    def create_table(self, title: str, columns):
        return SimpleTable(title, columns)

    def render(self, renderable=None) -> None:
        return None

    def progress(self, transient: bool = False):
        return NullProgress()

    def wait(self, seconds: float, description: str, transient: bool = True) -> None:
        total = max(float(seconds or 0), 0.0)
        end_at = time.time() + total
        while time.time() < end_at:
            check_stop()
            time.sleep(min(0.2, max(0.0, end_at - time.time())))


class SimpleTable:
    def __init__(self, title: str, columns) -> None:
        self.title = title
        self.columns = [column[0] if isinstance(column, tuple) else str(column) for column in columns]
        self.rows: list[tuple[str, ...]] = []

    def add_row(self, *values) -> None:
        self.rows.append(tuple(str(value) for value in values))

    def __str__(self) -> str:
        lines = [self.title, " | ".join(self.columns)]
        lines.extend(" | ".join(row) for row in self.rows)
        return "\n".join(lines)


@contextmanager
def reporting_context(reporter: AnalyzerReporter | None) -> Iterator[AnalyzerReporter]:
    yield reporter or NullReporter()
