from datetime import datetime


SHORT_VIDEO_LABEL = "短视频(0~30s)"
MEDIUM_VIDEO_LABEL = "中视频(30~60s)"
MEDIUM_LONG_VIDEO_LABEL = "中长视频(60~240s)"
LONG_VIDEO_LABEL = "长视频(240s+)"
DEFAULT_GROUP_NAME = "抖音博主"
UNKNOWN_DATE = "未知日期"


def normalize_timestamp(value):
    if value in (None, "", 0, "0"):
        return 0
    try:
        timestamp = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0
    if timestamp > 10**12:
        timestamp //= 1000
    return max(timestamp, 0)


def timestamp_to_date(timestamp):
    normalized = normalize_timestamp(timestamp)
    if not normalized:
        return UNKNOWN_DATE
    try:
        return datetime.fromtimestamp(normalized).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return UNKNOWN_DATE


def calculate_days_since(timestamp):
    normalized = normalize_timestamp(timestamp)
    if not normalized:
        return 0
    try:
        return (datetime.now() - datetime.fromtimestamp(normalized)).days
    except Exception:
        return 0


def seconds_to_duration_text(duration_seconds):
    seconds = max(int(duration_seconds or 0), 0)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain_seconds = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remain_seconds:02d}"
    return f"{minutes:02d}:{remain_seconds:02d}"


def normalize_duration_seconds(value):
    if value in (None, "", 0, "0"):
        return 0
    try:
        duration = float(value)
    except (TypeError, ValueError):
        return 0
    if duration > 1000:
        duration /= 1000
    return max(int(round(duration)), 0)


def categorize_duration(duration_seconds):
    if duration_seconds <= 30:
        return SHORT_VIDEO_LABEL
    if duration_seconds <= 60:
        return MEDIUM_VIDEO_LABEL
    if duration_seconds <= 240:
        return MEDIUM_LONG_VIDEO_LABEL
    return LONG_VIDEO_LABEL


def format_ratio(count, total):
    if total <= 0:
        return "0.00%"
    return f"{count / total * 100:.2f}%"


def calculate_average_update_interval_days(timestamps):
    normalized_timestamps = sorted(
        {
            normalized
            for normalized in (normalize_timestamp(timestamp) for timestamp in timestamps)
            if normalized > 0
        },
        reverse=True,
    )
    if len(normalized_timestamps) < 2:
        return None
    interval_days = [
        (normalized_timestamps[index] - normalized_timestamps[index + 1]) / 86400
        for index in range(len(normalized_timestamps) - 1)
    ]
    if not interval_days:
        return None
    return round(sum(interval_days) / len(interval_days), 2)


def parse_view_count(value):
    if value in (None, "", "--"):
        return 0
    text = str(value).strip().lower().replace(",", "")
    try:
        if text.endswith("w"):
            return int(float(text[:-1]) * 10000)
        return int(float(text))
    except ValueError:
        return 0
