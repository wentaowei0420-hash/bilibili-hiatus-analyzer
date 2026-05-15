from __future__ import annotations

import time
from typing import Any

from .domain_models import AnalysisResult, CreatorProfile, VideoEntry


class AnalyzerCacheRepository:
    """Thin repository facade over platform cache stores.

    The existing cache files are kept intact. This facade gives analyzers a
    stable dependency to migrate toward while old cache-store methods continue
    to work through delegation.
    """

    def __init__(self, cache_store: Any, *, platform: str) -> None:
        self._cache_store = cache_store
        self.platform = platform

    @property
    def raw(self) -> Any:
        return self._cache_store

    def __getattr__(self, name: str):
        return getattr(self._cache_store, name)

    def creator_from_cache(self, data: dict[str, Any]) -> CreatorProfile:
        return CreatorProfile.from_mapping(data or {}, platform=self.platform)

    def video_from_cache(self, data: dict[str, Any]) -> VideoEntry:
        return VideoEntry.from_mapping(data or {}, platform=self.platform)

    def result_from_cache(self, data: dict[str, Any]) -> AnalysisResult:
        return AnalysisResult.from_mapping(data or {}, platform=self.platform)

    def normalize_results(self, rows) -> list[AnalysisResult]:
        return [
            self.result_from_cache(row)
            for row in (rows or [])
            if isinstance(row, dict)
        ]

    @staticmethod
    def _merge_creator_metrics(existing: CreatorProfile | None, incoming: CreatorProfile) -> CreatorProfile:
        if existing is None:
            return incoming
        existing.follower_count = max(existing.follower_count, incoming.follower_count)
        existing.published_video_count = max(
            existing.published_video_count,
            incoming.published_video_count,
        )
        existing.average_like_count = max(
            existing.average_like_count,
            incoming.average_like_count,
        )
        return existing

    def _remember_creator_metric(
        self,
        metric_index: dict[str, CreatorProfile],
        profile: CreatorProfile,
        fallback_id: Any = "",
    ) -> None:
        key = str(profile.uploader_id or fallback_id or "").strip()
        if not key:
            return
        metric_index[key] = self._merge_creator_metrics(metric_index.get(key), profile)

    def get_creator_metric_index(self) -> dict[str, CreatorProfile]:
        metric_index: dict[str, CreatorProfile] = {}

        for uploader_id, result in (self.load_precise_results() or {}).items():
            if not isinstance(result, dict):
                continue
            self._remember_creator_metric(
                metric_index,
                self.creator_from_cache(result),
                uploader_id,
            )

        for uploader_id, entry in (self.load_duration_progress() or {}).items():
            if not isinstance(entry, dict):
                continue
            following = entry.get("following", {}) if isinstance(entry.get("following"), dict) else {}
            summary = entry.get("summary", {}) if isinstance(entry.get("summary"), dict) else {}
            profile = self.creator_from_cache(
                {
                    **summary,
                    **following,
                    "uploader_id": (
                        (following or {}).get("mid")
                        or summary.get("uploader_id")
                        or entry.get("uploader_id")
                        or uploader_id
                    ),
                    "uploader_name": (
                        (following or {}).get("uname")
                        or summary.get("uploader_name")
                        or entry.get("uploader_name")
                        or ""
                    ),
                    "published_video_count": (
                        summary.get("total_videos")
                        or summary.get("published_video_count")
                    ),
                }
            )
            self._remember_creator_metric(metric_index, profile, uploader_id)

        return metric_index

    def get_creator_metrics(self, uploader_id: Any) -> CreatorProfile | None:
        return self.get_creator_metric_index().get(str(uploader_id or "").strip())

    @staticmethod
    def duration_progress_entry(progress: Any, uploader_id: Any) -> dict[str, Any]:
        if not isinstance(progress, dict):
            return {}
        entry = progress.get(str(uploader_id or ""))
        return entry if isinstance(entry, dict) else {}

    @staticmethod
    def duration_progress_payload(entry: Any) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        if not isinstance(entry, dict):
            return [], {}
        videos = entry.get("videos", [])
        summary = entry.get("summary", {})
        return (
            videos if isinstance(videos, list) else [],
            summary if isinstance(summary, dict) else {},
        )

    @staticmethod
    def iter_duration_progress_entries(progress: Any):
        if not isinstance(progress, dict):
            return
        for uploader_id, entry in progress.items():
            if isinstance(entry, dict):
                yield str(uploader_id), entry

    @staticmethod
    def build_duration_progress_entry(
        following: dict[str, Any],
        videos,
        summary: dict[str, Any],
        *,
        cached_at: int | None = None,
    ) -> dict[str, Any]:
        following = following or {}
        return {
            "uploader_name": following.get("uname") or following.get("uploader_name") or "",
            "uploader_id": following.get("mid") or following.get("uploader_id") or "",
            "cached_at": int(time.time()) if cached_at is None else cached_at,
            "videos": videos or [],
            "summary": summary or {},
        }

    def load_followings(self):
        return self._cache_store.load_followings_cache()

    def load_followings_payload(self):
        return self._cache_store.load_followings_cache_payload()

    def load_run_progress(self):
        return self._cache_store.load_progress()

    def load_precise_results(self):
        return self._cache_store.load_precise_progress()

    def load_duration_progress(self):
        return self._cache_store.load_video_duration_progress()

    def load_failed_profile_keys(self, *args, **kwargs):
        return self._cache_store.load_failed_profile_keys(*args, **kwargs)

    def should_refresh_precise_result(self, following, cached_result) -> bool:
        return self._cache_store.should_refresh_precise_cache(following, cached_result)

    def should_refresh_duration_result(self, following, progress_entry) -> bool:
        return self._cache_store.should_refresh_video_duration_cache(following, progress_entry)

    def should_refresh_profile(self, user, entry, **kwargs):
        return self._cache_store.should_refresh_cache(user, entry, **kwargs)

    def refresh_result_runtime_fields(self, result) -> Any:
        return self._cache_store.refresh_result_runtime_fields(result)

    def normalize_homepage_url(self, homepage: str) -> str:
        return self._cache_store._normalize_homepage_url(homepage)

    def save_followings_cache(self, followings) -> Any:
        return self._cache_store.save_followings_cache(followings)

    def save_followings(self, followings) -> Any:
        return self.save_followings_cache(followings)

    def save_progress(self, progress) -> Any:
        return self._cache_store.save_progress(progress)

    def save_run_progress(self, progress) -> Any:
        return self.save_progress(progress)

    def save_precise_progress(self, progress) -> Any:
        return self._cache_store.save_precise_progress(progress)

    def save_precise_results(self, progress) -> Any:
        return self.save_precise_progress(progress)

    def save_video_duration_progress(self, progress) -> Any:
        return self._cache_store.save_video_duration_progress(progress)

    def save_duration_progress(self, progress) -> Any:
        return self.save_video_duration_progress(progress)

    def append_fetch_manifest(self, row: dict[str, Any]) -> Any:
        return self._cache_store.append_fetch_manifest(row)

    def record_fetch_manifest(self, row: dict[str, Any]) -> Any:
        return self.append_fetch_manifest(row)

    def append_failed_profile(self, *args, **kwargs) -> Any:
        return self._cache_store.append_failed_profile(*args, **kwargs)

    def record_failed_profile(self, *args, **kwargs) -> Any:
        return self.append_failed_profile(*args, **kwargs)

    def upsert_video_state_from_progress_entries(self, entries, *, source_mode: str) -> Any:
        return self._cache_store.upsert_video_state_from_progress_entries(
            entries,
            source_mode=source_mode,
        )

    def save_video_state_entries(self, entries, *, source_mode: str) -> Any:
        return self.upsert_video_state_from_progress_entries(
            entries,
            source_mode=source_mode,
        )

    def resolve_full_status_reset(self, uploader_id, **kwargs) -> Any:
        return self._cache_store.resolve_full_status_reset(uploader_id, **kwargs)

    def resolve_full_status_resets(self, uploader_ids, **kwargs) -> Any:
        return self._cache_store.resolve_full_status_resets(uploader_ids, **kwargs)

    def remove_unfollowed_user(self, *args, **kwargs) -> Any:
        return self._cache_store.remove_unfollowed_user(*args, **kwargs)

    def remove_unfollowed_profile(self, *args, **kwargs) -> Any:
        return self.remove_unfollowed_user(*args, **kwargs)
