from __future__ import annotations


class NoopExportService:
    """Export port that deliberately suppresses local file/report writes."""

    def save_main_results(self, *args, **kwargs):
        return None

    def save_video_duration_outputs(self, *args, **kwargs):
        return {}

    def save_summary_analysis(self, *args, **kwargs):
        return None

    def save_cache_inventory(self, *args, **kwargs):
        return None

    def save_all_videos(self, *args, **kwargs):
        return None

    def save_duration_report(self, *args, **kwargs):
        return None

    def save_duration_outputs(self, *args, **kwargs):
        return {}

    def save_full_fetch_mismatch(self, *args, **kwargs):
        return None
