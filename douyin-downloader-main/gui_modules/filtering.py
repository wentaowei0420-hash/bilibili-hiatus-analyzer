from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from .failed_records import load_failed_aweme_ids


FILTER_ALL = "全部视频"
FILTER_HIGH_LIKE = "高赞视频"
FILTER_GRADE = "指定等级"
LEGACY_FILTER_ALL = "鍏ㄩ儴瑙嗛"
LEGACY_FILTER_HIGH_LIKE = "楂樿禐瑙嗛"
LEGACY_FILTER_GRADE = "鎸囧畾绛夌骇"


@dataclass(frozen=True)
class DownloadFilter:
    mode: str
    grade: str
    like_threshold: int
    min_duration: int = 0
    max_duration: int = 0
    skip_failed_records: bool = False
    browser_fallback_enabled: bool = True
    preflight_sample_enabled: bool = True


def normalize_filter_mode(value: Any) -> str:
    text = str(value or "").strip()
    aliases = {
        FILTER_ALL: LEGACY_FILTER_ALL,
        FILTER_HIGH_LIKE: LEGACY_FILTER_HIGH_LIKE,
        FILTER_GRADE: LEGACY_FILTER_GRADE,
    }
    text = aliases.get(text, text)
    return (
        text
        if text in {LEGACY_FILTER_ALL, LEGACY_FILTER_HIGH_LIKE, LEGACY_FILTER_GRADE}
        else LEGACY_FILTER_HIGH_LIKE
    )


def normalize_grade(value: Any) -> str:
    text = str(value or "").strip().upper().replace("级", "").replace("绾?", "")
    return text[:1] if text[:1] in {"S", "A", "B", "C", "D"} else ""


def safe_int(value: Any, default: int = 0) -> int:
    try:
        text = str(value or "").replace(",", "").strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def row_video_grade(row: dict[str, Any]) -> str:
    for key in (
        "video_grade",
        "final_grade",
        "grade",
        "level",
        "等级",
        "视频等级",
        "视频最终等级",
        "绛夌骇",
        "瑙嗛绛夌骇",
    ):
        grade = normalize_grade(row.get(key))
        if grade:
            return grade

    for value in row.values():
        grade = normalize_grade(value)
        if grade and str(value).strip().upper() in {"S", "A", "B", "C", "D"}:
            return grade
    return ""


def row_like_count(row: dict[str, Any]) -> int:
    for key in (
        "like_count",
        "digg_count",
        "likes",
        "点赞数",
        "鐐硅禐鏁?",
    ):
        if str(row.get(key) or "").strip():
            return safe_int(row.get(key), 0)
    return 0


def row_duration_seconds(row: dict[str, Any]) -> int:
    for key in (
        "duration_seconds",
        "video_duration",
        "duration",
        "视频时长(秒)",
        "瑙嗛鏃堕暱(绉?",
    ):
        if str(row.get(key) or "").strip():
            return safe_int(row.get(key), 0)
    return 0


def describe_filter(options: DownloadFilter) -> str:
    mode = normalize_filter_mode(options.mode)
    if mode == LEGACY_FILTER_GRADE:
        desc = f"仅下载 {options.grade or 'A'} 级视频"
    elif mode == LEGACY_FILTER_HIGH_LIKE:
        desc = f"仅下载点赞数 >= {options.like_threshold} 的视频"
    else:
        desc = "下载全部候选视频"

    if options.min_duration and options.max_duration:
        desc += f"，时长 {options.min_duration}~{options.max_duration} 秒"
    elif options.min_duration:
        desc += f"，时长 >= {options.min_duration} 秒"
    elif options.max_duration:
        desc += f"，时长 <= {options.max_duration} 秒"
    if options.skip_failed_records:
        desc += "，跳过失败记录"
    if options.browser_fallback_enabled:
        desc += "，浏览器兜底"
    if options.preflight_sample_enabled:
        desc += "，抽样检测"
    return desc


def filter_rows_for_download(
    rows: list[dict[str, Any]],
    options: DownloadFilter,
    *,
    failed_csv_path: Path | None = None,
) -> list[dict[str, Any]]:
    filtered = list(rows)
    mode = normalize_filter_mode(options.mode)

    if mode == LEGACY_FILTER_GRADE:
        filtered = [
            row for row in filtered if row_video_grade(row) == options.grade
        ]
    elif mode == LEGACY_FILTER_HIGH_LIKE:
        filtered = [
            row for row in filtered if row_like_count(row) >= options.like_threshold
        ]

    if options.min_duration:
        filtered = [
            row
            for row in filtered
            if row_duration_seconds(row) >= options.min_duration
        ]
    if options.max_duration:
        filtered = [
            row
            for row in filtered
            if row_duration_seconds(row) > 0
            and row_duration_seconds(row) <= options.max_duration
        ]

    if options.skip_failed_records and failed_csv_path:
        failed_aweme_ids = load_failed_aweme_ids(failed_csv_path)
        if failed_aweme_ids:
            filtered = [
                row
                for row in filtered
                if str(row.get("aweme_id") or "").strip() not in failed_aweme_ids
            ]
    return filtered
