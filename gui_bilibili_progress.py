from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class BilibiliProgressUpdate:
    current: int
    total: int
    label: str


class BilibiliProgressAdapter:
    """Translate Bilibili analyzer stages into one GUI progress model.

    Bilibili precise fetch runs in numbered batches such as
    ``精确抓取 26-50 | 10/25``. The generic GUI parser treats that ``10`` as a
    task-local count, so the visible progress jumps or stalls. This adapter
    folds the batch range into a phase-local absolute count.
    """

    _PROGRESS_RE = re.compile(
        r"(?P<description>[^|\r\n]+?)\s*\|\s*"
        r"(?P<current>\d+(?:\.\d+)?)\s*/\s*(?P<total>\d+|\?)"
    )
    _PRECISE_BATCH_RE = re.compile(r"^精确抓取\s+(?P<start>\d+)\s*-\s*(?P<end>\d+)$")
    _PRECISE_RETRY_RE = re.compile(r"^精确抓取\s+补抓第(?P<round>\d+)轮$")
    _PENDING_PRECISE_RE = re.compile(r"仍有\s*(?P<count>\d+)\s*位UP主需要精确抓取")
    _PARTIAL_RUN_RE = re.compile(r"本轮仅处理排序靠前的\s*(?P<count>\d+)\s*位")
    _FOLLOWINGS_READY_RE = re.compile(r"成功获取\s*(?P<count>\d+)\s*位关注的UP主")
    _FOLLOWINGS_PROGRESS_RE = re.compile(
        r"获取B站关注列表\s*\(\s*(?P<current>\d+)\s*/\s*(?P<total>\d+)\s*\)"
    )
    _PRECISE_COOLDOWN_RE = re.compile(r"已完成\s*(?P<count>\d+)\s*位UP主，批次冷却")
    _DURATION_COOLDOWN_RE = re.compile(r"视频分析已完成\s*(?P<count>\d+)\s*位UP主")

    def __init__(self) -> None:
        self.reset()

    def reset(self) -> None:
        self._phase = ""
        self._followings_total: int | None = None
        self._precise_total: int | None = None
        self._duration_total: int | None = None
        self._like_backfill_total: int | None = None

    def update_from_log(self, line: str) -> BilibiliProgressUpdate | None:
        text = str(line or "").strip()
        if not text:
            return None

        metadata_update = self._read_metadata(text)
        if metadata_update:
            return metadata_update

        progress_match = self._PROGRESS_RE.search(text)
        if progress_match:
            return self._from_progress_line(
                progress_match.group("description").strip(),
                self._safe_int(progress_match.group("current")),
                self._safe_int(progress_match.group("total")),
            )

        cooldown_update = self._read_cooldown_progress(text)
        if cooldown_update:
            return cooldown_update

        return None

    def update_from_job(
        self,
        message: Any,
        current: Any,
        total: Any,
    ) -> BilibiliProgressUpdate | None:
        description = str(message or "").strip()
        parsed_current = self._safe_int(current)
        parsed_total = self._safe_int(total)
        if not description or parsed_current is None or parsed_total is None:
            return None
        return self._from_progress_line(description, parsed_current, parsed_total)

    def _read_metadata(self, text: str) -> BilibiliProgressUpdate | None:
        match = self._PARTIAL_RUN_RE.search(text)
        if match:
            self._followings_total = self._safe_int(match.group("count"))
            return self._update(
                "followings",
                self._followings_total or 0,
                self._followings_total or 0,
                "B站关注列表准备完成",
            )

        match = self._FOLLOWINGS_READY_RE.search(text)
        if match:
            self._followings_total = self._safe_int(match.group("count"))
            return self._update(
                "followings",
                self._followings_total or 0,
                self._followings_total or 0,
                "B站关注列表获取完成",
            )

        match = self._PENDING_PRECISE_RE.search(text)
        if match:
            self._precise_total = self._safe_int(match.group("count"))
            return self._update("precise", 0, self._precise_total or 0, "B站精确抓取准备中")

        return None

    def _read_cooldown_progress(self, text: str) -> BilibiliProgressUpdate | None:
        match = self._DURATION_COOLDOWN_RE.search(text)
        if match and self._duration_total:
            current = min(self._safe_int(match.group("count")) or 0, self._duration_total)
            return self._update("duration", current, self._duration_total, "B站完整模式视频分析冷却中")

        match = self._PRECISE_COOLDOWN_RE.search(text)
        if match and self._precise_total:
            current = min(self._safe_int(match.group("count")) or 0, self._precise_total)
            return self._update("precise", current, self._precise_total, "B站精确抓取批次冷却中")

        return None

    def _from_progress_line(
        self,
        description: str,
        current: int | None,
        total: int | None,
    ) -> BilibiliProgressUpdate | None:
        if current is None or total is None or total <= 0:
            return None

        followings_match = self._FOLLOWINGS_PROGRESS_RE.search(description)
        if followings_match:
            current = self._safe_int(followings_match.group("current")) or current
            total = self._safe_int(followings_match.group("total")) or total
            self._followings_total = total
            return self._update("followings", current, total, "B站关注列表获取中")

        batch_match = self._PRECISE_BATCH_RE.match(description)
        if batch_match:
            start = self._safe_int(batch_match.group("start")) or 1
            end = self._safe_int(batch_match.group("end")) or start + total - 1
            phase_total = self._precise_total or max(end, start + total - 1)
            absolute_current = min(max(start - 1, 0) + current, phase_total)
            return self._update("precise", absolute_current, phase_total, "B站精确抓取中")

        if self._PRECISE_RETRY_RE.match(description):
            return self._update("precise_retry", current, total, "B站精确补抓中")

        if description == "全量视频时长分析":
            self._duration_total = total
            return self._update("duration", current, total, "B站完整模式视频分析中")

        if description == "补抓历史点赞":
            self._like_backfill_total = total
            return self._update("like_backfill", current, total, "B站历史点赞补抓中")

        return None

    def _update(self, phase: str, current: int, total: int, label: str) -> BilibiliProgressUpdate:
        self._phase = phase
        current = max(0, int(current or 0))
        total = max(0, int(total or 0))
        if total > 0:
            current = min(current, total)
            label = f"{label}：已处理 {current} / {total}"
        return BilibiliProgressUpdate(current=current, total=total, label=label)

    @staticmethod
    def _safe_int(value: Any) -> int | None:
        try:
            if value in (None, "", "?"):
                return None
            return int(float(str(value).strip()))
        except (TypeError, ValueError):
            return None
