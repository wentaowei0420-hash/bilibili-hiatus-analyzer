import hashlib
import csv
import json
import sqlite3
import time
from datetime import datetime
from urllib.parse import urlparse

from bilibili_analyzer.logging_utils import smart_print as print
from common.export_store import delete_rows_by_values
from common.file_io import atomic_write_json
from common.platform_store import delete_uploader_rows, upsert_cache_entries, upsert_video_state_rows

from .utils import calculate_days_since, normalize_timestamp, timestamp_to_date


class CacheStore:
    def __init__(self, config):
        self.config = config

    @staticmethod
    def _entry_has_full_cache(entry):
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

    def load_followings_cache_payload(self):
        try:
            with self.config.followings_cache_json.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            return {}
        except Exception as exc:
            print(f"读取抖音关注列表缓存失败，将重新抓取: {exc}")
            return {}

        if data.get("storage") == "split":
            followings = self._load_split_followings(
                self.config.followings_cache_dir,
                data.get("keys", []),
            )
            return {
                "saved_at": data.get("saved_at", ""),
                "cached_at": data.get("cached_at"),
                "storage": "split",
                "keys": data.get("keys", []),
                "followings": followings,
            }

        if isinstance(data, dict):
            legacy_followings = data.get("followings", [])
            if isinstance(legacy_followings, list) and legacy_followings and not self.config.followings_cache_dir.exists():
                self._write_split_followings(
                    self.config.followings_cache_json,
                    self.config.followings_cache_dir,
                    self._build_followings_split_payload(legacy_followings),
                    "迁移抖音关注列表缓存到分片结构失败",
                )
                if self.config.followings_cache_dir.exists():
                    return self.load_followings_cache_payload()
        return data if isinstance(data, dict) else {}

    def load_followings_cache(self):
        data = self.load_followings_cache_payload()
        followings = data.get("followings", [])
        return followings if isinstance(followings, list) else []

    def save_followings_cache(self, followings):
        previous_followings = self.load_followings_cache()
        previous_uids = {
            str((item or {}).get("sec_uid") or "").strip()
            for item in previous_followings
            if isinstance(item, dict) and str((item or {}).get("sec_uid") or "").strip()
        }
        current_uids = {
            str((item or {}).get("sec_uid") or "").strip()
            for item in (followings or [])
            if isinstance(item, dict) and str((item or {}).get("sec_uid") or "").strip()
        }
        removed_uids = sorted(previous_uids - current_uids)

        payload = self._build_followings_split_payload(followings)
        self._write_split_followings(
            self.config.followings_cache_json,
            self.config.followings_cache_dir,
            payload,
            "保存抖音关注列表缓存失败",
        )
        entries = payload.get("entries", {})
        upsert_cache_entries(
            self.config.export_store_db,
            "douyin",
            entries,
            cache_type="followings",
            source_mode="followings",
            uploader_id_getter=lambda key, payload: ((payload or {}).get("sec_uid") or key),
            cached_at_getter=lambda payload: (payload or {}).get("latest_publish_timestamp", ""),
        )

        if removed_uids:
            progress = self.load_progress()
            if isinstance(progress, dict) and progress:
                updated_progress = {
                    key: value
                    for key, value in progress.items()
                    if str(key).strip() not in removed_uids
                }
                if len(updated_progress) != len(progress):
                    self.save_progress(updated_progress)

            self._remove_uploader_rows_from_store(removed_uids)
            print(
                f"🧹 检测到 {len(removed_uids)} 位已取关博主，已从本地缓存与导出状态中清理。"
            )

    def remove_unfollowed_user(self, homepage="", uploader_id=""):
        normalized_homepage = self._normalize_homepage_url(homepage)
        target_uploader_id = str(uploader_id or "").strip()
        removed_uids = set()

        followings_payload = self.load_followings_cache_payload()
        followings = followings_payload.get("followings", []) if isinstance(followings_payload, dict) else []
        if isinstance(followings, list) and followings:
            kept_followings = []
            for following in followings:
                if not isinstance(following, dict):
                    kept_followings.append(following)
                    continue
                current_uid = str(following.get("sec_uid") or "").strip()
                current_homepage = self._normalize_homepage_url(following.get("homepage", ""))
                matched = (
                    (target_uploader_id and current_uid == target_uploader_id)
                    or (normalized_homepage and current_homepage == normalized_homepage)
                )
                if matched:
                    if current_uid:
                        removed_uids.add(current_uid)
                    continue
                kept_followings.append(following)

            if len(kept_followings) != len(followings):
                self.save_followings_cache(kept_followings)

        if normalized_homepage and not removed_uids:
            progress = self.load_progress()
            for uid, entry in (progress or {}).items():
                user = (entry or {}).get("user", {}) if isinstance(entry, dict) else {}
                current_homepage = self._normalize_homepage_url(user.get("homepage", ""))
                if current_homepage == normalized_homepage:
                    removed_uids.add(str(uid).strip())

        if removed_uids:
            progress = self.load_progress()
            if isinstance(progress, dict):
                updated_progress = {
                    key: value
                    for key, value in progress.items()
                    if str(key).strip() not in removed_uids
                }
                if len(updated_progress) != len(progress):
                    self.save_progress(updated_progress)
            self._remove_uploader_rows_from_store(removed_uids)

        return sorted(removed_uids)

    def prune_non_followed_cache(self):
        followings_payload = self.load_followings_cache_payload()
        followings = followings_payload.get("followings", []) if isinstance(followings_payload, dict) else []
        current_uids = {
            str((item or {}).get("sec_uid") or "").strip()
            for item in (followings or [])
            if isinstance(item, dict) and str((item or {}).get("sec_uid") or "").strip()
        }
        if not current_uids:
            return []

        progress = self.load_progress()
        cached_progress_uids = {
            str(uid).strip()
            for uid in (progress or {}).keys()
            if str(uid).strip()
        }
        cached_store_uids = self._load_cached_uploader_ids_from_store()
        removed_uids = sorted((cached_progress_uids | cached_store_uids) - current_uids)
        if not removed_uids:
            return []

        updated_progress = {
            key: value
            for key, value in (progress or {}).items()
            if str(key).strip() not in removed_uids
        }
        if len(updated_progress) != len(progress):
            self.save_progress(updated_progress)

        self._remove_uploader_rows_from_store(removed_uids)
        print(
            f"🧹 检测到 {len(removed_uids)} 位非当前关注博主，已从本地缓存与导出状态中清理。"
        )
        return removed_uids

    def is_followings_cache_expired(self):
        try:
            with self.config.followings_cache_json.open("r", encoding="utf-8") as cache_file:
                data = json.load(cache_file)
        except FileNotFoundError:
            return True
        except Exception as exc:
            print(f"读取抖音关注列表缓存时间失败，将重新抓取: {exc}")
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
            print(f"读取抖音进度缓存失败，将重新抓取: {exc}")
            return {}

        if data.get("storage") == "split":
            return self._load_split_progress(self.config.progress_dir, data.get("keys", []))

        ups = data.get("ups", {})
        return ups if isinstance(ups, dict) else {}

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
        upsert_cache_entries(
            self.config.export_store_db,
            "douyin",
            trimmed_progress,
            cache_type="progress",
            source_mode=self.config.fetch_mode,
            uploader_id_getter=lambda key, payload: (((payload or {}).get("user", {}) or {}).get("sec_uid") or key),
            cached_at_getter=lambda payload: (payload or {}).get("cached_at", ""),
        )
    def append_fetch_manifest(self, record):
        manifest_path = getattr(self.config, "fetch_manifest_jsonl", None)
        if not manifest_path:
            return
        payload = dict(record or {})
        payload.setdefault("recorded_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        try:
            manifest_path.parent.mkdir(parents=True, exist_ok=True)
            with manifest_path.open("a", encoding="utf-8") as manifest_file:
                manifest_file.write(json.dumps(payload, ensure_ascii=False, separators=(",", ":")) + "\n")
        except Exception as exc:
            print(f"写入抖音抓取清单失败: {exc}")

    def append_failed_profile(self, user, reason, stage="", mode=None):
        path = getattr(self.config, "failed_profiles_csv", None)
        if not path:
            return
        row = {
            "failed_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "mode": mode or self.config.fetch_mode,
            "stage": stage or "",
            "uploader_id": (user or {}).get("sec_uid", ""),
            "uploader_name": (user or {}).get("nickname", ""),
            "homepage": (user or {}).get("homepage", ""),
            "reason": str(reason or ""),
        }
        fieldnames = [
            "failed_time",
            "mode",
            "stage",
            "uploader_id",
            "uploader_name",
            "homepage",
            "reason",
        ]
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            write_header = not path.exists() or path.stat().st_size == 0
            with path.open("a", encoding="utf-8-sig", newline="") as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames, extrasaction="ignore")
                if write_header:
                    writer.writeheader()
                writer.writerow(row)
        except Exception as exc:
            print(f"写入抖音失败博主CSV失败: {exc}")

    def load_failed_profile_keys(self, mode=None, max_age_hours=None):
        path = getattr(self.config, "failed_profiles_csv", None)
        if not path or not path.exists():
            return set()
        mode = str(mode or self.config.fetch_mode or "").strip()
        max_age_hours = (
            max_age_hours
            if max_age_hours is not None
            else getattr(self.config, "failed_profile_skip_max_age_hours", 24)
        )
        cutoff = time.time() - max(0, float(max_age_hours or 0)) * 3600 if max_age_hours else None
        keys = set()
        try:
            with path.open("r", encoding="utf-8-sig", newline="") as csvfile:
                for row in csv.DictReader(csvfile):
                    row = row if isinstance(row, dict) else {}
                    row_mode = str(row.get("mode") or "").strip()
                    if mode and row_mode and row_mode != mode:
                        continue
                    if cutoff:
                        failed_ts = self._parse_failed_time(row.get("failed_time"))
                        if failed_ts and failed_ts < cutoff:
                            continue
                    uploader_id = str(row.get("uploader_id") or "").strip()
                    homepage = self._normalize_homepage_url(row.get("homepage", ""))
                    if uploader_id:
                        keys.add(f"uid:{uploader_id}")
                    if homepage:
                        keys.add(f"homepage:{homepage}")
        except Exception as exc:
            print(f"读取抖音失败博主CSV失败，将不跳过失败记录: {exc}")
        return keys

    def is_cache_expired(self, cached_at):
        cached_timestamp = normalize_timestamp(cached_at)
        if not cached_timestamp:
            return True
        return time.time() - cached_timestamp >= self.config.precise_cache_max_age_hours * 3600

    def should_refresh_cache(
        self,
        current_user,
        progress_entry,
        return_reason=False,
        refresh_on_profile_change=True,
    ):
        def _result(needs_refresh, reason=None):
            return (needs_refresh, reason) if return_reason else needs_refresh

        if not isinstance(progress_entry, dict):
            return _result(True, "missing_entry")
        if self.is_cache_expired(progress_entry.get("cached_at")):
            return _result(True, "expired")
        summary = progress_entry.get("summary", {})
        if not isinstance(summary, dict) or "total_videos" not in summary:
            return _result(True, "missing_summary")

        current_user = current_user if isinstance(current_user, dict) else {}
        cached_user = progress_entry.get("user", {})
        if not isinstance(cached_user, dict):
            cached_user = {}

        if refresh_on_profile_change:
            current_aweme_count = current_user.get("aweme_count")
            cached_aweme_count = cached_user.get("aweme_count", summary.get("total_videos"))
            if current_aweme_count is not None and cached_aweme_count is not None:
                try:
                    if int(current_aweme_count) != int(cached_aweme_count):
                        return _result(True, "aweme_count_changed")
                except (TypeError, ValueError):
                    pass

            current_latest_publish_timestamp = normalize_timestamp(current_user.get("latest_publish_timestamp"))
            cached_latest_publish_timestamp = normalize_timestamp(summary.get("latest_publish_timestamp"))
            if current_latest_publish_timestamp and (
                not cached_latest_publish_timestamp
                or current_latest_publish_timestamp > cached_latest_publish_timestamp
            ):
                return _result(True, "latest_publish_timestamp_newer")
        return _result(False, "reuse")

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

    def _build_followings_split_payload(self, followings):
        keys = []
        entries = {}
        for index, following in enumerate(followings or []):
            if isinstance(following, dict):
                key = str(following.get("sec_uid") or f"__index__:{index}")
            else:
                key = f"__index__:{index}"
            keys.append(key)
            entries[key] = following
        return {
            "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "cached_at": int(time.time()),
            "storage": "split",
            "keys": keys,
            "entries": entries,
        }

    def _load_split_followings(self, directory, keys):
        followings = []
        if not directory.exists():
            return followings
        for key in keys:
            entry_path = directory / self._entry_filename(key)
            if not entry_path.exists():
                continue
            try:
                with entry_path.open("r", encoding="utf-8") as entry_file:
                    followings.append(json.load(entry_file))
            except Exception as exc:
                print(f"读取抖音关注列表分片失败({key})，将跳过该分片: {exc}")
        return followings

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
                print(f"读取抖音缓存分片失败({key})，将跳过该分片: {exc}")
        return progress

    def _trim_progress_entry(self, entry):
        if not isinstance(entry, dict):
            return entry
        trimmed_entry = dict(entry)
        if self.config.fetch_mode == "full" or self._entry_has_full_cache(trimmed_entry):
            return trimmed_entry
        videos = trimmed_entry.get("videos")
        if isinstance(videos, list) and len(videos) > self.config.progress_trim_video_limit:
            trimmed_entry["videos"] = videos[: self.config.progress_trim_video_limit]
        return trimmed_entry

    def _write_split_followings(self, manifest_path, directory, payload, error_message):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            keys = list(payload.get("keys", []))
            entries = payload.get("entries", {})
            expected_filenames = set()
            for key in keys:
                entry_filename = self._entry_filename(key)
                expected_filenames.add(entry_filename)
                entry_path = directory / entry_filename
                atomic_write_json(
                    entry_path,
                    entries.get(key),
                    encoding="utf-8",
                    separators=(",", ":"),
                )

            for existing_file in directory.glob("*.json"):
                if existing_file.name not in expected_filenames:
                    existing_file.unlink(missing_ok=True)

            manifest_payload = {
                "saved_at": payload.get("saved_at", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
                "cached_at": payload.get("cached_at", int(time.time())),
                "storage": "split",
                "keys": keys,
            }
            atomic_write_json(manifest_path, manifest_payload, encoding="utf-8", indent=2)
        except Exception as exc:
            print(f"{error_message}: {exc}")

    def _write_split_progress(self, manifest_path, directory, progress, error_message):
        try:
            directory.mkdir(parents=True, exist_ok=True)
            keys = sorted(progress.keys(), key=str)
            expected_filenames = set()
            for key in keys:
                entry_filename = self._entry_filename(key)
                expected_filenames.add(entry_filename)
                entry_path = directory / entry_filename
                atomic_write_json(
                    entry_path,
                    progress[key],
                    encoding="utf-8",
                    separators=(",", ":"),
                )

            for existing_file in directory.glob("*.json"):
                if existing_file.name not in expected_filenames:
                    existing_file.unlink(missing_ok=True)

            manifest_payload = {
                "saved_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "storage": "split",
                "keys": keys,
            }
            atomic_write_json(manifest_path, manifest_payload, encoding="utf-8", indent=2)
        except Exception as exc:
            print(f"{error_message}: {exc}")

    @staticmethod
    def _parse_failed_time(value):
        value = str(value or "").strip()
        if not value:
            return None
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S"):
            try:
                return datetime.strptime(value[:19], fmt).timestamp()
            except ValueError:
                continue
        return normalize_timestamp(value)

    @staticmethod
    def _normalize_homepage_url(homepage):
        homepage = str(homepage or "").strip()
        if not homepage:
            return ""
        if not homepage.startswith(("http://", "https://")):
            return homepage
        parsed = urlparse(homepage)
        if not parsed.netloc:
            return ""
        normalized_path = parsed.path.rstrip("/")
        if not normalized_path:
            return ""
        return f"https://{parsed.netloc}{normalized_path}"

    def _remove_uploader_rows_from_store(self, uploader_ids):
        uploader_ids = sorted({str(item).strip() for item in (uploader_ids or []) if str(item).strip()})
        if not uploader_ids:
            return

        delete_uploader_rows(self.config.export_store_db, "douyin", uploader_ids)
        for table_name in [
            self.config.export_main_table,
            self.config.export_analysis_table,
            self.config.export_uid_analysis_table,
        ]:
            delete_rows_by_values(self.config.export_store_db, table_name, uploader_ids)

    def upsert_video_state_from_progress_entries(self, progress, source_mode=None):
        video_rows = []
        for uid, entry in (progress or {}).items():
            if not isinstance(entry, dict):
                continue
            user = entry.get("user", {}) if isinstance(entry.get("user"), dict) else {}
            uploader_id = str(user.get("sec_uid") or uid or "").strip()
            uploader_name = user.get("nickname") or user.get("uploader_name") or ""
            for video in entry.get("videos", []) or []:
                if not isinstance(video, dict):
                    continue
                row = dict(video)
                row.setdefault("uploader_id", uploader_id)
                row.setdefault("uploader_name", uploader_name)
                video_rows.append(row)
        if video_rows:
            upsert_video_state_rows(
                self.config.export_store_db,
                "douyin",
                video_rows,
                video_id_column="aweme_id",
                uploader_id_column="uploader_id",
                uploader_name_column="uploader_name",
                source_mode=source_mode or self.config.fetch_mode,
            )

    def _load_cached_uploader_ids_from_store(self):
        db_path = self.config.export_store_db
        if not db_path.exists():
            return set()

        uploader_ids = set()
        table_targets = [
            ("douyin_creator_raw", "uploader_id"),
            ("douyin_video_raw", "uploader_id"),
            ("douyin_cache_state", "uploader_id"),
            ("douyin_summary_current", "uploader_id"),
            (self.config.export_main_table, None),
            (self.config.export_analysis_table, None),
            (self.config.export_uid_analysis_table, None),
        ]
        candidate_columns = ["UP主UID", "UP涓籙ID", "uploader_id", "target_uid"]

        with sqlite3.connect(db_path) as conn:
            cursor = conn.cursor()
            for table_name, preferred_column in table_targets:
                cursor.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
                    (table_name,),
                )
                if cursor.fetchone() is None:
                    continue

                target_column = preferred_column
                if not target_column:
                    table_info = cursor.execute(f'PRAGMA table_info("{table_name}")').fetchall()
                    columns = {str(row[1]) for row in table_info}
                    target_column = next(
                        (column for column in candidate_columns if column in columns),
                        None,
                    )
                if not target_column:
                    continue

                try:
                    rows = cursor.execute(
                        f'SELECT DISTINCT "{target_column}" FROM "{table_name}" '
                        f'WHERE "{target_column}" IS NOT NULL AND TRIM("{target_column}") <> \'\''
                    ).fetchall()
                except sqlite3.Error:
                    continue

                for (value,) in rows:
                    normalized = str(value or "").strip()
                    if normalized:
                        uploader_ids.add(normalized)

        return uploader_ids
