import time

from bilibili_analyzer.logging_utils import (
    create_progress,
    create_summary_panel,
    create_table,
    get_console,
    smart_print as print,
    wait_with_progress,
)

from .browser_client import DouyinRateLimitError, DouyinServiceError
from .exporters import (
    save_all_videos_to_csv,
    save_cache_inventory_to_csv,
    save_to_csv,
    save_video_duration_analysis_to_csv,
    save_video_duration_report,
)
from .utils import (
    DEFAULT_GROUP_NAME,
    LONG_VIDEO_LABEL,
    MEDIUM_LONG_VIDEO_LABEL,
    MEDIUM_VIDEO_LABEL,
    SHORT_VIDEO_LABEL,
    UNKNOWN_DATE,
    calculate_average_update_interval_days,
    calculate_days_since,
    format_ratio,
    normalize_timestamp,
    seconds_to_duration_text,
)


class DouyinHiatusAnalyzer:
    def __init__(self, config, browser_client, cache_store, upload_callback=None):
        self.config = config
        self.browser_client = browser_client
        self.cache_store = cache_store
        self.upload_callback = upload_callback

    @staticmethod
    def sort_followings_by_follower_count(followings):
        def follower_sort_key(user):
            raw_count = 0
            if isinstance(user, dict):
                raw_count = user.get("follower_count") or 0
            try:
                count = int(raw_count)
            except (TypeError, ValueError):
                count = 0
            nickname = ""
            if isinstance(user, dict):
                nickname = str(user.get("nickname") or "")
            return (-count, nickname)

        return sorted(followings or [], key=follower_sort_key)

    def get_fetch_mode(self):
        if self.config.fetch_mode in {"counts", "monitor", "delta", "full"}:
            return self.config.fetch_mode
        return "monitor"

    def should_export_duration_analysis(self):
        return self.config.enable_video_duration_analysis and self.get_fetch_mode() == "full"

    def should_export_summary_analysis(self):
        return self.config.enable_video_duration_analysis

    @staticmethod
    def _safe_int(value, default=0):
        try:
            if value in (None, ""):
                return default
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def calculate_average_like_from_profile(self, user, fallback=""):
        total_favorited = self._safe_int((user or {}).get("total_favorited"), 0)
        published_video_count = self._safe_int((user or {}).get("aweme_count"), 0)
        if total_favorited > 0 and published_video_count > 0:
            return int(total_favorited / published_video_count)
        return fallback

    @staticmethod
    def merge_videos(existing_videos, new_videos):
        merged = {}
        for video in existing_videos or []:
            aweme_id = video.get("aweme_id")
            if aweme_id:
                merged[aweme_id] = video
        for video in new_videos or []:
            aweme_id = video.get("aweme_id")
            if aweme_id:
                merged[aweme_id] = video
        return sorted(
            merged.values(),
            key=lambda item: normalize_timestamp(item.get("publish_timestamp")),
            reverse=True,
        )

    @staticmethod
    def get_latest_video_from_videos(videos):
        if not videos:
            return None
        return max(videos, key=lambda item: normalize_timestamp(item.get("publish_timestamp")))

    def get_latest_video_from_entry(self, entry):
        if not isinstance(entry, dict):
            return None
        latest_video = entry.get("latest_video")
        if latest_video:
            return latest_video
        return self.get_latest_video_from_videos(entry.get("videos", []))

    def build_result_item(self, user, summary, latest_video):
        upload_timestamp = normalize_timestamp(latest_video.get("publish_timestamp"))
        days_since = calculate_days_since(upload_timestamp)
        published_video_count = user.get("aweme_count") or summary.get("total_videos", 0)
        data_source = "douyin_video_api" if self.get_fetch_mode() == "full" else "douyin_recent_video_api"
        return {
            "uploader_name": user["nickname"],
            "following_remark": user.get("remark_name", ""),
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "follower_count": user.get("follower_count"),
            "total_favorited": user.get("total_favorited", ""),
            "published_video_count": published_video_count,
            "average_like_count": self.calculate_average_like_from_profile(
                user,
                summary.get("average_like_count", 0),
            ),
            "average_update_interval_days": summary.get("average_update_interval_days"),
            "latest_video_title": latest_video.get("video_title", "无标题视频"),
            "upload_timestamp": upload_timestamp,
            "upload_date": latest_video.get("publish_date", UNKNOWN_DATE),
            "days_since_update": days_since,
            "days_since_last_video": days_since,
            "view_count": latest_video.get("view_count", 0),
            "video_url": latest_video.get("video_url", ""),
            "data_source": data_source,
        }

    def build_counts_only_result_item(self, user):
        return {
            "uploader_name": user["nickname"],
            "following_remark": user.get("remark_name", ""),
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "follower_count": user.get("follower_count", ""),
            "total_favorited": user.get("total_favorited", ""),
            "published_video_count": user.get("aweme_count", 0),
            "average_like_count": self.calculate_average_like_from_profile(user, ""),
            "average_update_interval_days": "",
            "latest_video_title": "",
            "upload_date": "",
            "days_since_update": "",
            "days_since_last_video": "",
            "view_count": "",
            "video_url": "",
            "data_source": "douyin_followings_api",
        }

    def build_no_video_result_item(self, user):
        return {
            "uploader_name": user["nickname"],
            "following_remark": user.get("remark_name", ""),
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "follower_count": user.get("follower_count"),
            "total_favorited": user.get("total_favorited", ""),
            "published_video_count": user.get("aweme_count", 0),
            "average_like_count": self.calculate_average_like_from_profile(user, 0),
            "average_update_interval_days": None,
            "latest_video_title": "暂无公开视频",
            "upload_date": UNKNOWN_DATE,
            "days_since_update": 0,
            "days_since_last_video": 0,
            "view_count": 0,
            "video_url": "",
            "data_source": "no_video",
        }

    def build_fetch_failed_result_item(self, user):
        return {
            "uploader_name": user["nickname"],
            "following_remark": user.get("remark_name", ""),
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "follower_count": user.get("follower_count"),
            "total_favorited": user.get("total_favorited", ""),
            "published_video_count": user.get("aweme_count", 0),
            "average_like_count": self.calculate_average_like_from_profile(user, 0),
            "average_update_interval_days": None,
            "latest_video_title": "抓取失败",
            "upload_date": UNKNOWN_DATE,
            "days_since_update": 0,
            "days_since_last_video": 0,
            "view_count": 0,
            "video_url": "",
            "data_source": "fetch_failed",
        }

    def build_empty_summary(self, user):
        total_videos = user.get("aweme_count") or 0
        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "follower_count": user.get("follower_count"),
            "total_favorited": user.get("total_favorited", ""),
            "total_videos": total_videos,
            "latest_publish_timestamp": 0,
            "total_duration_seconds": 0,
            "average_duration_seconds": 0,
            "average_duration_text": "00:00",
            "average_like_count": self.calculate_average_like_from_profile(user, 0),
            "average_update_interval_days": None,
            "short_video_count": 0,
            "short_video_ratio": "0.00%",
            "medium_video_count": 0,
            "medium_video_ratio": "0.00%",
            "medium_long_video_count": 0,
            "medium_long_video_ratio": "0.00%",
            "long_video_count": 0,
            "long_video_ratio": "0.00%",
            "summary_scope": "empty",
        }

    def build_counts_only_summary(self, user):
        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "follower_count": user.get("follower_count", ""),
            "total_favorited": user.get("total_favorited", ""),
            # counts 模式只抓基础主页信息，因此分析表至少保留真实视频总数。
            "total_videos": user.get("aweme_count", ""),
            "latest_publish_timestamp": "",
            "total_duration_seconds": "",
            "average_duration_seconds": "",
            "average_duration_text": "",
            "average_like_count": self.calculate_average_like_from_profile(user, ""),
            "average_update_interval_days": "",
            "short_video_count": "",
            "short_video_ratio": "",
            "medium_video_count": "",
            "medium_video_ratio": "",
            "medium_long_video_count": "",
            "medium_long_video_ratio": "",
            "long_video_count": "",
            "long_video_ratio": "",
            "summary_scope": "counts",
        }

    def build_partial_summary(self, user, latest_video=None):
        latest_publish_timestamp = 0
        if isinstance(latest_video, dict):
            latest_publish_timestamp = normalize_timestamp(latest_video.get("publish_timestamp"))

        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "follower_count": user.get("follower_count", ""),
            "total_favorited": user.get("total_favorited", ""),
            "total_videos": user.get("aweme_count", ""),
            "latest_publish_timestamp": latest_publish_timestamp,
            "total_duration_seconds": "",
            "average_duration_seconds": "",
            "average_duration_text": "",
            "average_like_count": self.calculate_average_like_from_profile(user, ""),
            "average_update_interval_days": "",
            "short_video_count": "",
            "short_video_ratio": "",
            "medium_video_count": "",
            "medium_video_ratio": "",
            "medium_long_video_count": "",
            "medium_long_video_ratio": "",
            "long_video_count": "",
            "long_video_ratio": "",
            "summary_scope": "partial",
        }

    def build_summary_from_cached_entry(self, user, entry):
        if not isinstance(user, dict):
            user = {}
        if not isinstance(entry, dict):
            return self.build_counts_only_summary(user)

        cached_videos = entry.get("videos", [])
        latest_video = self.get_latest_video_from_entry(entry)
        cached_summary = entry.get("summary", {})

        if self.summary_has_complete_statistics(cached_summary):
            summary = dict(cached_summary or {})
            summary["uploader_name"] = user.get("nickname", summary.get("uploader_name", ""))
            summary["uploader_id"] = user.get("sec_uid", summary.get("uploader_id", ""))
            summary["follower_count"] = user.get("follower_count", summary.get("follower_count", ""))
            summary["total_favorited"] = user.get(
                "total_favorited",
                summary.get("total_favorited", ""),
            )
            return self.normalize_summary_for_mode(user, summary, cached_videos, latest_video)

        if isinstance(cached_videos, list) and cached_videos:
            summary = self.build_video_duration_summary(user, cached_videos)
            summary["summary_scope"] = "cached_sample"
            return summary

        return self.build_counts_only_summary(user)

    def build_result_from_cached_entry(self, user, entry):
        summary = self.build_summary_from_cached_entry(user, entry)
        latest_video = self.get_latest_video_from_entry(entry) if isinstance(entry, dict) else None

        if latest_video:
            result = self.build_result_item(user, summary, latest_video)
        else:
            result = self.build_counts_only_result_item(user)
            result["average_like_count"] = summary.get("average_like_count", "")
            result["average_update_interval_days"] = summary.get("average_update_interval_days", "")

            latest_publish_timestamp = normalize_timestamp(summary.get("latest_publish_timestamp"))
            if latest_publish_timestamp:
                result["upload_timestamp"] = latest_publish_timestamp
                result["data_source"] = "douyin_cached_summary"

        self.cache_store.refresh_result_runtime_fields(result)
        return result

    def rebuild_summary_rows_from_cache(self, followings=None, progress=None):
        followings = followings if isinstance(followings, list) else self.cache_store.load_followings_cache()
        progress = progress if isinstance(progress, dict) else self.cache_store.load_progress()

        summary_rows = []
        for user in self.sort_followings_by_follower_count(followings):
            uid = user.get("sec_uid")
            entry = progress.get(uid) if isinstance(progress, dict) and uid else None
            summary_rows.append(self.build_summary_from_cached_entry(user, entry))
        return summary_rows

    @staticmethod
    def has_complete_video_sample(user, videos):
        if not isinstance(videos, list) or not videos:
            return False

        aweme_count = 0
        if isinstance(user, dict):
            aweme_count = user.get("aweme_count") or 0

        try:
            aweme_count = int(aweme_count)
        except (TypeError, ValueError):
            aweme_count = 0

        return aweme_count > 0 and len(videos) >= aweme_count

    @staticmethod
    def summary_has_complete_statistics(summary):
        if not isinstance(summary, dict):
            return False

        summary_scope = str(summary.get("summary_scope") or "").strip().lower()
        if summary_scope in {"full", "preserved_full"}:
            return True

        numeric_keys = [
            "total_duration_seconds",
            "average_duration_seconds",
            "average_like_count",
            "short_video_count",
            "medium_video_count",
            "medium_long_video_count",
            "long_video_count",
        ]
        for key in numeric_keys:
            value = summary.get(key)
            try:
                if value is not None and value != "" and float(value) > 0:
                    return True
            except (TypeError, ValueError):
                continue

        average_update_interval_days = summary.get("average_update_interval_days")
        if average_update_interval_days not in (None, ""):
            return True

        average_duration_text = str(summary.get("average_duration_text") or "").strip()
        if average_duration_text and average_duration_text != "00:00":
            return True

        return False

    def build_preserved_full_summary(self, user, summary, latest_video=None):
        preserved = self.build_partial_summary(user, latest_video)
        for key in [
            "total_duration_seconds",
            "average_duration_seconds",
            "average_duration_text",
            "average_like_count",
            "average_update_interval_days",
            "short_video_count",
            "short_video_ratio",
            "medium_video_count",
            "medium_video_ratio",
            "medium_long_video_count",
            "medium_long_video_ratio",
            "long_video_count",
            "long_video_ratio",
        ]:
            preserved[key] = summary.get(key, preserved.get(key))
        preserved["summary_scope"] = "preserved_full"
        return preserved

    def normalize_summary_for_mode(self, user, summary, videos, latest_video):
        if self.get_fetch_mode() == "full":
            return summary
        if self.has_complete_video_sample(user, videos):
            return summary
        if self.summary_has_complete_statistics(summary):
            return self.build_preserved_full_summary(user, summary, latest_video)
        return self.build_partial_summary(user, latest_video)

    def build_video_duration_summary(self, user, videos):
        if not videos:
            return self.build_empty_summary(user)

        total_videos = len(videos)
        short_count = sum(1 for video in videos if video["duration_category"] == SHORT_VIDEO_LABEL)
        medium_count = sum(1 for video in videos if video["duration_category"] == MEDIUM_VIDEO_LABEL)
        medium_long_count = sum(
            1 for video in videos if video["duration_category"] == MEDIUM_LONG_VIDEO_LABEL
        )
        long_count = sum(1 for video in videos if video["duration_category"] == LONG_VIDEO_LABEL)
        total_duration_seconds = sum(video["duration_seconds"] for video in videos)
        total_like_count = sum(int(video.get("like_count") or 0) for video in videos)
        average_duration_seconds = int(total_duration_seconds / total_videos) if total_videos else 0
        latest_publish_timestamp = max(
            (normalize_timestamp(video.get("publish_timestamp")) for video in videos),
            default=0,
        )

        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "follower_count": user.get("follower_count"),
            "total_favorited": user.get("total_favorited", ""),
            "total_videos": total_videos,
            "latest_publish_timestamp": latest_publish_timestamp,
            "total_duration_seconds": total_duration_seconds,
            "average_duration_seconds": average_duration_seconds,
            "average_duration_text": seconds_to_duration_text(average_duration_seconds),
            "average_like_count": self.calculate_average_like_from_profile(
                user,
                int(total_like_count / total_videos) if total_videos else 0,
            ),
            "average_update_interval_days": calculate_average_update_interval_days(
                video.get("publish_timestamp") for video in videos
            ),
            "short_video_count": short_count,
            "short_video_ratio": format_ratio(short_count, total_videos),
            "medium_video_count": medium_count,
            "medium_video_ratio": format_ratio(medium_count, total_videos),
            "medium_long_video_count": medium_long_count,
            "medium_long_video_ratio": format_ratio(medium_long_count, total_videos),
            "long_video_count": long_count,
            "long_video_ratio": format_ratio(long_count, total_videos),
            "summary_scope": "full",
        }

    @staticmethod
    def _format_output_summary(paths):
        return "、".join(path.name for path in paths if path)

    @staticmethod
    def _sort_days_since_value(item):
        try:
            return int(item.get("days_since_update") or 0)
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _format_cached_at(cached_at):
        normalized = normalize_timestamp(cached_at)
        if not normalized:
            return ""
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(normalized))

    def infer_cache_modes(self, entry, has_followings_cache=False):
        modes = set()
        if has_followings_cache:
            modes.add("counts")
        if not isinstance(entry, dict):
            return modes

        explicit_modes = entry.get("cache_modes")
        if isinstance(explicit_modes, list):
            for mode in explicit_modes:
                if isinstance(mode, str) and mode.strip():
                    modes.add(mode.strip().lower())

        last_fetch_mode = entry.get("last_fetch_mode")
        if isinstance(last_fetch_mode, str) and last_fetch_mode.strip():
            modes.add(last_fetch_mode.strip().lower())

        if entry.get("latest_video") or entry.get("videos"):
            modes.add("monitor")

        summary = entry.get("summary")
        if self.summary_has_complete_statistics(summary):
            modes.add("full")

        return {mode for mode in modes if mode in {"counts", "monitor", "delta", "full"}}

    def build_cached_user(self, followings_by_uid, progress, uid):
        following_user = dict(followings_by_uid.get(uid) or {})
        progress_entry = progress.get(uid) if isinstance(progress, dict) else None
        cached_user = {}
        if isinstance(progress_entry, dict) and isinstance(progress_entry.get("user"), dict):
            cached_user = dict(progress_entry.get("user") or {})

        user = {}
        user.update(cached_user)
        user.update({key: value for key, value in following_user.items() if value not in (None, "")})
        user.setdefault("sec_uid", uid)
        user.setdefault("nickname", cached_user.get("nickname") or following_user.get("nickname") or uid)
        user.setdefault("homepage", cached_user.get("homepage") or following_user.get("homepage") or f"https://www.douyin.com/user/{uid}")
        user.setdefault("remark_name", following_user.get("remark_name", cached_user.get("remark_name", "")))
        user.setdefault("follower_count", following_user.get("follower_count", cached_user.get("follower_count")))
        user.setdefault("aweme_count", following_user.get("aweme_count", cached_user.get("aweme_count")))
        user.setdefault("total_favorited", following_user.get("total_favorited", cached_user.get("total_favorited")))
        user.setdefault(
            "latest_publish_timestamp",
            following_user.get("latest_publish_timestamp", cached_user.get("latest_publish_timestamp")),
        )
        return user

    def build_cache_inventory_rows(self, followings_payload, progress):
        followings = []
        followings_cached_at = ""
        if isinstance(followings_payload, dict):
            followings = followings_payload.get("followings", []) or []
            followings_cached_at = self._format_cached_at(followings_payload.get("cached_at"))

        followings_by_uid = {
            user.get("sec_uid"): user
            for user in followings
            if isinstance(user, dict) and user.get("sec_uid")
        }
        all_uids = sorted(set(followings_by_uid) | set(progress.keys()))
        rows = []

        for uid in all_uids:
            entry = progress.get(uid) if isinstance(progress, dict) else None
            user = self.build_cached_user(followings_by_uid, progress, uid)
            latest_video = self.get_latest_video_from_entry(entry)
            summary = entry.get("summary", {}) if isinstance(entry, dict) else {}
            cache_modes = sorted(self.infer_cache_modes(entry, has_followings_cache=uid in followings_by_uid))

            rows.append(
                {
                    "uploader_name": user.get("nickname", uid),
                    "following_remark": user.get("remark_name", ""),
                    "uploader_id": uid,
                    "uploader_homepage": user.get("homepage", ""),
                    "follower_count": user.get("follower_count", ""),
                    "total_favorited": user.get("total_favorited", ""),
                    "published_video_count": user.get("aweme_count", ""),
                    "cache_modes": ",".join(cache_modes),
                    "last_fetch_mode": (entry.get("last_fetch_mode") if isinstance(entry, dict) else "") or "",
                    "has_counts_cache": "是" if uid in followings_by_uid else "",
                    "has_monitor_cache": "是" if "monitor" in cache_modes else "",
                    "has_delta_cache": "是" if "delta" in cache_modes else "",
                    "has_full_cache": "是" if "full" in cache_modes else "",
                    "has_followings_cache": "是" if uid in followings_by_uid else "",
                    "followings_cache_saved_at": followings_cached_at if uid in followings_by_uid else "",
                    "has_progress_cache": "是" if isinstance(entry, dict) else "",
                    "progress_cached_at": self._format_cached_at(entry.get("cached_at")) if isinstance(entry, dict) else "",
                    "summary_scope": (summary.get("summary_scope") if isinstance(summary, dict) else "") or "",
                    "cached_video_count": len(entry.get("videos", []) or []) if isinstance(entry, dict) else 0,
                    "has_latest_video_cache": "是" if latest_video else "",
                    "latest_video_title": latest_video.get("video_title", "") if latest_video else "",
                    "latest_publish_date": latest_video.get("publish_date", "") if latest_video else "",
                    "latest_publish_timestamp": normalize_timestamp(latest_video.get("publish_timestamp")) if latest_video else "",
                }
            )

        return rows

    def build_cached_snapshot(self):
        followings_payload = self.cache_store.load_followings_cache_payload()
        progress = self.cache_store.load_progress()
        followings = followings_payload.get("followings", []) if isinstance(followings_payload, dict) else []
        followings_by_uid = {
            user.get("sec_uid"): user
            for user in followings
            if isinstance(user, dict) and user.get("sec_uid")
        }
        # 主表/分析表的缓存重建应只基于“当前仍在关注列表中的博主”。
        # 旧 progress 里残留的历史博主只保留给缓存清单诊断，不再带入飞书主表。
        all_uids = set(followings_by_uid)
        if not all_uids:
            return [], [], []

        merged_users = [
            self.build_cached_user(followings_by_uid, progress, uid)
            for uid in all_uids
        ]
        ordered_users = self.sort_followings_by_follower_count(merged_users)

        results = []
        summary_rows = []
        for user in ordered_users:
            uid = user.get("sec_uid")
            entry = progress.get(uid)
            latest_video = self.get_latest_video_from_entry(entry)
            if isinstance(entry, dict):
                videos = entry.get("videos", []) or []
                summary = entry.get("summary", self.build_empty_summary(user))
                if not isinstance(summary, dict):
                    summary = self.build_empty_summary(user)
                summary = dict(summary)
                summary["uploader_name"] = user["nickname"]
                summary["uploader_id"] = user["sec_uid"]
                summary["follower_count"] = user.get("follower_count")
                summary["total_favorited"] = user.get("total_favorited", summary.get("total_favorited", ""))
                if user.get("aweme_count") not in (None, ""):
                    summary["total_videos"] = user.get("aweme_count")
                summary = self.normalize_summary_for_mode(user, summary, videos, latest_video)
                if latest_video:
                    result = self.build_result_item(user, summary, latest_video)
                else:
                    result = self.build_counts_only_result_item(user)
            else:
                summary = self.build_counts_only_summary(user)
                result = self.build_counts_only_result_item(user)

            self.cache_store.refresh_result_runtime_fields(result)
            results.append(result)
            if self.should_export_summary_analysis():
                summary_rows.append(summary)

        cache_rows = self.build_cache_inventory_rows(followings_payload, progress)
        return results, summary_rows, cache_rows

    def export_cached_snapshot(self):
        results, summary_rows, cache_rows = self.build_cached_snapshot()
        if not results and not cache_rows:
            return False

        results = sorted(
            [dict(item) for item in results],
            key=self._sort_days_since_value,
            reverse=True,
        )
        save_to_csv(self.config, results)
        if self.should_export_summary_analysis():
            save_video_duration_analysis_to_csv(self.config, summary_rows)
        save_cache_inventory_to_csv(self.config, cache_rows)
        return bool(results)

    def display_top_results(self, results):
        table = create_table(
            "🏆 抖音断更排行榜 Top 10",
            [
                ("排名", "right", "bold"),
                ("博主", "left"),
                ("断更天数", "right"),
                ("粉丝数", "right"),
                ("视频数", "right"),
                ("平均点赞", "right"),
                ("平均几天一更", "right"),
                ("最新发布日期", "left"),
            ],
        )

        for index, result in enumerate(results[:10], 1):
            average_update_interval_days = result.get("average_update_interval_days")
            average_update_text = (
                f"{average_update_interval_days:.2f}"
                if isinstance(average_update_interval_days, (int, float))
                else "暂无"
            )
            table.add_row(
                str(index),
                str(result["uploader_name"]),
                str(result["days_since_update"]),
                str(result.get("follower_count") or "暂无"),
                str(result.get("published_video_count", 0)),
                str(result.get("average_like_count", 0)),
                average_update_text,
                str(result.get("upload_date", UNKNOWN_DATE)),
            )

        get_console().print()
        get_console().print(table)
        get_console().print()

    def display_counts_results(self, results):
        table = create_table(
            "📋 抖音基础监控 Top 10",
            [
                ("排名", "right", "bold"),
                ("博主", "left"),
                ("粉丝数", "right"),
                ("视频数", "right"),
                ("主页", "left"),
            ],
        )

        for index, result in enumerate(results[:10], 1):
            table.add_row(
                str(index),
                str(result["uploader_name"]),
                str(result.get("follower_count") or "暂无"),
                str(result.get("published_video_count", 0)),
                str(result["uploader_homepage"]),
            )

        get_console().print()
        get_console().print(table)
        get_console().print()

    def flush_partial_outputs(
        self,
        results,
        all_video_rows,
        summary_rows,
        progress,
        pending_progress_saves,
        processed_count,
    ):
        if pending_progress_saves:
            self.cache_store.save_progress(progress)

        snapshot_results = sorted(
            [dict(item) for item in results],
            key=self._sort_days_since_value,
            reverse=True,
        )
        save_to_csv(self.config, snapshot_results)

        if self.should_export_summary_analysis():
            save_video_duration_analysis_to_csv(self.config, list(summary_rows))
        if self.should_export_duration_analysis():
            save_all_videos_to_csv(self.config, list(all_video_rows))
            save_video_duration_report(self.config, list(summary_rows), len(all_video_rows))

        local_outputs = [self.config.output_csv]
        if self.should_export_summary_analysis():
            local_outputs.append(self.config.video_duration_analysis_csv)
        if self.should_export_duration_analysis():
            local_outputs.extend([self.config.all_videos_csv, self.config.video_duration_report_md])

        get_console().print(
            create_summary_panel(
                "💾 本地阶段保存",
                [
                    f"已安全保存到本地: 已处理 {processed_count} 位博主",
                    f"已更新文件: {self._format_output_summary(local_outputs)}",
                ],
                border_style="green",
            )
        )

        if self.upload_callback is not None:
            get_console().print(
                create_summary_panel(
                    "☁️ 抖音阶段同步",
                    [
                        f"已处理博主: {processed_count}",
                        f"待上传文件: {self._format_output_summary(local_outputs)}",
                    ],
                    border_style="cyan",
                )
            )
            try:
                self.upload_callback(processed_count)
            except Exception as exc:
                print(f"⚠️  阶段性飞书上传失败，但分析会继续执行: {exc}")

    def analyze_hiatus(self):
        self.browser_client.ensure_login()
        fetch_mode = self.get_fetch_mode()
        cached_followings = self.cache_store.load_followings_cache()
        use_followings_cache = (
            fetch_mode != "counts"
            and bool(cached_followings)
            and not self.cache_store.is_followings_cache_expired()
        )

        if use_followings_cache:
            followings = cached_followings
            print(
                f"♻️  已复用 {len(followings)} 位抖音关注列表缓存，"
                f"{self.config.followings_cache_max_age_hours} 小时内不重新刷新关注页。"
            )
        else:
            try:
                followings = self.browser_client.get_followings()
                if followings:
                    self.cache_store.save_followings_cache(followings)
            except Exception as exc:
                if cached_followings:
                    followings = cached_followings
                    print(f"⚠️  刷新抖音关注列表失败，已回退到本地缓存继续分析: {exc}")
                else:
                    raise

        if not followings:
            print("❌ 未能获取到任何抖音关注列表")
            return None

        followings = self.sort_followings_by_follower_count(followings)
        print("📈 已按粉丝数从高到低排序后开始抓取。")

        progress = self.cache_store.load_progress()
        if progress:
            print(f"♻️  已加载 {len(progress)} 条抖音缓存")

        export_duration_analysis = self.should_export_duration_analysis()
        if fetch_mode == "counts":
            print("📇 当前为基础统计模式：只抓取每位博主的粉丝数、获赞总数和发布视频数。")
        elif fetch_mode == "monitor":
            print(f"🪶 当前为轻量监控模式：每位博主只抓最近 {self.config.recent_video_limit} 条作品。")
        elif fetch_mode == "delta":
            print(f"🧩 当前为增量模式：每位博主只抓最近 {self.config.recent_video_limit} 条作品并合并到缓存。")
        else:
            print("📚 当前为全量模式：会抓取博主全部作品，并生成完整时长分析。")
        if self.config.enable_video_duration_analysis and not export_duration_analysis:
            print("⏭️  当前模式已跳过全量视频时长分析导出，以降低风控概率。")

        results = []
        all_video_rows = []
        summary_rows = []
        pending_progress_saves = 0
        refreshed_user_count = 0
        cache_hit_count = 0
        refresh_reason_counts = {
            "missing_entry": 0,
            "expired": 0,
            "missing_summary": 0,
            "aweme_count_changed": 0,
            "latest_publish_timestamp_newer": 0,
        }

        if fetch_mode == "counts":
            with create_progress() as progress_bar:
                task_id = progress_bar.add_task("统计抖音博主基础数据", total=len(followings))
                for index, user in enumerate(followings, 1):
                    entry = progress.get(user.get("sec_uid")) if isinstance(progress, dict) else None
                    if isinstance(entry, dict):
                        cached_user = entry.get("user", {}) if isinstance(entry.get("user"), dict) else {}
                        if user.get("total_favorited") in (None, "") and cached_user.get("total_favorited") not in (None, ""):
                            user["total_favorited"] = cached_user.get("total_favorited")
                    summary = self.build_summary_from_cached_entry(user, entry)
                    result = self.build_result_from_cached_entry(user, entry)
                    results.append(result)
                    if self.should_export_summary_analysis():
                        summary_rows.append(summary)

                    progress_bar.advance(task_id)

                    if (
                        self.config.intermediate_upload_interval_users > 0
                        and index % self.config.intermediate_upload_interval_users == 0
                    ):
                        self.flush_partial_outputs(
                            results,
                            all_video_rows,
                            summary_rows,
                            progress,
                            pending_progress_saves,
                            index,
                        )

            self.display_counts_results(results)
            save_to_csv(self.config, results)
            if self.should_export_summary_analysis():
                save_video_duration_analysis_to_csv(self.config, summary_rows)
            save_cache_inventory_to_csv(
                self.config,
                self.build_cache_inventory_rows(
                    self.cache_store.load_followings_cache_payload(),
                    self.cache_store.load_progress(),
                ),
            )

            exported = [self.config.output_csv, self.config.cache_inventory_csv]
            if self.should_export_summary_analysis():
                exported.append(self.config.video_duration_analysis_csv)
            print(
                f"🗂️  抖音 counts 模式已输出：{self._format_output_summary(exported)}，"
                f"共 {len(results)} 位博主"
            )
            return results

        with create_progress() as progress_bar:
            task_id = progress_bar.add_task("分析抖音博主", total=len(followings))
            for index, user in enumerate(followings, 1):
                entry = progress.get(user["sec_uid"])
                if entry and isinstance(entry.get("user"), dict):
                    user.setdefault("follower_count", entry["user"].get("follower_count"))
                    user.setdefault("aweme_count", entry["user"].get("aweme_count"))
                    user.setdefault("total_favorited", entry["user"].get("total_favorited"))
                    user.setdefault("latest_publish_timestamp", entry["user"].get("latest_publish_timestamp"))

                latest_video = self.get_latest_video_from_entry(entry)
                refresh_needed, refresh_reason = self.cache_store.should_refresh_cache(
                    user,
                    entry,
                    return_reason=True,
                )
                if refresh_needed:
                    if refresh_reason in refresh_reason_counts:
                        refresh_reason_counts[refresh_reason] += 1
                    try:
                        if fetch_mode == "full":
                            videos = self.browser_client.get_all_videos_for_user(user)
                            latest_video = self.get_latest_video_from_videos(videos)
                        else:
                            recent_videos = self.browser_client.get_recent_videos_for_user(
                                user,
                                self.config.recent_video_limit,
                            )
                            latest_video = recent_videos[0] if recent_videos else None
                            if fetch_mode == "delta" and entry:
                                videos = self.merge_videos(entry.get("videos", []), recent_videos)
                            elif entry and entry.get("videos"):
                                videos = entry.get("videos", [])
                            else:
                                videos = recent_videos
                    except DouyinRateLimitError as exc:
                        print(f"⚠️  {user['nickname']} 触发页面级速率限制: {exc}")
                        self.browser_client.restart(self.config.rate_limit_global_cooldown)
                        if entry:
                            videos = entry.get("videos", [])
                            latest_video = self.get_latest_video_from_entry(entry)
                        else:
                            results.append(self.build_fetch_failed_result_item(user))
                            progress_bar.advance(task_id)
                            continue
                    except DouyinServiceError as exc:
                        print(f"⚠️  {user['nickname']} 触发页面级限制: {exc}")
                        self.browser_client.restart(self.config.service_error_global_cooldown)
                        if entry:
                            videos = entry.get("videos", [])
                            latest_video = self.get_latest_video_from_entry(entry)
                        else:
                            results.append(self.build_fetch_failed_result_item(user))
                            progress_bar.advance(task_id)
                            continue
                    except Exception as exc:
                        print(f"⚠️  {user['nickname']} 抓取失败: {exc}")
                        if entry:
                            videos = entry.get("videos", [])
                            latest_video = self.get_latest_video_from_entry(entry)
                        else:
                            results.append(self.build_fetch_failed_result_item(user))
                            progress_bar.advance(task_id)
                            continue

                    if fetch_mode == "full":
                        summary = self.build_video_duration_summary(user, videos)
                    elif entry and isinstance(entry.get("summary"), dict):
                        summary = dict(entry.get("summary") or {})
                        summary["uploader_name"] = user["nickname"]
                        summary["uploader_id"] = user["sec_uid"]
                        summary["follower_count"] = user.get("follower_count")
                        summary["total_favorited"] = user.get("total_favorited", summary.get("total_favorited", ""))
                        if user.get("aweme_count") is not None:
                            summary["total_videos"] = user.get("aweme_count")
                        if latest_video:
                            summary["latest_publish_timestamp"] = normalize_timestamp(
                                latest_video.get("publish_timestamp")
                            )
                    else:
                        summary = self.build_video_duration_summary(user, videos)

                    summary = self.normalize_summary_for_mode(user, summary, videos, latest_video)
                    existing_modes = set()
                    if isinstance(entry, dict) and isinstance(entry.get("cache_modes"), list):
                        existing_modes = {
                            str(mode).strip().lower()
                            for mode in entry.get("cache_modes", [])
                            if str(mode).strip()
                        }
                    if fetch_mode in {"monitor", "delta", "full"}:
                        existing_modes.add(fetch_mode)

                    progress[user["sec_uid"]] = {
                        "cached_at": int(time.time()),
                        "user": user,
                        "videos": videos,
                        "summary": summary,
                        "latest_video": latest_video,
                        "last_fetch_mode": fetch_mode,
                        "cache_modes": sorted(existing_modes),
                    }
                    refreshed_user_count += 1
                    pending_progress_saves += 1

                    if pending_progress_saves >= self.config.progress_save_interval_users:
                        self.cache_store.save_progress(progress)
                        pending_progress_saves = 0

                    if (
                        self.config.refresh_batch_size > 0
                        and refreshed_user_count % self.config.refresh_batch_size == 0
                    ):
                        cooldown = self.config.refresh_batch_cooldown
                        print(
                            f"⏸️  已连续刷新 {refreshed_user_count} 位博主，"
                            f"批次冷却 {cooldown:.0f} 秒后继续..."
                        )
                        wait_with_progress(cooldown, "抖音抓取批次冷却中")

                    if (
                        self.config.browser_restart_interval_users > 0
                        and refreshed_user_count % self.config.browser_restart_interval_users == 0
                    ):
                        print(
                            f"🔧 已刷新 {refreshed_user_count} 位博主，重启浏览器会话以降低后续风控概率..."
                        )
                        self.browser_client.restart(5)
                else:
                    cache_hit_count += 1
                    videos = entry.get("videos", []) if entry else []
                    summary = (
                        entry.get("summary", self.build_empty_summary(user))
                        if entry
                        else self.build_empty_summary(user)
                    )
                    summary = self.normalize_summary_for_mode(user, summary, videos, latest_video)

                if latest_video is None and videos:
                    latest_video = self.get_latest_video_from_videos(videos)

                if latest_video:
                    result = self.build_result_item(user, summary, latest_video)
                else:
                    result = self.build_no_video_result_item(user)

                self.cache_store.refresh_result_runtime_fields(result)
                results.append(result)

                if self.should_export_summary_analysis():
                    summary_rows.append(summary)
                if export_duration_analysis:
                    all_video_rows.extend(videos)

                progress_bar.advance(task_id)

                if (
                    self.config.intermediate_upload_interval_users > 0
                    and index % self.config.intermediate_upload_interval_users == 0
                ):
                    self.flush_partial_outputs(
                        results,
                        all_video_rows,
                        summary_rows,
                        progress,
                        pending_progress_saves,
                        index,
                    )
                    pending_progress_saves = 0

        if pending_progress_saves:
            self.cache_store.save_progress(progress)

        if fetch_mode != "full":
            refreshed_total = sum(refresh_reason_counts.values())
            get_console().print(
                create_summary_panel(
                    "📦 抖音缓存命中摘要",
                    [
                        f"复用缓存: {cache_hit_count}",
                        f"重新抓取: {refreshed_total}",
                        f"无缓存: {refresh_reason_counts['missing_entry']}",
                        f"缓存过期: {refresh_reason_counts['expired']}",
                        f"摘要缺失: {refresh_reason_counts['missing_summary']}",
                        f"视频数变化: {refresh_reason_counts['aweme_count_changed']}",
                        f"最新发布时间变新: {refresh_reason_counts['latest_publish_timestamp_newer']}",
                    ],
                    border_style="blue",
                )
            )

        results.sort(key=self._sort_days_since_value, reverse=True)
        self.display_top_results(results)
        save_to_csv(self.config, results)

        if self.should_export_summary_analysis():
            save_video_duration_analysis_to_csv(self.config, summary_rows)
        save_cache_inventory_to_csv(
            self.config,
            self.build_cache_inventory_rows(
                self.cache_store.load_followings_cache_payload(),
                progress,
            ),
        )
        if export_duration_analysis:
            save_all_videos_to_csv(self.config, all_video_rows)
            save_video_duration_report(self.config, summary_rows, len(all_video_rows))

        exported = [self.config.output_csv, self.config.cache_inventory_csv]
        if self.should_export_summary_analysis():
            exported.append(self.config.video_duration_analysis_csv)
        if export_duration_analysis:
            exported.extend([self.config.all_videos_csv, self.config.video_duration_report_md])
        get_console().print(
            create_summary_panel(
                f"🗂️ 抖音 {fetch_mode} 模式输出",
                [
                    f"输出文件数: {len(exported)}",
                    f"文件列表: {self._format_output_summary(exported)}",
                    f"结果数: {len(results)} 位博主",
                ],
                border_style="green",
            )
        )
        return results
