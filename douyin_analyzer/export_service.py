from __future__ import annotations

from .exporters import (
    save_all_videos_to_csv,
    save_cache_inventory_to_csv,
    save_full_fetch_mismatch_to_csv,
    save_to_csv,
    save_video_duration_analysis_to_csv,
    save_video_duration_report,
)


class DouyinExportService:
    def __init__(self, config) -> None:
        self.config = config

    def save_main_results(self, results, *, merge_existing: bool = False):
        return save_to_csv(self.config, self._rows(results), merge_existing=merge_existing)

    def save_summary_analysis(self, summary_rows, *, merge_existing: bool = False):
        return save_video_duration_analysis_to_csv(
            self.config,
            self._rows(summary_rows),
            merge_existing=merge_existing,
        )

    def save_cache_inventory(self, cache_rows):
        return save_cache_inventory_to_csv(self.config, self._rows(cache_rows))

    def save_all_videos(self, all_video_rows):
        return save_all_videos_to_csv(self.config, self._rows(all_video_rows))

    def save_duration_report(self, summary_rows, video_count: int):
        return save_video_duration_report(self.config, self._rows(summary_rows), video_count)

    def save_full_fetch_mismatch(self, rows):
        return save_full_fetch_mismatch_to_csv(self.config, self._rows(rows))

    def save_duration_outputs(self, all_video_rows, summary_rows):
        self.save_all_videos(all_video_rows)
        self.save_duration_report(summary_rows, len(all_video_rows))
        return {
            "all_videos": self.config.all_videos_csv,
            "report": self.config.video_duration_report_md,
        }

    @staticmethod
    def _rows(items):
        rows = []
        for item in items or []:
            if hasattr(item, "to_dict"):
                rows.append(item.to_dict())
            elif isinstance(item, dict):
                rows.append(item)
        return rows
