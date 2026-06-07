from pathlib import Path
from types import SimpleNamespace

from bilibili_analyzer.analyzer import BilibiliHiatusAnalyzer


class DummyProgress:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def add_task(self, *_args, **_kwargs):
        return "task"

    def advance(self, *_args, **_kwargs):
        pass


class DummyTable:
    def add_row(self, *_args, **_kwargs):
        pass


class DummyReporter:
    def message(self, *_args, **_kwargs):
        pass

    def panel(self, *_args, **_kwargs):
        pass

    def wait(self, *_args, **_kwargs):
        pass

    def progress(self):
        return DummyProgress()

    def create_table(self, *_args, **_kwargs):
        return DummyTable()

    def render(self, *_args, **_kwargs):
        pass


class DummyExportService:
    def __init__(self):
        self.saved_main_results = []

    def save_main_results(self, results, *, merge_existing=False):
        self.saved_main_results = list(results or [])
        self.merge_existing = bool(merge_existing)

    def save_video_duration_outputs(self, all_video_rows, summary_rows):
        self.all_video_rows = list(all_video_rows or [])
        self.summary_rows = list(summary_rows or [])
        return {}


class CountingApi:
    def __init__(self, followings):
        self.followings = [dict(item) for item in followings]
        self.get_followings_list_calls = 0
        self.get_uploader_relation_stat_calls = 0
        self.get_latest_video_calls = 0
        self.get_all_videos_for_up_calls = 0

    def check_cookie(self):
        pass

    def get_followings_list(self):
        self.get_followings_list_calls += 1
        return [dict(item) for item in self.followings]

    def get_uploader_relation_stat(self, mid, _uname):
        self.get_uploader_relation_stat_calls += 1
        return {
            "follower_count": 1000 - int(mid),
            "total_favorited": 2000 - int(mid),
            "total_view_count": 3000 - int(mid),
        }

    def get_latest_video(self, _mid, _uname):
        self.get_latest_video_calls += 1
        return None

    def get_all_videos_for_up(self, mid, uname):
        self.get_all_videos_for_up_calls += 1
        return [
            {
                "uploader_name": uname,
                "uploader_id": mid,
                "video_title": f"Video {mid}",
                "bvid": f"BV{mid}",
                "publish_date": "2026-06-01",
                "publish_timestamp": 1717689600 + int(mid),
                "duration_text": "01:00",
                "duration_seconds": 60,
                "duration_category": "中视频",
                "like_count": 10,
                "coin_count": 1,
                "favorite_count": 1,
                "like_count_fetched": True,
                "view_count": 100,
                "video_url": f"https://www.bilibili.com/video/BV{mid}",
            }
        ]


class MemoryCache:
    def __init__(self, followings):
        self.followings = [dict(item) for item in followings]
        self.precise_progress = {}
        self.duration_progress = {}

    def load_followings_cache(self):
        return [dict(item) for item in self.followings]

    def save_followings_cache(self, followings):
        self.followings = [dict(item) for item in (followings or [])]

    def load_precise_progress(self):
        return dict(self.precise_progress)

    def save_precise_progress(self, results_by_mid):
        self.precise_progress = dict(results_by_mid or {})

    def load_video_duration_progress(self):
        return dict(self.duration_progress)

    def save_video_duration_progress(self, progress):
        self.duration_progress = dict(progress or {})

    def should_refresh_precise_cache(self, _following, _cached_result):
        return True

    def should_refresh_video_duration_cache(self, _following, _progress_entry):
        return True

    def refresh_result_runtime_fields(self, result):
        return result


def test_full_mode_reuses_basic_followings_cache_and_skips_precise_fetch(monkeypatch):
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_BY", "follower_count")
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_DESC", "true")

    followings = [
        {
            "mid": "1",
            "uname": "UP 1",
            "group_id_text": "0",
            "group_name_text": "默认分组",
            "follower_count": 1000,
            "total_favorited": 2000,
            "total_view_count": 3000,
        },
        {
            "mid": "2",
            "uname": "UP 2",
            "group_id_text": "0",
            "group_name_text": "默认分组",
            "follower_count": 900,
            "total_favorited": 1900,
            "total_view_count": 2900,
        },
    ]
    config = SimpleNamespace(
        enable_video_duration_analysis=True,
        enable_real_video_like_fetch=False,
        enable_cached_video_like_backfill=False,
        video_stat_max_requests_per_run=0,
        video_analysis_workers=1,
        video_analysis_batch_size=5,
        video_analysis_batch_cooldown=0,
        output_csv=Path("bilibili_hiatus_ranking.csv"),
        all_videos_csv=Path("all_videos.csv"),
        video_duration_analysis_csv=Path("video_duration_analysis.csv"),
        video_duration_report_md=Path("video_duration_report.md"),
    )
    api = CountingApi(followings)
    cache = MemoryCache(followings)
    export_service = DummyExportService()
    analyzer = BilibiliHiatusAnalyzer(
        config,
        api,
        cache,
        reporter=DummyReporter(),
        export_service=export_service,
        export_outputs=False,
    )

    results = analyzer.analyze_hiatus()

    assert len(results) == 2
    assert api.get_followings_list_calls == 0
    assert api.get_uploader_relation_stat_calls == 0
    assert api.get_latest_video_calls == 0
    assert api.get_all_videos_for_up_calls == 2
