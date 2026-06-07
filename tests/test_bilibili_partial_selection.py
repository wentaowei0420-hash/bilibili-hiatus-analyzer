from types import SimpleNamespace

from bilibili_analyzer.archive import archive_creators
from bilibili_analyzer.analyzer import BilibiliHiatusAnalyzer


class DummyReporter:
    def message(self, *_args, **_kwargs):
        pass


class DummyApi:
    def __init__(self, followings):
        self._followings = followings

    def get_followings_list(self):
        return [dict(item) for item in self._followings]

    def get_uploader_relation_stat(self, mid, _uname):
        return {
            "follower_count": 1000 - int(mid),
            "total_favorited": 0,
            "total_view_count": 0,
        }


class DummyCache:
    def __init__(self, mids, *, duration_due=()):
        self._mids = {str(mid) for mid in mids}
        self._duration_due = {str(mid) for mid in duration_due}
        self.saved_followings = []

    def load_followings_cache(self):
        return []

    def save_followings_cache(self, followings):
        self.saved_followings = list(followings or [])

    def load_precise_progress(self):
        return {
            mid: {
                "uploader_id": mid,
                "uploader_name": f"UP {mid}",
                "data_source": "video_api",
                "cached_at": 1,
            }
            for mid in self._mids
        }

    def load_video_duration_progress(self):
        return {
            mid: {
                "uploader_id": mid,
                "uploader_name": f"UP {mid}",
                "cached_at": 1,
                "summary": {"total_videos": 1},
                "videos": [],
            }
            for mid in self._mids
        }

    def should_refresh_precise_cache(self, following, cached_result):
        return not isinstance(cached_result, dict)

    def should_refresh_video_duration_cache(self, following, progress_entry):
        return str(following.get("mid")) in self._duration_due or not isinstance(progress_entry, dict)


def _analyzer(*, enable_video_duration_analysis, max_followings=2, duration_due=(), export_store_db=None):
    followings = [
        {"mid": str(index), "uname": f"UP {index}"}
        for index in range(1, 6)
    ]
    config = SimpleNamespace(
        enable_video_duration_analysis=enable_video_duration_analysis,
        export_store_db=export_store_db,
    )
    return BilibiliHiatusAnalyzer(
        config,
        DummyApi(followings),
        DummyCache([item["mid"] for item in followings], duration_due=duration_due),
        max_followings=max_followings,
        reporter=DummyReporter(),
        export_outputs=False,
    )


def test_full_partial_fetch_selects_due_followings_before_top_slice(monkeypatch):
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_BY", "follower_count")
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_DESC", "true")
    analyzer = _analyzer(enable_video_duration_analysis=True, duration_due=("4", "5"))

    selected, partial_run = analyzer._fetch_and_prepare_followings()

    assert partial_run is True
    assert [item["mid"] for item in selected] == ["4", "5"]


def test_basic_partial_fetch_keeps_top_slice(monkeypatch):
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_BY", "follower_count")
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_DESC", "true")
    analyzer = _analyzer(enable_video_duration_analysis=False, duration_due=("4", "5"))

    selected, partial_run = analyzer._fetch_and_prepare_followings()

    assert partial_run is True
    assert [item["mid"] for item in selected] == ["1", "2"]


def test_basic_mode_saves_followings_cache(monkeypatch):
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_BY", "follower_count")
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_DESC", "true")
    analyzer = _analyzer(enable_video_duration_analysis=False, duration_due=())

    analyzer._fetch_and_prepare_followings()

    assert [item["mid"] for item in analyzer.cache_repository.raw.saved_followings] == [
        "1",
        "2",
        "3",
        "4",
        "5",
    ]


def test_active_archived_bilibili_followings_are_skipped(monkeypatch, tmp_path):
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_BY", "follower_count")
    monkeypatch.setenv("BILIBILI_FETCH_ORDER_DESC", "true")
    db_path = tmp_path / "bilibili_export_store.db"
    archive_creators(
        db_path,
        [
            {
                "uploader_id": "1",
                "uploader_name": "UP 1",
                "homepage_url": "https://space.bilibili.com/1",
                "manual_grade": "",
                "final_grade": "",
                "final_score": 0.0,
                "confidence": "",
                "follower_count": 999,
                "total_favorited": 0,
                "total_view_count": 0,
                "published_video_count": 1,
                "cached_video_count": 1,
                "latest_video_title": "Video 1",
                "latest_publish_time": "2026-01-01",
                "inactive_days": 150.0,
                "avg_update_days": 10.0,
                "cached_modes": "full",
                "last_fetch_mode": "full",
                "has_full_cache": "是",
                "source_snapshot": {},
            }
        ],
    )
    analyzer = _analyzer(
        enable_video_duration_analysis=False,
        duration_due=(),
        export_store_db=db_path,
    )

    selected, partial_run = analyzer._fetch_and_prepare_followings()

    assert partial_run is True
    assert [item["mid"] for item in selected] == ["2", "3"]
