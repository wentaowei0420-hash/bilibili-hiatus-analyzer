import random
import time


class SyncRetryHandler:
    def __init__(self, max_retries=3, delays=None, jitter=(0.0, 0.0), on_retry=None):
        self.max_retries = max(1, int(max_retries or 1))
        self.delays = list(delays or [1, 2, 5])
        self.jitter = tuple(jitter or (0.0, 0.0))
        self.on_retry = on_retry

    def execute(self, func, *args, **kwargs):
        last_error = None
        for attempt in range(1, self.max_retries + 1):
            try:
                return func(*args, **kwargs)
            except Exception as exc:
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = self.delays[min(attempt - 1, len(self.delays) - 1)]
                delay += random.uniform(*self.jitter)
                if self.on_retry:
                    self.on_retry(attempt, exc, delay)
                time.sleep(max(0.0, delay))
        raise last_error
