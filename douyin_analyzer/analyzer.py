import os
import time

from loguru import logger

from common.platform_store import read_video_rows_for_uploader
from common.runtime_control import OperationCancelled, check_stop
from bilibili_analyzer.logging_utils import (
    create_progress,
    create_summary_panel,
    create_table,
    get_console,
    smart_print as print,
    wait_with_progress,
)

from .browser_client import (
    DouyinFullFetchValidationError,
    DouyinRateLimitError,
    DouyinServiceError,
)
from .archive import load_active_archived_uids
from .exporters import (
    save_all_videos_to_csv,
    save_cache_inventory_to_csv,
    save_full_fetch_mismatch_to_csv,
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
    def __init__(self, config, browser_client, cache_store, upload_callback=None, max_followings=None):
        self.config = config
        self.browser_client = browser_client
        self.cache_store = cache_store
        self.upload_callback = upload_callback
        try:
            self.max_followings = int(max_followings) if max_followings is not None else None
        except (TypeError, ValueError):
            self.max_followings = None
        if self.max_followings is not None and self.max_followings <= 0:
            self.max_followings = None

    FETCH_ORDER_LABELS = {
        "follower_count": "\u7c89\u4e1d\u6570",
        "published_video_count": "\u89c6\u9891\u603b\u6570",
        "total_favorited": "\u83b7\u8d5e\u603b\u6570",
        "average_like_count": "\u5e73\u5747\u70b9\u8d5e\u6570",
    }

    @staticmethod
    def _env_flag(name, default=True):
        value = os.getenv(name)
        if value is None:
            return default
        return value.strip().lower() in {"1", "true", "yes", "on"}

    def get_following_fetch_order(self):
        field = str(os.getenv("DOUYIN_FETCH_ORDER_BY", "follower_count") or "follower_count").strip()
        if field not in self.FETCH_ORDER_LABELS:
            field = "follower_count"
        descending = self._env_flag("DOUYIN_FETCH_ORDER_DESC", True)
        return field, descending, self.FETCH_ORDER_LABELS[field]

    def _resolve_following_sort_value(self, user, field):
        user = user if isinstance(user, dict) else {}
        if field == "published_video_count":
            return self._safe_int(user.get("aweme_count") or user.get("published_video_count") or user.get("total_videos"), 0)
        if field == "total_favorited":
            return self._safe_int(user.get("total_favorited"), 0)
        if field == "average_like_count":
            return self._safe_int(self.calculate_average_like_from_profile(user, 0), 0)
        return self._safe_int(user.get("follower_count"), 0)

    def sort_followings_by_follower_count(self, followings):
        field, descending, _label = self.get_following_fetch_order()

        def sort_key(user):
            user = user if isinstance(user, dict) else {}
            value = self._resolve_following_sort_value(user, field)
            nickname = str(user.get("nickname") or "")
            uid = str(user.get("sec_uid") or "")
            primary = -value if descending else value
            return (primary, nickname, uid)

        return sorted(followings or [], key=sort_key)

    def get_fetch_mode(self):
        if self.config.fetch_mode in {"counts", "verify", "monitor", "delta", "full"}:
            return self.config.fetch_mode
        return "monitor"

    @staticmethod
    def modes_requiring_basic_cache():
        return {"verify", "monitor", "delta", "full"}

    @staticmethod
    def entry_has_full_cache(entry):
        if not isinstance(entry, dict):
            return False
        summary = entry.get("summary") if isinstance(entry.get("summary"), dict) else {}
        summary_scope = str(summary.get("summary_scope") or "").strip().lower()
        if entry.get("full_status_reset") or summary_scope == "status_reset":
            return False

        cache_modes = entry.get("cache_modes")
        if isinstance(cache_modes, str):
            cache_modes = cache_modes.split(",")
        if isinstance(cache_modes, list):
            for mode in cache_modes:
                if str(mode or "").strip().lower() == "full":
                    return True

        return (
            str(entry.get("last_fetch_mode") or "").strip().lower() == "full"
            or summary_scope in {"full", "preserved_full"}
        )

    def should_preserve_full_cache(self, entry):
        return self.get_fetch_mode() != "full" and self.entry_has_full_cache(entry)

    @staticmethod
    def preserve_full_progress_entry(entry, observed_mode=None):
        preserved = dict(entry or {})
        raw_modes = preserved.get("cache_modes", [])
        if isinstance(raw_modes, str):
            raw_modes = raw_modes.split(",")
        modes = {
            str(mode or "").strip().lower()
            for mode in raw_modes
            if str(mode or "").strip()
        }
        modes.add("full")
        if observed_mode in {"counts", "verify", "monitor", "delta"}:
            modes.add(observed_mode)
        preserved["cache_modes"] = sorted(modes)
        preserved["last_fetch_mode"] = "full"
        return preserved

    def merge_updated_followings_cache(self, original_followings, updated_users):
        updates = {
            str((user or {}).get("sec_uid") or "").strip(): user
            for user in (updated_users or [])
            if isinstance(user, dict) and str((user or {}).get("sec_uid") or "").strip()
        }
        if not updates:
            return

        merged = []
        seen = set()
        for item in original_followings or []:
            if not isinstance(item, dict):
                merged.append(item)
                continue
            uid = str(item.get("sec_uid") or "").strip()
            if uid in updates:
                merged.append({**item, **updates[uid]})
                seen.add(uid)
            else:
                merged.append(item)

        for uid, user in updates.items():
            if uid not in seen:
                merged.append(user)

        self.cache_store.save_followings_cache(merged)

    @staticmethod
    def _is_following_integrity_error(error):
        message = str(error or "")
        integrity_markers = (
            "主页显示关注",
            "实际仅抓取到",
            "但主接口抓取到",
            "关注列表抓取异常",
            "关注列表抓取失败",
            "关注列表页未成功打开",
            "没有检测到关注列表面板",
        )
        return any(marker in message for marker in integrity_markers)

    @staticmethod
    def _is_full_fetch_validation_error(error):
        message = str(error or "")
        validation_markers = (
            "全量抓取作品数量校验失败",
            "全量抓取未滚动到底",
            "全量抓取未检测到底部结束标记",
            "页面未出现“暂时没有更多了”",
        )
        return any(marker in message for marker in validation_markers)

    @staticmethod
    def build_full_fetch_mismatch_row(user, error, retry_count):
        expected_count = 0
        actual_count = 0
        no_more_marker_seen = ""
        if isinstance(error, DouyinFullFetchValidationError):
            expected_count = int(error.expected_count or 0)
            actual_count = int(error.actual_count or 0)
            no_more_marker_seen = "是" if error.no_more_marker_seen else ""
        return {
            "uploader_name": (user or {}).get("nickname", ""),
            "uploader_id": (user or {}).get("sec_uid", ""),
            "uploader_homepage": (user or {}).get("homepage", ""),
            "expected_video_count": expected_count,
            "actual_video_count": actual_count,
            "retry_count": int(retry_count or 0),
            "no_more_marker_seen": no_more_marker_seen,
            "last_error": str(error or ""),
            "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

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

    def is_zero_video_candidate(self, user):
        return isinstance(user, dict) and self._safe_int(user.get("aweme_count"), -1) == 0

    def is_empty_video_profile_confirmed(self, user):
        return self.is_zero_video_candidate(user) and bool((user or {}).get("_empty_video_page_confirmed"))

    @staticmethod
    def _remove_user_from_memory_collection(collection, user):
        if not isinstance(collection, list) or not isinstance(user, dict):
            return

        target_uid = str(user.get("sec_uid") or "").strip()
        target_homepage = str(user.get("homepage") or "").strip()
        kept_items = []
        for item in collection:
            if not isinstance(item, dict):
                kept_items.append(item)
                continue
            current_uid = str(item.get("sec_uid") or "").strip()
            current_homepage = str(item.get("homepage") or "").strip()
            if (target_uid and current_uid == target_uid) or (target_homepage and current_homepage == target_homepage):
                continue
            kept_items.append(item)
        collection[:] = kept_items

    def handle_empty_video_profile(self, user, progress=None, cached_followings=None, verified_users=None):
        if not self.is_empty_video_profile_confirmed(user):
            return False
        if self.browser_client is None:
            return False

        nickname = str((user or {}).get("nickname") or (user or {}).get("sec_uid") or "unknown")
        homepage = self.browser_client.normalize_homepage_url((user or {}).get("homepage", ""))
        if not homepage:
            return False

        print(f"🧹 {nickname} 主页已确认作品清空，立即执行取消关注并清理缓存。")
        try:
            unfollow_result = self.browser_client.unfollow_user_by_homepage(homepage)
        except Exception as exc:
            print(f"⚠️  {nickname} 自动取消关注失败，暂不清理缓存: {exc}")
            logger.warning(
                "Douyin auto unfollow failed for empty profile | uid={} | homepage={} | error={}",
                user.get("sec_uid"),
                homepage,
                exc,
            )
            return False

        status = str((unfollow_result or {}).get("status") or "")
        if status not in {"unfollowed", "skipped"}:
            print(f"⚠️  {nickname} 自动取消关注未成功，暂不清理缓存: {unfollow_result}")
            logger.warning(
                "Douyin auto unfollow did not complete for empty profile | uid={} | homepage={} | result={}",
                user.get("sec_uid"),
                homepage,
                unfollow_result,
            )
            return False

        removed_uids = self.cache_store.remove_unfollowed_user(
            homepage=homepage,
            uploader_id=(user or {}).get("sec_uid", ""),
        )
        if isinstance(progress, dict):
            progress.pop(str((user or {}).get("sec_uid") or "").strip(), None)
        self._remove_user_from_memory_collection(cached_followings, user)
        self._remove_user_from_memory_collection(verified_users, user)

        logger.info(
            "Douyin empty profile auto unfollowed and cache cleared | uid={} | removed_uids={}",
            user.get("sec_uid"),
            removed_uids,
        )
        print(
            f"✅ {nickname} 已完成自动取消关注，并清理本地缓存"
            f"{'：' + ', '.join(removed_uids) if removed_uids else ''}"
        )
        return True

    def calculate_average_like_from_profile(self, user, fallback=""):
        total_favorited = self._safe_int((user or {}).get("total_favorited"), 0)
        published_video_count = self._safe_int((user or {}).get("aweme_count"), 0)
        if total_favorited > 0 and published_video_count > 0:
            return int(total_favorited / published_video_count)
        return fallback

    @staticmethod
    def summary_has_precise_public_like_total(summary):
        if not isinstance(summary, dict):
            return False
        summary_scope = str(summary.get("summary_scope") or "").strip().lower()
        total_favorited_source = str(summary.get("total_favorited_source") or "").strip().lower()
        return (
            summary_scope in {"full", "preserved_full"}
            and total_favorited_source == "public_video_like_sum"
            and summary.get("total_favorited") not in (None, "")
        )

    @staticmethod
    def sum_public_video_likes(videos):
        if not isinstance(videos, list):
            return None

        total_like_count = 0
        has_like_value = False
        for video in videos:
            if not isinstance(video, dict):
                continue
            like_count = video.get("like_count")
            if like_count in (None, ""):
                continue
            try:
                total_like_count += int(like_count)
                has_like_value = True
            except (TypeError, ValueError):
                continue

        return total_like_count if has_like_value else None

    def apply_precise_public_like_total_from_videos(self, user, summary, videos):
        if not isinstance(summary, dict):
            return summary

        if not self.has_complete_video_sample(user, videos):
            return summary

        total_like_count = self.sum_public_video_likes(videos)
        if total_like_count is None:
            return summary

        summary["total_favorited"] = total_like_count
        summary["total_favorited_source"] = "public_video_like_sum"
        summary["total_videos"] = len(videos)
        if videos:
            summary["average_like_count"] = int(total_like_count / len(videos))
        return summary

    def resolve_total_favorited(self, user, summary=None):
        user_total_favorited = (user or {}).get("total_favorited", "")
        if self.summary_has_precise_public_like_total(summary):
            return self._safe_int(summary.get("total_favorited"), summary.get("total_favorited"))
        if isinstance(summary, dict):
            summary_total_favorited = summary.get("total_favorited")
            if self.get_fetch_mode() == "full" and summary_total_favorited not in (None, ""):
                return self._safe_int(summary_total_favorited, summary_total_favorited)
        if user_total_favorited not in (None, ""):
            return self._safe_int(user_total_favorited, user_total_favorited)
        if isinstance(summary, dict):
            summary_total_favorited = summary.get("total_favorited")
            if summary_total_favorited not in (None, ""):
                return self._safe_int(summary_total_favorited, summary_total_favorited)
        return user_total_favorited

    def resolve_published_video_count(self, user, summary=None):
        if self.summary_has_precise_public_like_total(summary):
            summary_total_videos = (summary or {}).get("total_videos")
            if summary_total_videos not in (None, ""):
                return self._safe_int(summary_total_videos, summary_total_videos)
        user_aweme_count = (user or {}).get("aweme_count")
        if user_aweme_count not in (None, ""):
            return self._safe_int(user_aweme_count, user_aweme_count)
        if isinstance(summary, dict):
            summary_total_videos = summary.get("total_videos")
            if summary_total_videos not in (None, ""):
                return self._safe_int(summary_total_videos, summary_total_videos)
        return user_aweme_count

    def resolve_average_like_count(self, user, summary=None, fallback=""):
        total_favorited = self.resolve_total_favorited(user, summary)
        video_count = 0
        if self.summary_has_precise_public_like_total(summary):
            video_count = self._safe_int((summary or {}).get("total_videos"), 0)
        if video_count <= 0:
            video_count = self._safe_int((user or {}).get("aweme_count"), 0)
        if video_count <= 0 and isinstance(summary, dict):
            video_count = self._safe_int(summary.get("total_videos"), 0)

        total_favorited_int = self._safe_int(total_favorited, 0)
        if total_favorited_int > 0 and video_count > 0:
            return int(total_favorited_int / video_count)

        if isinstance(summary, dict):
            average_like_count = summary.get("average_like_count")
            if average_like_count not in (None, ""):
                return average_like_count
        return self.calculate_average_like_from_profile(user, fallback)

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
        published_video_count = self.resolve_published_video_count(user, summary)
        data_source = "douyin_video_api" if self.get_fetch_mode() == "full" else "douyin_recent_video_api"
        return {
            "uploader_name": user["nickname"],
            "following_remark": user.get("remark_name", ""),
            "uploader_id": user["sec_uid"],
            "uploader_homepage": user["homepage"],
            "following_group_names": DEFAULT_GROUP_NAME,
            "follower_count": user.get("follower_count"),
            "total_favorited": self.resolve_total_favorited(user, summary),
            "published_video_count": published_video_count,
            "average_like_count": self.resolve_average_like_count(
                user,
                summary,
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
        complete_stored_videos = self.load_complete_stored_videos(user, cached_summary, cached_videos)
        if complete_stored_videos:
            cached_videos = complete_stored_videos

        if self.summary_has_complete_statistics(cached_summary):
            summary = dict(cached_summary or {})
            summary = self.apply_precise_public_like_total_from_videos(user, summary, cached_videos)
            summary["uploader_name"] = user.get("nickname", summary.get("uploader_name", ""))
            summary["uploader_id"] = user.get("sec_uid", summary.get("uploader_id", ""))
            summary["follower_count"] = user.get("follower_count", summary.get("follower_count", ""))
            if summary.get("total_favorited") in (None, ""):
                summary["total_favorited"] = user.get("total_favorited", "")
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
            result["total_favorited"] = self.resolve_total_favorited(user, summary)
            result["average_like_count"] = self.resolve_average_like_count(user, summary, "")
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

    def load_complete_stored_videos(self, user, summary=None, current_videos=None):
        uid = str((user or {}).get("sec_uid") or (summary or {}).get("uploader_id") or "").strip()
        if not uid:
            return []

        expected_count = self._safe_int((user or {}).get("aweme_count"), 0)
        if expected_count <= 0 and isinstance(summary, dict):
            expected_count = self._safe_int(summary.get("total_videos"), 0)
        if expected_count <= 0:
            return []

        if isinstance(current_videos, list) and len(current_videos) >= expected_count:
            return current_videos

        stored_videos = read_video_rows_for_uploader(self.config.export_store_db, "douyin", uid)
        if len(stored_videos) >= expected_count:
            logger.info(
                "Douyin precise likes recovered from stored videos | uid={} | videos={} | expected={}",
                uid,
                len(stored_videos),
                expected_count,
            )
            return stored_videos
        return []

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
            "total_favorited",
            "total_favorited_source",
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
            return self.apply_precise_public_like_total_from_videos(user, summary, videos)
        if self.summary_has_precise_public_like_total(summary):
            return self.build_preserved_full_summary(user, summary, latest_video)
        return self.build_partial_summary(user, latest_video)

    def build_video_duration_summary(self, user, videos):
        if not videos:
            return self.build_empty_summary(user)

        total_videos = len(videos)
        complete_video_sample = bool((user or {}).get("_full_fetch_validated")) or self.has_complete_video_sample(user, videos)
        short_count = sum(1 for video in videos if video["duration_category"] == SHORT_VIDEO_LABEL)
        medium_count = sum(1 for video in videos if video["duration_category"] == MEDIUM_VIDEO_LABEL)
        medium_long_count = sum(
            1 for video in videos if video["duration_category"] == MEDIUM_LONG_VIDEO_LABEL
        )
        long_count = sum(1 for video in videos if video["duration_category"] == LONG_VIDEO_LABEL)
        total_duration_seconds = sum(video["duration_seconds"] for video in videos)
        total_like_count = self.sum_public_video_likes(videos) or 0
        total_favorited = total_like_count if complete_video_sample else user.get("total_favorited", "")
        average_like_count = (
            int(total_like_count / total_videos)
            if complete_video_sample and total_videos
            else self.calculate_average_like_from_profile(
                user,
                int(total_like_count / total_videos) if total_videos else 0,
            )
        )
        average_duration_seconds = int(total_duration_seconds / total_videos) if total_videos else 0
        latest_publish_timestamp = max(
            (normalize_timestamp(video.get("publish_timestamp")) for video in videos),
            default=0,
        )

        return {
            "uploader_name": user["nickname"],
            "uploader_id": user["sec_uid"],
            "follower_count": user.get("follower_count"),
            "total_favorited": total_favorited,
            "total_favorited_source": "public_video_like_sum" if complete_video_sample else "profile_total",
            "total_videos": total_videos,
            "latest_publish_timestamp": latest_publish_timestamp,
            "total_duration_seconds": total_duration_seconds,
            "average_duration_seconds": average_duration_seconds,
            "average_duration_text": seconds_to_duration_text(average_duration_seconds),
            "average_like_count": average_like_count,
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
            "summary_scope": "full" if complete_video_sample else "partial",
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

    def _format_progress_expires_at(self, cached_at):
        normalized = normalize_timestamp(cached_at)
        if not normalized:
            return ""
        expires_at = normalized + int(self.config.precise_cache_max_age_hours) * 3600
        return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(expires_at))

    def _is_progress_cache_due(self, cached_at):
        normalized = normalize_timestamp(cached_at)
        if not normalized:
            return "是"
        expires_at = normalized + int(self.config.precise_cache_max_age_hours) * 3600
        return "是" if time.time() >= expires_at else ""

    def infer_cache_modes(self, entry, has_followings_cache=False):
        modes = set()
        if has_followings_cache:
            modes.add("counts")
        if not isinstance(entry, dict):
            return modes
        summary = entry.get("summary")
        summary_scope = ""
        if isinstance(summary, dict):
            summary_scope = str(summary.get("summary_scope") or "").strip().lower()
        if entry.get("full_status_reset") or summary_scope == "status_reset":
            explicit_modes = entry.get("cache_modes")
            if isinstance(explicit_modes, list):
                for mode in explicit_modes:
                    mode_text = str(mode or "").strip().lower()
                    if mode_text and mode_text != "full":
                        modes.add(mode_text)
            if entry.get("latest_video") or entry.get("videos"):
                modes.add("monitor")
            return {mode for mode in modes if mode in {"counts", "verify", "monitor", "delta", "full"}}

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

        if self.entry_has_full_cache(entry):
            modes.add("full")

        return {mode for mode in modes if mode in {"counts", "verify", "monitor", "delta", "full"}}

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
            last_fetch_mode = (
                "full"
                if isinstance(entry, dict) and self.entry_has_full_cache(entry)
                else ((entry.get("last_fetch_mode") if isinstance(entry, dict) else "") or "")
            )
            cached_videos = entry.get("videos", []) if isinstance(entry, dict) else []
            cached_video_count = len(cached_videos or []) if isinstance(cached_videos, list) else 0
            if isinstance(entry, dict) and self.entry_has_full_cache(entry):
                complete_stored_videos = self.load_complete_stored_videos(user, summary, cached_videos)
                if complete_stored_videos:
                    cached_video_count = max(cached_video_count, len(complete_stored_videos))

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
                    "last_fetch_mode": last_fetch_mode,
                    "has_counts_cache": "是" if uid in followings_by_uid else "",
                    "has_verify_cache": "是" if "verify" in cache_modes else "",
                    "has_monitor_cache": "是" if "monitor" in cache_modes else "",
                    "has_delta_cache": "是" if "delta" in cache_modes else "",
                    "has_full_cache": "是" if "full" in cache_modes else "",
                    "has_followings_cache": "是" if uid in followings_by_uid else "",
                    "followings_cache_saved_at": followings_cached_at if uid in followings_by_uid else "",
                    "has_progress_cache": "是" if isinstance(entry, dict) else "",
                    "progress_cached_at": self._format_cached_at(entry.get("cached_at")) if isinstance(entry, dict) else "",
                    "progress_cache_expires_at": (
                        self._format_progress_expires_at(entry.get("cached_at")) if isinstance(entry, dict) else ""
                    ),
                    "progress_cache_due": (
                        self._is_progress_cache_due(entry.get("cached_at")) if isinstance(entry, dict) else "是"
                    ),
                    "summary_scope": (summary.get("summary_scope") if isinstance(summary, dict) else "") or "",
                    "cached_video_count": cached_video_count,
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
                summary = self.build_summary_from_cached_entry(user, entry)
                if latest_video:
                    result = self.build_result_item(user, summary, latest_video)
                else:
                    result = self.build_counts_only_result_item(user)
                    result["total_favorited"] = self.resolve_total_favorited(user, summary)
                    result["average_like_count"] = self.resolve_average_like_count(user, summary, "")
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
        merge_existing=False,
    ):
        if pending_progress_saves:
            self.cache_store.save_progress(progress)

        if not results and not summary_rows and not all_video_rows:
            return

        snapshot_results = sorted(
            [dict(item) for item in results],
            key=self._sort_days_since_value,
            reverse=True,
        )
        save_to_csv(self.config, snapshot_results, merge_existing=merge_existing)

        if self.should_export_summary_analysis():
            save_video_duration_analysis_to_csv(
                self.config,
                list(summary_rows),
                merge_existing=merge_existing,
            )
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

    def append_fetch_manifest_record(
        self,
        user,
        fetch_status,
        video_count=0,
        latest_video=None,
        result=None,
        refresh_reason="",
        message="",
    ):
        latest_video = latest_video if isinstance(latest_video, dict) else {}
        result = result if isinstance(result, dict) else {}
        self.cache_store.append_fetch_manifest(
            {
                "mode": self.get_fetch_mode(),
                "uploader_id": (user or {}).get("sec_uid") or result.get("uploader_id") or "",
                "uploader_name": (user or {}).get("nickname") or result.get("uploader_name") or "",
                "homepage": (user or {}).get("homepage") or result.get("uploader_homepage") or "",
                "fetch_status": fetch_status,
                "refresh_reason": refresh_reason or "",
                "video_count": video_count,
                "latest_video_id": latest_video.get("aweme_id") or latest_video.get("video_id") or "",
                "latest_publish_timestamp": normalize_timestamp(latest_video.get("publish_timestamp")),
                "result_source": result.get("data_source") or "",
                "message": message or "",
            }
        )

    def failed_profile_key_matches(self, user, failed_keys):
        if not failed_keys:
            return False
        uid = str((user or {}).get("sec_uid") or "").strip()
        homepage = self.cache_store._normalize_homepage_url((user or {}).get("homepage", ""))
        return (uid and f"uid:{uid}" in failed_keys) or (
            homepage and f"homepage:{homepage}" in failed_keys
        )

    def build_failed_profile_skipped_result_item(self, user):
        result = self.build_fetch_failed_result_item(user)
        result["data_source"] = "failed_profile_skipped"
        return result

    def record_profile_failure(self, user, exc, stage):
        self.cache_store.append_failed_profile(
            user,
            reason=str(exc),
            stage=stage,
            mode=self.get_fetch_mode(),
        )
        self.append_fetch_manifest_record(
            user,
            "failed",
            video_count=0,
            refresh_reason=stage,
            message=str(exc),
        )

    def analyze_hiatus(self):
        self.browser_client.ensure_login()
        fetch_mode = self.get_fetch_mode()
        cached_followings = self.cache_store.load_followings_cache()
        logger.info(
            "Douyin analysis start | mode={} | cached_followings={} | intermediate_upload_interval={}",
            fetch_mode,
            len(cached_followings or []),
            self.config.intermediate_upload_interval_users,
        )
        monitor_refresh_followings = fetch_mode == "monitor"
        enable_profile_change_refresh = fetch_mode == "monitor"
        # 关注列表决定“当前仍在关注的博主集合”，因此普通运行也必须优先刷新；
        # 否则用户手动取关后，未过期的旧缓存会让已取关博主持续残留在主表中。
        use_followings_cache = fetch_mode in self.modes_requiring_basic_cache()

        if use_followings_cache and not monitor_refresh_followings:
            if not cached_followings:
                raise RuntimeError("抖音非基础模式需要先运行一次基础统计模式，生成关注列表缓存和 UP 主页链接。")
            followings = cached_followings
            print(
                f"♻️  已复用 {len(followings)} 位抖音关注列表缓存，"
                "本模式不重新滚动关注列表，将直接按缓存主页链接进入主页。"
            )
            logger.info(
                "Douyin basic followings cache reused | mode={} | cached_rows={}",
                fetch_mode,
                len(followings),
            )
        elif use_followings_cache and monitor_refresh_followings:
            if not cached_followings:
                raise RuntimeError("抖音监控模式需要先运行一次基础统计模式，生成关注列表缓存和 UP 主页链接。")
            try:
                print(
                    f"🧭 监控模式将先刷新关注缓存，用于比较最新发布时间 | 本地缓存={len(cached_followings)} 条"
                )
                followings = self.browser_client.get_followings()
                if followings:
                    self.cache_store.save_followings_cache(followings)
                    cached_followings = followings
                    print(
                        f"🧭 监控模式关注缓存已刷新 | 已写入缓存={len(followings)} 条 | 后续将据此判断是否需要进主页"
                    )
                    logger.info(
                        "Douyin monitor followings refreshed for publish timestamp compare | rows={}",
                        len(followings),
                    )
                else:
                    followings = cached_followings
                    print(
                        f"⚠️  监控模式未拿到新的关注列表数据，已回退本地缓存继续运行 | 缓存条数={len(followings)}"
                    )
                    logger.warning(
                        "Douyin monitor followings refresh returned empty; fallback cached rows={}",
                        len(followings),
                    )
            except Exception as exc:
                if self._is_following_integrity_error(exc):
                    raise
                followings = cached_followings
                print(
                    f"⚠️  监控模式刷新关注缓存失败，已回退本地缓存继续运行 | 缓存条数={len(followings)} | 原因={exc}"
                )
                logger.warning(
                    "Douyin monitor followings refresh failed; fallback cached rows={} | error={}",
                    len(followings),
                    exc,
                )
        else:
            try:
                print(
                    f"🧭 关注列表刷新开始 | 模式={fetch_mode} | "
                    f"本地关注缓存={len(cached_followings or [])} 条"
                )
                followings = self.browser_client.get_followings()
                if followings:
                    self.cache_store.save_followings_cache(followings)
                    print(f"🧭 关注列表刷新成功 | 已写入缓存={len(followings)} 条")
                    logger.info("Douyin followings refreshed | rows={}", len(followings))
            except Exception as exc:
                if self._is_following_integrity_error(exc):
                    print(f"❌ 关注列表完整性校验失败，不回退旧缓存，避免未关注博主重新进入表格: {exc}")
                    logger.error("Douyin followings integrity failed | error={}", exc)
                    raise
                if cached_followings:
                    followings = cached_followings
                    print(f"🧭 关注列表回退缓存 | 缓存条数={len(followings)} | 失败原因={exc}")
                    print(f"⚠️  刷新抖音关注列表失败，已回退到本地缓存继续分析: {exc}")
                    logger.warning(
                        "Douyin followings fallback cache used | cached_rows={} | error={}",
                        len(followings),
                        exc,
                    )
                else:
                    raise

        if not followings:
            print("❌ 未能获取到任何抖音关注列表")
            return None

        progress = self.cache_store.load_progress()
        if progress:
            print(f"♻️  已加载 {len(progress)} 条抖音缓存")

        try:
            archived_uids = load_active_archived_uids(self.config.export_store_db)
        except Exception as exc:
            archived_uids = set()
            logger.warning("Douyin archive status load failed; continue without archive filter | error={}", exc)
        if archived_uids:
            before_archive_filter = len(followings)
            followings = [
                user
                for user in followings
                if not (
                    isinstance(user, dict)
                    and str(user.get("sec_uid") or user.get("uid") or "").strip() in archived_uids
                )
            ]
            skipped_archived = before_archive_filter - len(followings)
            if skipped_archived:
                print(
                    f"🗄️  已跳过 {skipped_archived} 位处于 active 归档状态的长期未更新 UP，"
                    "本轮不再进入主页校验/监控/全量处理。"
                )
                logger.info(
                    "Douyin archived creators skipped | skipped={} | remaining={}",
                    skipped_archived,
                    len(followings),
                )
            if not followings:
                print("🗄️  当前关注列表全部命中 active 归档状态，本轮无需继续处理。")
                return None

        order_field, order_desc, order_label = self.get_following_fetch_order()
        followings = self.sort_followings_by_follower_count(followings)
        total_followings = len(followings)
        partial_run = self.max_followings is not None and self.max_followings < total_followings
        if partial_run:
            if fetch_mode == "counts":
                followings = followings[: self.max_followings]
                print(
                    f"\U0001f9e9 \u90e8\u5206\u6293\u53d6\u6a21\u5f0f\u5df2\u751f\u6548 | \u5168\u90e8\u5173\u6ce8={total_followings} \u4f4d | "
                    f"\u672c\u8f6e\u4ec5\u5904\u7406\u6392\u5e8f\u9760\u524d\u7684 {len(followings)} \u4f4d"
                )
                logger.info(
                    "Douyin partial following limit applied | mode=counts | total={} | selected={}",
                    total_followings,
                    len(followings),
                )
            else:
                due_followings = []
                for user in followings:
                    uid = user.get("sec_uid") if isinstance(user, dict) else None
                    entry = progress.get(uid) if isinstance(progress, dict) and uid else None
                    refresh_needed, _ = self.cache_store.should_refresh_cache(
                        user,
                        entry,
                        return_reason=True,
                        refresh_on_profile_change=enable_profile_change_refresh,
                    )
                    if refresh_needed:
                        due_followings.append(user)
                due_total = len(due_followings)
                followings = due_followings[: self.max_followings]
                print(
                    f"\U0001f9e9 \u90e8\u5206\u6293\u53d6\u6a21\u5f0f\u5df2\u751f\u6548 | \u5168\u90e8\u5173\u6ce8={total_followings} \u4f4d | "
                    f"\u8fbe\u5230\u6293\u53d6\u6761\u4ef6={due_total} \u4f4d | \u672c\u8f6e\u4ec5\u5904\u7406\u6392\u5e8f\u9760\u524d\u7684 {len(followings)} \u4f4d\u8d85\u65f6\u6216\u65e0\u8bb0\u5f55\u535a\u4e3b"
                )
                logger.info(
                    "Douyin partial following limit applied | mode={} | total={} | due_candidates={} | selected={}",
                    fetch_mode,
                    total_followings,
                    due_total,
                    len(followings),
                )
        if followings:
            top_user = followings[0] if isinstance(followings[0], dict) else {}
            top_metric = self._resolve_following_sort_value(top_user, order_field)
            print(
                f"\U0001f4ca \u5173\u6ce8\u5217\u8868\u51c6\u5907\u5b8c\u6210 | \u672c\u8f6e\u5904\u7406={len(followings)} \u4f4d | "
                f"{order_label}\u6700\u9760\u524d={top_user.get('nickname', '')}({top_metric})"
            )
            logger.info(
                "Douyin followings ready | rows={} | top_nickname={} | top_metric={} | order_field={}",
                len(followings),
                top_user.get("nickname", ""),
                top_metric,
                order_field,
            )
        print(
            f"\U0001f4f1 \u5df2\u6309{order_label}{'\u4ece\u9ad8\u5230\u4f4e' if order_desc else '\u4ece\u4f4e\u5230\u9ad8'}\u6392\u5e8f\u540e\u5f00\u59cb\u6293\u53d6\u3002"
        )

        export_duration_analysis = self.should_export_duration_analysis()
        if fetch_mode == "counts":
            print("📇 当前为基础统计模式：只抓取每位博主的粉丝数、获赞总数和发布视频数。")
        elif fetch_mode == "verify":
            print("🔎 当前为主页校验模式：复用基础缓存主页链接，逐个进入主页校验获赞总数等主页数据。")
        elif fetch_mode == "monitor":
            print(f"🪶 当前为轻量监控模式：每位博主只抓最近 {self.config.recent_video_limit} 条作品。")
        elif fetch_mode == "delta":
            print(f"🧩 当前为增量模式：每位博主只抓最近 {self.config.recent_video_limit} 条作品并合并到缓存。")
        else:
            print("📚 当前为全量模式：会抓取博主全部作品，并生成完整时长分析。")
        if fetch_mode != "counts":
            cache_days = self.config.precise_cache_max_age_hours / 24
            print(f"🏷️  非基础模式抓取标记已启用：{cache_days:.0f} 天内已抓取博主将复用缓存，不重复进主页。")
        if self.config.enable_video_duration_analysis and not export_duration_analysis:
            print("⏭️  当前模式已跳过全量视频时长分析导出，以降低风控概率。")

        results = []
        all_video_rows = []
        summary_rows = []
        full_fetch_mismatch_rows = []
        pending_progress_saves = 0
        refreshed_user_count = 0
        cache_hit_count = 0
        refresh_reason_counts = {
            "missing_entry": 0,
            "expired": 0,
            "missing_summary": 0,
            "aweme_count_changed": 0,
            "latest_publish_timestamp_newer": 0,
            "zero_aweme_count_candidate": 0,
        }
        failed_profile_keys = (
            self.cache_store.load_failed_profile_keys(fetch_mode)
            if getattr(self.config, "skip_failed_profiles", False)
            else set()
        )
        failed_profile_skip_count = 0
        if failed_profile_keys:
            print(
                f"🧯 已启用失败博主跳过：{len(failed_profile_keys)} 个失败标记在有效期内，"
                f"有效期={self.config.failed_profile_skip_max_age_hours} 小时"
            )

        if fetch_mode == "counts":
            with create_progress() as progress_bar:
                task_id = progress_bar.add_task("统计抖音博主基础数据", total=len(followings))
                for index, user in enumerate(followings, 1):
                    try:
                        check_stop()
                    except OperationCancelled:
                        self.flush_partial_outputs(
                            results,
                            all_video_rows,
                            summary_rows,
                            progress,
                            pending_progress_saves,
                            index - 1,
                            merge_existing=partial_run,
                        )
                        raise
                    if self.failed_profile_key_matches(user, failed_profile_keys):
                        failed_profile_skip_count += 1
                        result = self.build_failed_profile_skipped_result_item(user)
                        results.append(result)
                        if self.should_export_summary_analysis():
                            summary_rows.append(self.build_counts_only_summary(user))
                        self.append_fetch_manifest_record(
                            user,
                            "failed_profile_skipped",
                            result=result,
                            message="matched_failed_profiles_csv",
                        )
                        progress_bar.advance(task_id)
                        continue
                    entry = progress.get(user.get("sec_uid")) if isinstance(progress, dict) else None
                    if isinstance(entry, dict):
                        cached_user = entry.get("user", {}) if isinstance(entry.get("user"), dict) else {}
                        if user.get("total_favorited") in (None, "") and cached_user.get("total_favorited") not in (None, ""):
                            user["total_favorited"] = cached_user.get("total_favorited")
                    if self.is_zero_video_candidate(user):
                        try:
                            self.browser_client.refresh_user_profile_from_homepage(user)
                        except (DouyinRateLimitError, DouyinServiceError) as exc:
                            print(f"⚠️  {user.get('nickname', '未知UP主')} 空主页确认失败，暂时保留: {exc}")
                            self.record_profile_failure(user, exc, "counts_empty_confirm")
                        except Exception as exc:
                            print(f"⚠️  {user.get('nickname', '未知UP主')} 空主页确认异常，暂时保留: {exc}")
                            self.record_profile_failure(user, exc, "counts_empty_confirm")
                        if self.handle_empty_video_profile(
                            user,
                            progress=progress,
                            cached_followings=cached_followings,
                        ):
                            progress_bar.advance(task_id)
                            continue
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
                            merge_existing=partial_run,
                        )

            self.display_counts_results(results)
            save_to_csv(self.config, results, merge_existing=partial_run)
            if self.should_export_summary_analysis():
                save_video_duration_analysis_to_csv(self.config, summary_rows, merge_existing=partial_run)
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
            logger.info(
                "Douyin counts finished | followings={} | results={} | summaries={} | exported={}",
                len(followings),
                len(results),
                len(summary_rows),
                [str(path) for path in exported],
            )
            print(
                f"🗂️  抖音 counts 模式已输出：{self._format_output_summary(exported)}，"
                f"共 {len(results)} 位博主"
            )
            return results

        if fetch_mode == "verify":
            verified_users = []
            profile_verified_count = 0
            with create_progress() as progress_bar:
                task_id = progress_bar.add_task("校验抖音博主主页数据", total=len(followings))
                for index, user in enumerate(followings, 1):
                    uid = user.get("sec_uid")
                    try:
                        check_stop()
                    except OperationCancelled:
                        self.merge_updated_followings_cache(cached_followings, verified_users)
                        self.flush_partial_outputs(
                            results,
                            all_video_rows,
                            summary_rows,
                            progress,
                            pending_progress_saves,
                            index - 1,
                            merge_existing=partial_run,
                        )
                        raise

                    if self.failed_profile_key_matches(user, failed_profile_keys):
                        failed_profile_skip_count += 1
                        result = self.build_failed_profile_skipped_result_item(user)
                        results.append(result)
                        if self.should_export_summary_analysis():
                            summary_rows.append(self.build_counts_only_summary(user))
                        self.append_fetch_manifest_record(
                            user,
                            "failed_profile_skipped",
                            result=result,
                            message="matched_failed_profiles_csv",
                        )
                        progress_bar.advance(task_id)
                        continue

                    entry = progress.get(uid) if isinstance(progress, dict) and uid else None
                    refresh_needed, refresh_reason = self.cache_store.should_refresh_cache(
                        user,
                        entry,
                        return_reason=True,
                        refresh_on_profile_change=False,
                    )
                    if self.is_zero_video_candidate(user) and not refresh_needed:
                        refresh_needed = True
                        refresh_reason = "zero_aweme_count_candidate"
                    profile_verified = False
                    if refresh_needed:
                        if refresh_reason in refresh_reason_counts:
                            refresh_reason_counts[refresh_reason] += 1
                        try:
                            self.browser_client.refresh_user_profile_from_homepage(user)
                            verified_users.append(dict(user))
                            profile_verified = True
                            profile_verified_count += 1
                            refreshed_user_count += 1
                        except DouyinRateLimitError as exc:
                            print(f"⚠️  {user.get('nickname', '未知UP主')} 主页校验触发速率限制: {exc}")
                            self.record_profile_failure(user, exc, "verify_rate_limit")
                            self.browser_client.restart(self.config.rate_limit_global_cooldown)
                        except DouyinServiceError as exc:
                            print(f"⚠️  {user.get('nickname', '未知UP主')} 主页校验出现服务异常: {exc}")
                            self.record_profile_failure(user, exc, "verify_service_error")
                            self.browser_client.restart(self.config.service_error_global_cooldown)
                        except Exception as exc:
                            print(f"⚠️  {user.get('nickname', '未知UP主')} 主页校验失败，将保留缓存数据: {exc}")
                            self.record_profile_failure(user, exc, "verify_profile")
                    else:
                        cache_hit_count += 1

                    if profile_verified and self.handle_empty_video_profile(
                        user,
                        progress=progress,
                        cached_followings=cached_followings,
                        verified_users=verified_users,
                    ):
                        self.append_fetch_manifest_record(
                            user,
                            "empty_profile_unfollowed",
                            video_count=0,
                            latest_video=None,
                            refresh_reason=refresh_reason,
                            message="empty_video_profile_confirmed",
                        )
                        progress_bar.advance(task_id)
                        continue

                    summary = self.build_summary_from_cached_entry(user, entry)
                    if (
                        profile_verified
                        and user.get("total_favorited") not in (None, "")
                        and not self.summary_has_precise_public_like_total(summary)
                    ):
                        summary["total_favorited"] = user.get("total_favorited")
                    result = self.build_result_from_cached_entry(user, entry)
                    if profile_verified:
                        result["data_source"] = "douyin_profile_verify"
                    elif not refresh_needed:
                        result["data_source"] = "douyin_cache_mark_valid"
                    verify_videos = entry.get("videos", []) if isinstance(entry, dict) else []
                    self.append_fetch_manifest_record(
                        user,
                        "profile_verified" if profile_verified else "cache_hit",
                        video_count=len(verify_videos or []),
                        latest_video=self.get_latest_video_from_entry(entry),
                        result=result,
                        refresh_reason=refresh_reason,
                    )
                    results.append(result)
                    if self.should_export_summary_analysis():
                        summary_rows.append(summary)

                    if uid and profile_verified:
                        videos = entry.get("videos", []) if isinstance(entry, dict) else []
                        latest_video = self.get_latest_video_from_entry(entry)
                        preserve_full_cache = self.should_preserve_full_cache(entry)
                        if preserve_full_cache:
                            progress[uid] = self.preserve_full_progress_entry(entry, "verify")
                        else:
                            existing_modes = set()
                            if isinstance(entry, dict) and isinstance(entry.get("cache_modes"), list):
                                existing_modes = {
                                    str(mode).strip().lower()
                                    for mode in entry.get("cache_modes", [])
                                    if str(mode).strip()
                                }
                            existing_modes.add("verify")
                            progress[uid] = {
                                "cached_at": int(time.time()),
                                "user": user,
                                "videos": videos,
                                "summary": summary,
                                "latest_video": latest_video,
                                "last_fetch_mode": fetch_mode,
                                "cache_modes": sorted(existing_modes),
                            }
                        self.cache_store.upsert_video_state_from_progress_entries(
                            {uid: progress[uid]},
                            source_mode="full" if preserve_full_cache else fetch_mode,
                        )
                        pending_progress_saves += 1

                    progress_bar.advance(task_id)

                    if pending_progress_saves >= self.config.progress_save_interval_users:
                        self.cache_store.save_progress(progress)
                        pending_progress_saves = 0

                    if (
                        profile_verified
                        and self.config.refresh_batch_size > 0
                        and refreshed_user_count % self.config.refresh_batch_size == 0
                    ):
                        cooldown = self.config.refresh_batch_cooldown
                        print(
                            f"⏸️  已连续主页校验 {refreshed_user_count} 位博主，"
                            f"批次冷却 {cooldown:.0f} 秒后继续..."
                        )
                        wait_with_progress(cooldown, "抖音主页校验批次冷却中")

                    if (
                        profile_verified
                        and self.config.browser_restart_interval_users > 0
                        and refreshed_user_count % self.config.browser_restart_interval_users == 0
                    ):
                        print(
                            f"🔧 已主页校验 {refreshed_user_count} 位博主，重启浏览器会话以降低后续风控概率..."
                        )
                        self.browser_client.restart(5)

                    if (
                        self.config.intermediate_upload_interval_users > 0
                        and index % self.config.intermediate_upload_interval_users == 0
                    ):
                        self.merge_updated_followings_cache(cached_followings, verified_users)
                        self.flush_partial_outputs(
                            results,
                            all_video_rows,
                            summary_rows,
                            progress,
                            pending_progress_saves,
                            index,
                            merge_existing=partial_run,
                        )
                        pending_progress_saves = 0

            if pending_progress_saves:
                self.cache_store.save_progress(progress)
            self.merge_updated_followings_cache(cached_followings, verified_users)

            self.display_counts_results(results)
            save_to_csv(self.config, results, merge_existing=partial_run)
            if self.should_export_summary_analysis():
                save_video_duration_analysis_to_csv(self.config, summary_rows, merge_existing=partial_run)
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
            logger.info(
                "Douyin verify finished | followings={} | verified={} | cache_hits={} | results={} | exported={}",
                len(followings),
                profile_verified_count,
                cache_hit_count,
                len(results),
                [str(path) for path in exported],
            )
            print(
                f"🗂️  抖音 verify 模式已输出：{self._format_output_summary(exported)}，"
                f"本轮进主页校验 {profile_verified_count} 位，复用未到期缓存 {cache_hit_count} 位"
            )
            return results

        with create_progress() as progress_bar:
            task_id = progress_bar.add_task("分析抖音博主", total=len(followings))
            for index, user in enumerate(followings, 1):
                try:
                    check_stop()
                except OperationCancelled:
                    self.flush_partial_outputs(
                        results,
                        all_video_rows,
                        summary_rows,
                        progress,
                        pending_progress_saves,
                        index - 1,
                        merge_existing=partial_run,
                    )
                    raise

                if self.failed_profile_key_matches(user, failed_profile_keys):
                    failed_profile_skip_count += 1
                    result = self.build_failed_profile_skipped_result_item(user)
                    results.append(result)
                    if self.should_export_summary_analysis():
                        summary_rows.append(self.build_counts_only_summary(user))
                    self.append_fetch_manifest_record(
                        user,
                        "failed_profile_skipped",
                        result=result,
                        message="matched_failed_profiles_csv",
                    )
                    progress_bar.advance(task_id)
                    continue

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
                    refresh_on_profile_change=enable_profile_change_refresh,
                )
                if self.is_zero_video_candidate(user) and not refresh_needed:
                    refresh_needed = True
                    refresh_reason = "zero_aweme_count_candidate"
                if refresh_needed:
                    if refresh_reason in refresh_reason_counts:
                        refresh_reason_counts[refresh_reason] += 1
                    try:
                        if fetch_mode == "full":
                            user["_full_fetch_validated"] = False
                            videos = []
                            full_fetch_validation_error = None
                            full_fetch_attempts = 0
                            for full_fetch_attempts in range(1, 3):
                                try:
                                    retry_videos = self.browser_client.get_all_videos_for_user(user)
                                    videos = self.merge_videos(videos, retry_videos)
                                    user["_full_fetch_validated"] = True
                                    full_fetch_validation_error = None
                                    break
                                except DouyinFullFetchValidationError as exc:
                                    videos = self.merge_videos(videos, exc.videos)
                                    latest_video = self.get_latest_video_from_videos(videos)
                                    full_fetch_validation_error = exc
                                    print(
                                        f"⚠️  {user['nickname']} 全量数量校验未通过 "
                                        f"({exc.actual_count}/{exc.expected_count or '未知'})：{exc}"
                                    )
                                    if full_fetch_attempts < 2:
                                        print(f"🔁 正在重新进入 {user['nickname']} 主页执行第 2 次全量抓取校验...")
                                        continue
                                    user["_full_fetch_validated"] = False
                                    full_fetch_mismatch_rows.append(
                                        self.build_full_fetch_mismatch_row(
                                            user,
                                            exc,
                                            full_fetch_attempts,
                                        )
                                    )
                                    print(
                                        f"📝 {user['nickname']} 全量两次抓取后作品数仍未对齐，"
                                        f"已记录到 {self.config.full_fetch_mismatch_csv.name}，继续下一个博主。"
                                    )
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
                            self.record_profile_failure(user, exc, "fetch_rate_limit")
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
                            self.record_profile_failure(user, exc, "fetch_service_error")
                            results.append(self.build_fetch_failed_result_item(user))
                            progress_bar.advance(task_id)
                            continue
                    except Exception as exc:
                        print(f"⚠️  {user['nickname']} 抓取失败: {exc}")
                        if entry:
                            videos = entry.get("videos", [])
                            latest_video = self.get_latest_video_from_entry(entry)
                        else:
                            self.record_profile_failure(user, exc, "fetch_profile")
                            results.append(self.build_fetch_failed_result_item(user))
                            progress_bar.advance(task_id)
                            continue

                    if self.handle_empty_video_profile(
                        user,
                        progress=progress,
                        cached_followings=cached_followings,
                    ):
                        self.append_fetch_manifest_record(
                            user,
                            "empty_profile_unfollowed",
                            video_count=0,
                            latest_video=None,
                            refresh_reason=refresh_reason,
                            message="empty_video_profile_confirmed",
                        )
                        progress_bar.advance(task_id)
                        continue

                    if fetch_mode == "full":
                        summary = self.build_video_duration_summary(user, videos)
                    elif entry and isinstance(entry.get("summary"), dict):
                        summary = dict(entry.get("summary") or {})
                        summary["uploader_name"] = user["nickname"]
                        summary["uploader_id"] = user["sec_uid"]
                        summary["follower_count"] = user.get("follower_count")
                        summary = self.apply_precise_public_like_total_from_videos(user, summary, videos)
                        if not self.summary_has_precise_public_like_total(summary):
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

                    progress_user = {
                        key: value
                        for key, value in user.items()
                        if not str(key).startswith("_")
                    }
                    preserve_full_cache = self.should_preserve_full_cache(entry)
                    if preserve_full_cache:
                        progress[user["sec_uid"]] = self.preserve_full_progress_entry(entry, fetch_mode)
                    else:
                        progress[user["sec_uid"]] = {
                            "cached_at": int(time.time()),
                            "user": progress_user,
                            "videos": videos,
                            "summary": summary,
                            "latest_video": latest_video,
                            "last_fetch_mode": fetch_mode,
                            "cache_modes": sorted(existing_modes),
                        }
                    self.cache_store.upsert_video_state_from_progress_entries(
                        {user["sec_uid"]: progress[user["sec_uid"]]},
                        source_mode="full" if preserve_full_cache else fetch_mode,
                    )
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
                self.append_fetch_manifest_record(
                    user,
                    "refreshed" if refresh_needed else "cache_hit",
                    video_count=len(videos or []),
                    latest_video=latest_video,
                    result=result,
                    refresh_reason=refresh_reason,
                )
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
                        merge_existing=partial_run,
                    )
                    pending_progress_saves = 0

        if pending_progress_saves:
            self.cache_store.save_progress(progress)

        if fetch_mode != "full":
            refreshed_total = sum(refresh_reason_counts.values())
            logger.info(
                "Douyin cache summary | mode={} | cache_hits={} | refreshed_total={} | reasons={}",
                fetch_mode,
                cache_hit_count,
                refreshed_total,
                refresh_reason_counts,
            )
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
                        f"作品为 0 待确认: {refresh_reason_counts['zero_aweme_count_candidate']}",
                        f"失败名单跳过: {failed_profile_skip_count}",
                    ],
                    border_style="blue",
                )
            )

        results.sort(key=self._sort_days_since_value, reverse=True)
        self.display_top_results(results)
        save_to_csv(self.config, results, merge_existing=partial_run)

        if self.should_export_summary_analysis():
            save_video_duration_analysis_to_csv(self.config, summary_rows, merge_existing=partial_run)
        save_cache_inventory_to_csv(
            self.config,
            self.build_cache_inventory_rows(
                self.cache_store.load_followings_cache_payload(),
                progress,
            ),
        )
        if fetch_mode == "full":
            save_full_fetch_mismatch_to_csv(self.config, full_fetch_mismatch_rows)
        if export_duration_analysis:
            save_all_videos_to_csv(self.config, all_video_rows)
            save_video_duration_report(self.config, summary_rows, len(all_video_rows))

        exported = [self.config.output_csv, self.config.cache_inventory_csv]
        if self.should_export_summary_analysis():
            exported.append(self.config.video_duration_analysis_csv)
        if fetch_mode == "full":
            exported.append(self.config.full_fetch_mismatch_csv)
        if export_duration_analysis:
            exported.extend([self.config.all_videos_csv, self.config.video_duration_report_md])
        logger.info(
            "Douyin analysis finished | mode={} | results={} | summaries={} | videos={} | exported={}",
            fetch_mode,
            len(results),
            len(summary_rows),
            len(all_video_rows),
            [str(path) for path in exported],
        )
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
