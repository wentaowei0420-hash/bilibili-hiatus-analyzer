from gui_modules.detail_failure_cooldown import DetailFailureCooldownPolicy


def test_detail_failure_cooldown_does_not_sleep_before_tenth_failure():
    policy = DetailFailureCooldownPolicy(random_seconds=lambda low, high: high)

    decision = None
    for _ in range(9):
        decision = policy.record_detail_api_failure()

    assert decision is not None
    assert decision.failure_count == 9
    assert decision.should_sleep is False
    assert decision.seconds == 0.0


def test_detail_failure_cooldown_uses_short_window_from_tenth_failure():
    policy = DetailFailureCooldownPolicy(random_seconds=lambda low, high: 2.3)

    for _ in range(9):
        policy.record_detail_api_failure()
    decision = policy.record_detail_api_failure()

    assert decision.failure_count == 10
    assert decision.should_sleep is True
    assert decision.seconds == 2.3
    assert "冷却 2.3 秒后继续" in decision.message
    assert "疑似触发风控" not in decision.message


def test_detail_failure_cooldown_uses_long_window_from_thirtieth_failure():
    policy = DetailFailureCooldownPolicy(random_seconds=lambda low, high: 7.8)

    for _ in range(29):
        policy.record_detail_api_failure()
    decision = policy.record_detail_api_failure()

    assert decision.failure_count == 30
    assert decision.should_sleep is True
    assert decision.seconds == 7.8
    assert "疑似触发风控" in decision.message


def test_detail_failure_cooldown_reset_clears_failure_counter():
    policy = DetailFailureCooldownPolicy(random_seconds=lambda low, high: 2.0)

    for _ in range(12):
        policy.record_detail_api_failure()
    policy.reset()
    decision = policy.record_detail_api_failure()

    assert policy.failure_count == 1
    assert decision.failure_count == 1
    assert decision.should_sleep is False
