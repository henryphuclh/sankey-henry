import threading
import time
from typing import Optional


class RateLimiter:
    """Thread-safe token bucket rate limiter."""

    def __init__(self, rate: float = 8.0, per: float = 1.0):
        self.rate = rate          # tokens per `per` seconds
        self.per = per
        self._tokens = rate
        self._last_check = time.monotonic()
        self._lock = threading.Lock()

    def acquire(self, tokens: float = 1.0) -> None:
        """Block until `tokens` tokens are available."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_check
            self._last_check = now
            self._tokens = min(self.rate, self._tokens + elapsed * (self.rate / self.per))

            if self._tokens < tokens:
                wait = (tokens - self._tokens) / (self.rate / self.per)
                time.sleep(wait)
                self._tokens = 0.0
            else:
                self._tokens -= tokens

    def __call__(self, tokens: float = 1.0) -> None:
        self.acquire(tokens)


sec_limiter   = RateLimiter(rate=8.0, per=1.0)
yahoo_limiter = RateLimiter(rate=2.0, per=1.0)
