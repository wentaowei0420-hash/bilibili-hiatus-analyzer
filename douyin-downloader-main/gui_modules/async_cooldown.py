from __future__ import annotations

import asyncio

from .detail_failure_cooldown import DetailFailureCooldownDecision


async def sleep_for_decision(decision: DetailFailureCooldownDecision) -> None:
    if not decision.should_sleep or decision.seconds <= 0:
        return
    await asyncio.sleep(decision.seconds)
