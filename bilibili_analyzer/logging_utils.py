from loguru import logger
from rich.console import Console
from rich.logging import RichHandler
from rich.progress import (
    BarColumn,
    MofNCompleteColumn,
    Progress,
    SpinnerColumn,
    TaskProgressColumn,
    TextColumn,
    TimeElapsedColumn,
    TimeRemainingColumn,
)
from rich.panel import Panel
from rich.text import Text
from rich.table import Table
import time


ERROR_MARKERS = ("❌", "失败", "错误", "出错")
WARNING_MARKERS = ("⚠️", "异常", "风控", "重试队列")
SUCCESS_MARKERS = ("✅", "成功", "🏆", "📊", "🎬")
console = Console(soft_wrap=True)


def setup_logging(log_dir, log_prefix: str):
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        RichHandler(
            console=console,
            rich_tracebacks=True,
            markup=False,
            show_path=False,
        ),
        colorize=False,
        format="{message}",
    )
    logger.add(
        str(log_dir / f"{log_prefix}_{{time:YYYY-MM-DD}}.log"),
        rotation="00:00",
        retention="30 days",
        encoding="utf-8",
        level="INFO",
    )
    return logger


def smart_print(*args, **kwargs):
    text = kwargs.get("sep", " ").join(str(arg) for arg in args).strip()
    if not text:
        return

    if any(marker in text for marker in ERROR_MARKERS):
        logger.error(text)
    elif any(marker in text for marker in WARNING_MARKERS):
        logger.warning(text)
    elif any(marker in text for marker in SUCCESS_MARKERS):
        logger.success(text)
    else:
        logger.info(text)


def get_console() -> Console:
    return console


def create_progress(transient: bool = False) -> Progress:
    return Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(bar_width=None),
        TaskProgressColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        TimeRemainingColumn(),
        console=console,
        transient=transient,
        expand=True,
    )


def create_table(title: str, columns):
    table = Table(title=title, show_header=True, header_style="bold cyan")
    for column in columns:
        if isinstance(column, tuple):
            header = column[0]
            justify = column[1] if len(column) > 1 else "left"
            style = column[2] if len(column) > 2 else None
            table.add_column(header, justify=justify, style=style)
        else:
            table.add_column(str(column))
    return table


def create_summary_panel(title: str, lines, border_style: str = "cyan", subtitle: str | None = None):
    content = "\n".join(str(line) for line in lines if str(line).strip()) or "暂无数据"
    return Panel(
        Text.from_markup(content),
        title=title,
        border_style=border_style,
        subtitle=subtitle,
        expand=False,
        padding=(0, 1),
    )


def wait_with_progress(seconds: float, description: str, transient: bool = True, step: float = 0.2):
    total = max(float(seconds or 0), 0.0)
    if total <= 0:
        return

    with create_progress(transient=transient) as progress:
        task_id = progress.add_task(description, total=total)
        remaining = total
        while remaining > 0:
            sleep_for = min(step, remaining)
            time.sleep(sleep_for)
            progress.advance(task_id, sleep_for)
            remaining -= sleep_for
