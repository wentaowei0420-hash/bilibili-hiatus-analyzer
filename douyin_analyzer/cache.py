import json
import time
from datetime import datetime

from bilibili_analyzer.logging_utils import smart_print as print

from .utils import calculate_days_since, normalize_timestamp, timestamp_to_date


class CacheStore:
    def __init__(self, config):
        self.config = config

    def load_followings_cache(self):
        try:
            with self.config.followings_cache_json.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            return []
        except Exception as exc:
            print(f"⚠️  读取抖音关注列表缓存失败，将重新抓取: {exc}")
            return []

        followings = data.get("followings", [])
        if not isinstance(followings, list):
            return []
        return followings

    def save_followings_cache(self, followings):
        payload = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cached_at": int(time.time()),
            "followings": followings,
        }
        try:
            with self.config.followings_cache_json.open("w", encoding="utf-8") as cache_file:
                json.dump(payload, cache_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"⚠️  保存抖音关注列表缓存失败: {exc}")

    def is_followings_cache_expired(self):
        try:
            with self.config.followings_cache_json.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            return True
        except Exception as exc:
            print(f"⚠️  读取抖音关注列表缓存时间失败，将重新抓取: {exc}")
            return True

        cached_at = data.get("cached_at")
        cached_timestamp = normalize_timestamp(cached_at)
        if not cached_timestamp:
            return True
        return (
            time.time() - cached_timestamp
            >= self.config.followings_cache_max_age_hours * 3600
        )

    def load_progress(self):
        try:
            with self.config.progress_json.open("r", encoding="utf-8") as progress_file:
                data = json.load(progress_file)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            print(f"⚠️  读取抖音进度缓存失败，将重新抓取: {exc}")
            return {}

        ups = data.get("ups", {})
        if not isinstance(ups, dict):
            return {}
        return ups

    def save_progress(self, progress):
        payload = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "ups": progress,
        }
        try:
            with self.config.progress_json.open("w", encoding="utf-8") as progress_file:
                json.dump(payload, progress_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"⚠️  保存抖音进度缓存失败: {exc}")

    def is_cache_expired(self, cached_at):
        cached_timestamp = normalize_timestamp(cached_at)
        if not cached_timestamp:
            return True
        return time.time() - cached_timestamp >= self.config.precise_cache_max_age_hours * 3600

    def should_refresh_cache(self, current_user, progress_entry):
        if not isinstance(progress_entry, dict):
            return True
        if self.is_cache_expired(progress_entry.get("cached_at")):
            return True
        summary = progress_entry.get("summary", {})
        if not isinstance(summary, dict) or "total_videos" not in summary:
            return True
        current_user = current_user if isinstance(current_user, dict) else {}
        cached_user = progress_entry.get("user", {})
        if not isinstance(cached_user, dict):
            cached_user = {}

        current_aweme_count = current_user.get("aweme_count")
        cached_aweme_count = cached_user.get("aweme_count", summary.get("total_videos"))
        if current_aweme_count is not None and cached_aweme_count is not None:
            if int(current_aweme_count) != int(cached_aweme_count):
                return True

        current_latest_publish_timestamp = normalize_timestamp(
            current_user.get("latest_publish_timestamp")
        )
        cached_latest_publish_timestamp = normalize_timestamp(
            summary.get("latest_publish_timestamp")
        )
        if current_latest_publish_timestamp and (
            current_latest_publish_timestamp != cached_latest_publish_timestamp
        ):
            return True
        return False

    def refresh_result_runtime_fields(self, result):
        if not isinstance(result, dict):
            return result

        upload_timestamp = normalize_timestamp(result.get("upload_timestamp"))
        if upload_timestamp:
            result["upload_date"] = timestamp_to_date(upload_timestamp)
            days_since = calculate_days_since(upload_timestamp)
            result["days_since_update"] = days_since
            result["days_since_last_video"] = days_since
        return result
