import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from gui_bilibili_progress import BilibiliProgressAdapter


def test_precise_batches_are_folded_into_absolute_progress():
    adapter = BilibiliProgressAdapter()

    pending = adapter.update_from_log("🎬 仍有 60 位UP主需要精确抓取。")
    assert pending.current == 0
    assert pending.total == 60

    first_batch = adapter.update_from_log("精确抓取 1-25 | 10/25")
    assert first_batch.current == 10
    assert first_batch.total == 60

    second_batch = adapter.update_from_log("精确抓取 26-50 | 10/25")
    assert second_batch.current == 35
    assert second_batch.total == 60

    final_batch = adapter.update_from_log("精确抓取 51-60 | 10/10")
    assert final_batch.current == 60
    assert final_batch.total == 60


def test_structured_job_progress_uses_batch_description():
    adapter = BilibiliProgressAdapter()
    adapter.update_from_log("🎬 仍有 40 位UP主需要精确抓取。")

    update = adapter.update_from_job("精确抓取 26-40", 5, 15)

    assert update.current == 30
    assert update.total == 40


def test_duration_progress_resets_after_precise_phase():
    adapter = BilibiliProgressAdapter()
    adapter.update_from_log("🎬 仍有 60 位UP主需要精确抓取。")
    adapter.update_from_log("精确抓取 51-60 | 10/10")

    duration = adapter.update_from_job("全量视频时长分析", 3, 12)

    assert duration.current == 3
    assert duration.total == 12
    assert "完整模式" in duration.label


def test_followings_progress_is_supported_when_rich_line_is_visible():
    adapter = BilibiliProgressAdapter()

    update = adapter.update_from_log("获取B站关注列表 (50/120) | 50/120")

    assert update.current == 50
    assert update.total == 120
