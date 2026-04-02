import json
import time
from datetime import datetime

from bilibili_analyzer.logging_utils import smart_print as print

from .utils import calculate_days_since, normalize_timestamp, timestamp_to_date


class CacheStore:
    def __init__(self, config):
        self.config = config

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

    def should_refresh_cache(self, progress_entry):
        if not isinstance(progress_entry, dict):
            return True
        if self.is_cache_expired(progress_entry.get("cached_at")):
            return True
        summary = progress_entry.get("summary", {})
        if not isinstance(summary, dict) or "total_videos" not in summary:
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
