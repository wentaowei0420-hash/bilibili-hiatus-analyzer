import sys

from loguru import logger


ERROR_MARKERS = ("❌", "失败", "错误", "出错")
WARNING_MARKERS = ("⚠️", "异常", "风控", "重试队列")
SUCCESS_MARKERS = ("✅", "成功", "🎉", "🏆", "🚀")


def setup_logging(log_dir, log_prefix: str):
    log_dir.mkdir(parents=True, exist_ok=True)
    logger.remove()
    logger.add(
        sys.stdout,
        colorize=True,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | "
        "<level>{level: <8}</level> | <level>{message}</level>",
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
