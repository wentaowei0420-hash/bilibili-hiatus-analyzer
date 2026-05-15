from __future__ import annotations

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

    def remove_unfollowed_user(self, *args, **kwargs) -> Any:
        return self._cache_store.remove_unfollowed_user(*args, **kwargs)

    def remove_unfollowed_profile(self, *args, **kwargs) -> Any:
        return self.remove_unfollowed_user(*args, **kwargs)
