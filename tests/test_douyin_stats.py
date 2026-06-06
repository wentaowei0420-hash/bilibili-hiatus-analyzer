import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from backend import gui_data


def test_build_douyin_mode_stats_includes_counts_and_full_breakdown():
    active_rows = [
        {
            "has_counts_cache": "是",
            "has_full_cache": "是",
            "progress_cache_due": "",
        },
        {
            "has_counts_cache": "是",
            "has_full_cache": "是",
            "progress_cache_due": "是",
        },
        {
            "has_counts_cache": "是",
            "has_full_cache": "",
            "progress_cache_due": "",
        },
    ]

    modes = gui_data._build_douyin_mode_stats(active_rows)

    assert set(modes) == {"counts", "full"}
    assert modes["counts"]["count"] == 3
    assert modes["full"]["count"] == 2
    assert modes["full"]["valid_count"] == 1
    assert modes["full"]["expired_count"] == 1
    assert modes["full"]["unfetched_count"] == 1
