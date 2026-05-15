from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping, Optional


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(str(value).replace(",", "").strip()))
    except (TypeError, ValueError):
        return default


@dataclass(slots=True)
class CreatorProfile:
    platform: str
    uploader_id: str
    uploader_name: str
    homepage: str = ""
    follower_count: int = 0
    published_video_count: int = 0
    total_favorited: int = 0
    average_like_count: int = 0
    group_ids: str = ""
    group_names: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, platform: str = "") -> "CreatorProfile":
        return cls(
            platform=platform,
            uploader_id=str(
                data.get("uploader_id")
                or data.get("sec_uid")
                or data.get("mid")
                or data.get("uid")
                or ""
            ),
            uploader_name=str(
                data.get("uploader_name")
                or data.get("nickname")
                or data.get("uname")
                or ""
            ),
            homepage=str(data.get("uploader_homepage") or data.get("homepage") or ""),
            follower_count=_safe_int(data.get("follower_count")),
            published_video_count=_safe_int(
                data.get("published_video_count")
                or data.get("aweme_count")
                or data.get("total_videos")
            ),
            total_favorited=_safe_int(data.get("total_favorited")),
            average_like_count=_safe_int(data.get("average_like_count")),
            group_ids=str(data.get("following_group_ids") or data.get("group_id_text") or ""),
            group_names=str(data.get("following_group_names") or data.get("group_name_text") or ""),
        )

    def to_dict(self, *, include_platform: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_platform:
            data.pop("platform", None)
        return data


@dataclass(slots=True)
class VideoEntry:
    platform: str
    uploader_id: str
    uploader_name: str
    video_id: str
    title: str = ""
    publish_timestamp: int = 0
    publish_date: str = ""
    duration_seconds: int = 0
    duration_text: str = ""
    like_count: int = 0
    view_count: int = 0
    url: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, platform: str = "") -> "VideoEntry":
        return cls(
            platform=platform,
            uploader_id=str(data.get("uploader_id") or data.get("sec_uid") or data.get("mid") or ""),
            uploader_name=str(data.get("uploader_name") or data.get("nickname") or data.get("uname") or ""),
            video_id=str(data.get("video_id") or data.get("aweme_id") or data.get("bvid") or ""),
            title=str(data.get("video_title") or data.get("title") or data.get("desc") or ""),
            publish_timestamp=_safe_int(data.get("publish_timestamp") or data.get("upload_timestamp")),
            publish_date=str(data.get("publish_date") or data.get("upload_date") or ""),
            duration_seconds=_safe_int(data.get("duration_seconds")),
            duration_text=str(data.get("duration_text") or ""),
            like_count=_safe_int(data.get("like_count")),
            view_count=_safe_int(data.get("view_count")),
            url=str(data.get("video_url") or data.get("url") or ""),
        )

    def to_dict(self, *, include_platform: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_platform:
            data.pop("platform", None)
        return data


@dataclass(slots=True)
class AnalysisResult:
    platform: str
    uploader_id: str
    uploader_name: str
    days_since_update: int
    upload_date: str = ""
    uploader_homepage: str = ""
    following_group_ids: str = ""
    following_group_names: str = ""
    following_remark: str = ""
    follower_count: int = 0
    total_favorited: Any = ""
    published_video_count: int = 0
    average_like_count: int = 0
    average_update_interval_days: Optional[float] = None
    latest_video_title: str = ""
    upload_timestamp: Any = ""
    activity_timestamp: Any = ""
    days_since_last_video: Any = ""
    view_count: Any = 0
    video_url: str = ""
    data_source: str = ""

    @classmethod
    def from_mapping(cls, data: Mapping[str, Any], *, platform: str = "") -> "AnalysisResult":
        interval = data.get("average_update_interval_days")
        try:
            interval_value = float(interval) if interval not in (None, "") else None
        except (TypeError, ValueError):
            interval_value = None
        return cls(
            platform=platform,
            uploader_id=str(data.get("uploader_id") or data.get("sec_uid") or data.get("mid") or ""),
            uploader_name=str(data.get("uploader_name") or data.get("nickname") or data.get("uname") or ""),
            days_since_update=_safe_int(data.get("days_since_update")),
            upload_date=str(data.get("upload_date") or data.get("publish_date") or ""),
            uploader_homepage=str(data.get("uploader_homepage") or data.get("homepage") or ""),
            following_group_ids=str(data.get("following_group_ids") or ""),
            following_group_names=str(data.get("following_group_names") or ""),
            following_remark=str(data.get("following_remark") or ""),
            follower_count=_safe_int(data.get("follower_count")),
            total_favorited=data.get("total_favorited", ""),
            published_video_count=_safe_int(data.get("published_video_count") or data.get("total_videos")),
            average_like_count=_safe_int(data.get("average_like_count")),
            average_update_interval_days=interval_value,
            latest_video_title=str(data.get("latest_video_title") or ""),
            upload_timestamp=data.get("upload_timestamp", ""),
            activity_timestamp=data.get("activity_timestamp", ""),
            days_since_last_video=data.get("days_since_last_video", data.get("days_since_update", "")),
            view_count=data.get("view_count", 0),
            video_url=str(data.get("video_url") or ""),
            data_source=str(data.get("data_source") or ""),
        )

    def to_dict(self, *, include_platform: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_platform:
            data.pop("platform", None)
        return data


@dataclass(slots=True)
class AnalysisSummary:
    platform: str
    result_count: int = 0
    video_count: int = 0
    summary_count: int = 0
    failed_count: int = 0
    exported_files: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(slots=True)
class VideoDurationSummary:
    platform: str
    uploader_id: str
    uploader_name: str
    follower_count: int = 0
    total_videos: int = 0
    latest_publish_timestamp: int = 0
    total_duration_seconds: int = 0
    average_duration_seconds: int = 0
    average_duration_text: str = "00:00"
    average_like_count: int = 0
    average_update_interval_days: Optional[float] = None
    short_video_count: int = 0
    short_video_ratio: str = "0.00%"
    medium_video_count: int = 0
    medium_video_ratio: str = "0.00%"
    medium_long_video_count: int = 0
    medium_long_video_ratio: str = "0.00%"
    long_video_count: int = 0
    long_video_ratio: str = "0.00%"
    total_favorited: Any = ""
    total_favorited_source: str = ""
    missing_like_count: int = 0
    like_data_complete: bool = True
    summary_scope: str = ""

    def to_dict(self, *, include_empty_platform: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_empty_platform:
            data.pop("platform", None)
        if self.total_favorited == "":
            data.pop("total_favorited", None)
        if not self.total_favorited_source:
            data.pop("total_favorited_source", None)
        if not self.summary_scope:
            data.pop("summary_scope", None)
        return data


@dataclass(slots=True)
class CreatorSummary:
    platform: str
    uploader_id: str
    uploader_name: str
    follower_count: Any = ""
    total_favorited: Any = ""
    total_videos: Any = ""
    latest_publish_timestamp: Any = ""
    total_duration_seconds: Any = ""
    average_duration_seconds: Any = ""
    average_duration_text: Any = ""
    average_like_count: Any = ""
    average_update_interval_days: Any = ""
    short_video_count: Any = ""
    short_video_ratio: Any = ""
    medium_video_count: Any = ""
    medium_video_ratio: Any = ""
    medium_long_video_count: Any = ""
    medium_long_video_ratio: Any = ""
    long_video_count: Any = ""
    long_video_ratio: Any = ""
    total_favorited_source: Any = ""
    summary_scope: str = ""

    def to_dict(self, *, include_platform: bool = False) -> dict[str, Any]:
        data = asdict(self)
        if not include_platform:
            data.pop("platform", None)
        if not self.total_favorited_source:
            data.pop("total_favorited_source", None)
        return data
