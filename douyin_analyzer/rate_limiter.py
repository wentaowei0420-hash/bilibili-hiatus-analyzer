import random
import time
from collections import deque


class DouyinRateLimiter:
    def __init__(self, config):
        self.config = config
        self._last_request_at = 0.0
        self._violation_times = deque()
        self._conservative_until = 0.0
        self._conservative_notice_emitted = False

    def is_conservative_mode(self):
        return time.monotonic() < self._conservative_until

    def current_mode_label(self):
        return "conservative" if self.is_conservative_mode() else "normal"

    def _rate_multiplier(self):
        multiplier = float(getattr(self.config, "conservative_mode_rate_multiplier", 1.0) or 1.0)
        if self.is_conservative_mode():
            return max(1.0, multiplier)
        return 1.0

    def scaled_seconds(self, seconds):
        return float(seconds or 0) * self._rate_multiplier()

    def scaled_int(self, value, minimum=1):
        scaled = int(round(float(value or 0) / self._rate_multiplier()))
        return max(minimum, scaled)

    def current_fallback_max_ids(self):
        base_value = int(getattr(self.config, "video_browser_fallback_max_ids", 0) or 0)
        if not self.is_conservative_mode():
            return max(0, base_value)
        cap = int(getattr(self.config, "conservative_mode_fallback_max_ids", 0) or 0)
        if cap <= 0:
            return max(0, base_value)
        return min(max(0, base_value), cap)

    def effective_request_rate(self):
        base_rate = float(getattr(self.config, "request_rate_limit_per_second", 0) or 0)
        multiplier = self._rate_multiplier()
        if base_rate <= 0:
            return 0.0
        return base_rate / multiplier

    def before_request(self):
        rate_limit = self.effective_request_rate()
        if rate_limit <= 0:
            return
        minimum_interval = 1.0 / rate_limit
        now = time.monotonic()
        elapsed = now - self._last_request_at
        if elapsed < minimum_interval:
            time.sleep(minimum_interval - elapsed)
        self._last_request_at = time.monotonic()

    def compute_backoff_seconds(self, attempt, base_seconds=None, max_seconds=None):
        base = float(
            base_seconds
            if base_seconds is not None
            else getattr(self.config, "retry_backoff_base_seconds", 2.0)
        )
        cap = float(
            max_seconds
            if max_seconds is not None
            else getattr(self.config, "retry_backoff_max_seconds", 30.0)
        )
        backoff = min(cap, base * (2 ** max(0, attempt - 1)))
        return self.scaled_seconds(backoff) + random.uniform(0.2, 0.8)

    def _trim_violations(self):
        now = time.monotonic()
        window_seconds = float(getattr(self.config, "conservative_mode_duration_seconds", 300) or 300)
        while self._violation_times and now - self._violation_times[0] > window_seconds:
            self._violation_times.popleft()

    def _enter_conservative_mode_if_needed(self):
        self._trim_violations()
        trigger_count = int(getattr(self.config, "conservative_mode_trigger_count", 3) or 3)
        if len(self._violation_times) < trigger_count:
            return False
        duration = float(getattr(self.config, "conservative_mode_duration_seconds", 300) or 300)
        self._conservative_until = max(self._conservative_until, time.monotonic() + duration)
        return True

    def record_rate_limit(self):
        self._violation_times.append(time.monotonic())
        return self._enter_conservative_mode_if_needed()

    def record_service_error(self):
        self._violation_times.append(time.monotonic())
        return self._enter_conservative_mode_if_needed()

    def record_success(self):
        self._trim_violations()
        if not self.is_conservative_mode():
            self._conservative_notice_emitted = False

    def consume_conservative_notice(self):
        if not self.is_conservative_mode() or self._conservative_notice_emitted:
            return False
        self._conservative_notice_emitted = True
        return True
