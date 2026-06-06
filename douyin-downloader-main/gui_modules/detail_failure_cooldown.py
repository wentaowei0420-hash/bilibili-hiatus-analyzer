from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class DetailFailureCooldownDecision:
    failure_count: int
    seconds: float = 0.0
    should_sleep: bool = False
    message: str = ""


class DetailFailureCooldownPolicy:
    def __init__(
        self,
        random_seconds: Callable[[float, float], float] | None = None,
    ):
        self._failure_count = 0
        self._random_seconds = random_seconds or random.uniform

    @property
    def failure_count(self) -> int:
        return self._failure_count

    def reset(self) -> None:
        self._failure_count = 0

    def record_detail_api_failure(self) -> DetailFailureCooldownDecision:
        self._failure_count += 1

        if self._failure_count >= 30:
            seconds = float(self._random_seconds(5.0, 10.0))
            return DetailFailureCooldownDecision(
                failure_count=self._failure_count,
                seconds=seconds,
                should_sleep=True,
                message=(
                    f"连续详情接口失败 {self._failure_count} 次，疑似触发风控，"
                    f"冷却 {seconds:.1f} 秒后继续"
                ),
            )

        if self._failure_count >= 10:
            seconds = float(self._random_seconds(1.0, 3.0))
            return DetailFailureCooldownDecision(
                failure_count=self._failure_count,
                seconds=seconds,
                should_sleep=True,
                message=(
                    f"连续详情接口失败 {self._failure_count} 次，"
                    f"冷却 {seconds:.1f} 秒后继续"
                ),
            )

        return DetailFailureCooldownDecision(failure_count=self._failure_count)
