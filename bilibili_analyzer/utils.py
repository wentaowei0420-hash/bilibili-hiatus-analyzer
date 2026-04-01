from datetime import datetime


SHORT_VIDEO_LABEL = "短视频(0~30s)"
MEDIUM_VIDEO_LABEL = "中视频(30~60s)"
MEDIUM_LONG_VIDEO_LABEL = "中长视频(60~240s)"
LONG_VIDEO_LABEL = "长视频(240s+)"
DEFAULT_GROUP_NAME = "默认分组"
UNKNOWN_DATE = "未知日期"


def parse_view_count(value):
    text = str(value or "").strip()
    if not text or text == "--":
        return 0

    text = text.replace(",", "")
    if text.endswith("万"):
        try:
            return int(float(text[:-1]) * 10000)
        except ValueError:
            return 0

    try:
        return int(float(text))
    except ValueError:
        return 0


def parse_duration_to_seconds(duration_text):
    text = str(duration_text or "").strip()
    if not text:
        return 0

    parts = text.split(":")
    try:
        numbers = [int(part) for part in parts]
    except ValueError:
        return 0

    if len(numbers) == 2:
        return numbers[0] * 60 + numbers[1]
    if len(numbers) == 3:
        return numbers[0] * 3600 + numbers[1] * 60 + numbers[2]
    return 0


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


def seconds_to_duration_text(duration_seconds):
    seconds = max(int(duration_seconds or 0), 0)
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    remain_seconds = seconds % 60
    if hours:
        return f"{hours:02d}:{minutes:02d}:{remain_seconds:02d}"
    return f"{minutes:02d}:{remain_seconds:02d}"


def build_homepage_url(mid):
    return f"https://space.bilibili.com/{mid}"


def normalize_group_ids(group_ids):
    if not group_ids:
        return []
    if isinstance(group_ids, list):
        return [int(group_id) for group_id in group_ids]
    return [int(group_ids)]


def format_group_ids(group_ids):
    normalized_ids = normalize_group_ids(group_ids)
    if not normalized_ids:
        return "0"
    return ",".join(str(group_id) for group_id in normalized_ids)


def resolve_group_names(group_ids, tag_name_map):
    normalized_ids = normalize_group_ids(group_ids)
    if not normalized_ids:
        return [DEFAULT_GROUP_NAME]
    return [tag_name_map.get(group_id, f"未知分组({group_id})") for group_id in normalized_ids]


def format_group_names(group_ids, tag_name_map):
    return ",".join(resolve_group_names(group_ids, tag_name_map))


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
    try:
        normalized = normalize_timestamp(timestamp)
        if not normalized or normalized <= 0:
            return UNKNOWN_DATE
        return datetime.fromtimestamp(normalized).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return UNKNOWN_DATE


def calculate_days_since(timestamp):
    try:
        normalized = normalize_timestamp(timestamp)
        if not normalized or normalized <= 0:
            return 0
        return (datetime.now() - datetime.fromtimestamp(normalized)).days
    except Exception:
        return 0
