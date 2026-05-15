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
