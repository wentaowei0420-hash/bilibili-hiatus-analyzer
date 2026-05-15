from __future__ import annotations

from .exporters import (
    save_all_videos_to_csv,
    save_to_csv,
    save_video_duration_analysis_to_csv,
    save_video_duration_report,
)


class BilibiliExportService:
    def __init__(self, config) -> None:
        self.config = config

    def save_main_results(self, results, *, merge_existing: bool = False):
        return save_to_csv(self.config, results, merge_existing=merge_existing)

    def save_video_duration_outputs(self, all_video_rows, summary_rows):
        save_all_videos_to_csv(self.config, all_video_rows)
        save_video_duration_analysis_to_csv(self.config, summary_rows)
        save_video_duration_report(self.config, summary_rows, len(all_video_rows))
        return {
            "all_videos": self.config.all_videos_csv,
            "analysis": self.config.video_duration_analysis_csv,
            "report": self.config.video_duration_report_md,
        }
