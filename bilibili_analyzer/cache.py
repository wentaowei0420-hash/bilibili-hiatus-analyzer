import hashlib
import json
import time
from datetime import datetime

from common.platform_store import upsert_cache_entries

from .logging_utils import smart_print as print
from .utils import calculate_days_since, normalize_timestamp, timestamp_to_date, UNKNOWN_DATE


class CacheStore:
    def __init__(self, config):
        self.config = config

    def load_precise_progress(self):
        try:
            with self.config.progress_json.open("r", encoding="utf-8") as progress_file:
                data = json.load(progress_file)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            print(f"读取进度文件失败，将从头开始: {exc}")
            return {}

        raw_results = data.get("results_by_mid", {})
        if not isinstance(raw_results, dict):
            return {}

        results = {}
        for mid, result in raw_results.items():
            if not isinstance(result, dict):
                continue
            if result.get("data_source") == "video_api" and result.get("upload_date") == UNKNOWN_DATE:
                continue
            results[mid] = result
        return results

    def save_precise_progress(self, results_by_mid):
        payload = {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "results_by_mid": results_by_mid,
        }
        self._write_json(self.config.progress_json, payload, "保存进度文件失败")
        upsert_cache_entries(
            self.config.export_store_db,
            "bilibili",
            results_by_mid,
            cache_type="precise_progress",
            source_mode="precise",
            uploader_id_getter=lambda key, payload: ((payload or {}).get("uploader_id") or key),
            cached_at_getter=lambda payload: (payload or {}).get("cached_at", ""),
        )

    def load_video_duration_progress(self):
        try:
            with self.config.video_duration_progress_json.open("r", encoding="utf-8") as progress_file:
                data = json.load(progress_file)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            print(f"读取视频时长分析进度失败，将重新抓取: {exc}")
            return {}

        if data.get("storage") == "split":
            return self._load_split_progress(
                self.config.video_duration_progress_dir,
                data.get("keys", []),
            )

        ups = data.get("ups", {})
        return ups if isinstance(ups, dict) else {}

    def save_video_duration_progress(self, progress):
        self._write_split_progress(
            self.config.video_duration_progress_json,
            self.config.video_duration_progress_dir,
            progress,
            "保存视频时长分析进度失败",
        )
        upsert_cache_entries(
            self.config.export_store_db,
            "bilibili",
            progress,
            cache_type="video_duration_progress",
            source_mode="analysis",
            uploader_id_getter=lambda key, payload: (((payload or {}).get("following", {}) or {}).get("mid") or key),
            cached_at_getter=lambda payload: (payload or {}).get("cached_at", ""),
        )

    def is_cache_expired(self, cached_at, max_age_hours):
        cached_timestamp = normalize_timestamp(cached_at)
        if not cached_timestamp:
            return True
        return time.time() - cached_timestamp >= max_age_hours * 3600

    def should_refresh_precise_cache(self, following, cached_result):
        if not isinstance(cached_result, dict):
            return True

        data_source = cached_result.get("data_source")
        if data_source not in ("video_api", "no_video"):
            return True

        if self.is_cache_expired(cached_result.get("cached_at"), self.config.precise_cache_max_age_hours):
            return True

        following_mtime = normalize_timestamp(following.get("mtime"))
        if not following_mtime:
            return False

        if data_source == "video_api":
            cached_upload_timestamp = normalize_timestamp(cached_result.get("upload_timestamp"))
            if not cached_upload_timestamp:
                return True
            return following_mtime > cached_upload_timestamp

        cached_at = normalize_timestamp(cached_result.get("cached_at"))
        if not cached_at:
            return True
        return following_mtime > cached_at

    def should_refresh_video_duration_cache(self, following, progress_entry):
        if not isinstance(progress_entry, dict):
            return True

        summary = progress_entry.get("summary", {})
        if not isinstance(summary, dict) or not summary:
            return True

        if self.is_cache_expired(progress_entry.get("cached_at"), self.config.video_duration_cache_max_age_hours):
            return True

        following_mtime = normalize_timestamp(following.get("mtime"))
        if not following_mtime:
            return False

        latest_publish_timestamp = normalize_timestamp(summary.get("latest_publish_timestamp"))
        if not latest_publish_timestamp:
            return True
        return following_mtime > latest_publish_timestamp

    def refresh_result_runtime_fields(self, result):
        if not isinstance(result, dict):
            return result

        data_source = result.get("data_source")
        if data_source == "video_api":
            upload_timestamp = normalize_timestamp(result.get("upload_timestamp"))
            if upload_timestamp:
                result["upload_date"] = timestamp_to_date(upload_timestamp)
                days_since = calculate_days_since(upload_timestamp)
                result["days_since_update"] = days_since
                result["days_since_last_video"] = days_since
        elif data_source == "followings_mtime":
            activity_timestamp = normalize_timestamp(result.get("activity_timestamp"))
            if activity_timestamp:
                result["upload_date"] = timestamp_to_date(activity_timestamp)
                days_since = calculate_days_since(activity_timestamp)
                result["days_since_update"] = days_since
                result["days_since_last_video"] = days_since

        return result

    def _write_json(self, path, payload, error_message):
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open("w", encoding="utf-8") as progress_file:
                json.dump(payload, progress_file, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"{error_message}: {exc}")

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
                print(f"读取缓存分片失败({key})，将跳过该分片: {exc}")
        return progress

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

            self._write_json(
                manifest_path,
                {
                    "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "storage": "split",
                    "keys": keys,
                },
                error_message,
            )
        except Exception as exc:
            print(f"{error_message}: {exc}")
