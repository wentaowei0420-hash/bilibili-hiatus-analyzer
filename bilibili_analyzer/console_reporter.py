from __future__ import annotations

from .logging_utils import (
    create_progress,
    create_summary_panel,
    create_table,
    get_console,
    smart_print,
    wait_with_progress,
)


class RichAnalyzerReporter:
    def message(self, *args, **kwargs) -> None:
        smart_print(*args, **kwargs)

    def panel(self, title: str, lines, border_style: str = "cyan", subtitle: str | None = None) -> None:
        get_console().print(
            create_summary_panel(
                title,
                lines,
                border_style=border_style,
                subtitle=subtitle,
            )
        )

    def create_table(self, title: str, columns):
        return create_table(title, columns)

    def render(self, renderable=None) -> None:
        if renderable is None:
            get_console().print()
            return
        get_console().print(renderable)

    def progress(self, transient: bool = False):
        return create_progress(transient=transient)

    def wait(self, seconds: float, description: str, transient: bool = True) -> None:
        wait_with_progress(seconds, description, transient=transient)
