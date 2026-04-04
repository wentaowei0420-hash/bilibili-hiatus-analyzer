import json
import time
import hashlib
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
            self.config.followings_cache_json.parent.mkdir(parents=True, exist_ok=True)
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

        if data.get("storage") == "split":
            return self._load_split_progress(self.config.progress_dir, data.get("keys", []))

        ups = data.get("ups", {})
        if not isinstance(ups, dict):
            return {}
        return ups

    def save_progress(self, progress):
        trimmed_progress = {
            key: self._trim_progress_entry(entry)
            for key, entry in progress.items()
        }
        self._write_split_progress(
            self.config.progress_json,
            self.config.progress_dir,
            trimmed_progress,
            "保存抖音进度缓存失败",
        )

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

    @staticmethod
    def _entry_filename(key):
        digest = hashlib.sha1(str(key).encode("utf-8")).hexdigest()
        return f"{digest}.json"

    def _load_split_progress(self, directory, keys):
        progress = {}
        if not directory.exists():
            return progress

        for key in keys:
            entry_path = directory / self._entry_filename(key)
            if not entry_path.exists():
                continue
            try:
                with entry_path.open("r", encoding="utf-8") as entry_file:
                    progress[key] = json.load(entry_file)
            except Exception as exc:
                print(f"⚠️  读取抖音缓存分片失败({key})，将跳过该分片: {exc}")
        return progress

    def _trim_progress_entry(self, entry):
        if not isinstance(entry, dict):
            return entry

        trimmed_entry = dict(entry)
        if self.config.fetch_mode == "full":
            return trimmed_entry

        videos = trimmed_entry.get("videos")
        if isinstance(videos, list) and len(videos) > self.config.progress_trim_video_limit:
            trimmed_entry["videos"] = videos[: self.config.progress_trim_video_limit]
        return trimmed_entry

    def _write_split_progress(self, manifest_path, directory, progress, error_message):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            keys = sorted(progress.keys(), key=str)
            expected_filenames = set()

            for key in keys:
                entry_filename = self._entry_filename(key)
                expected_filenames.add(entry_filename)
                entry_path = directory / entry_filename
                with entry_path.open("w", encoding="utf-8") as entry_file:
                    json.dump(progress[key], entry_file, ensure_ascii=False, separators=(",", ":"))

            for existing_file in directory.glob("*.json"):
                if existing_file.name not in expected_filenames:
                    existing_file.unlink(missing_ok=True)

            manifest_payload = {
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "storage": "split",
                "keys": keys,
            }
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with manifest_path.open("w", encoding="utf-8") as progress_file:
                json.dump(manifest_payload, progress_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"⚠️  {error_message}: {exc}")
