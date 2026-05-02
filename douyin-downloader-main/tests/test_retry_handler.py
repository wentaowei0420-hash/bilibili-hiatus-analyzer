import pytest

from control.retry_handler import RetryHandler


@pytest.mark.asyncio
async def test_retry_handler_succeeds_on_first_try():
    handler = RetryHandler(max_retries=3)
    call_count = 0

    async def task():
        nonlocal call_count
        call_count += 1
        return "ok"

    result = await handler.execute_with_retry(task)
    assert result == "ok"
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_handler_retries_then_succeeds():
    handler = RetryHandler(max_retries=3)
    handler.retry_delays = [0, 0, 0]
    call_count = 0

    async def task():
        nonlocal call_count
        call_count += 1
        if call_count < 3:
            raise RuntimeError("transient error")
        return "recovered"

    result = await handler.execute_with_retry(task)
    assert result == "recovered"
    assert call_count == 3


@pytest.mark.asyncio
async def test_retry_handler_raises_after_exhaustion():
    handler = RetryHandler(max_retries=2)
    handler.retry_delays = [0, 0]

    async def task():
        raise ValueError("permanent")

    with pytest.raises(ValueError, match="permanent"):
        await handler.execute_with_retry(task)


@pytest.mark.asyncio
async def test_retry_handler_can_downgrade_final_log_level(caplog):
    handler = RetryHandler(max_retries=2)
    handler.retry_delays = [0, 0]

    async def task():
        raise ValueError("optional failure")

    with pytest.raises(ValueError, match="optional failure"):
        await handler.execute_with_retry(task, final_log_level="warning")

    assert "All 2 attempts failed: optional failure" in caplog.text
