import threading
import time
from pathlib import Path
import sys
from typing import Optional

sys.path.insert(0, str(Path(__file__).parent.parent.parent))
from config import SEC_RATE_LIMIT, YAHOO_RATE_LIMIT


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


sec_limiter   = RateLimiter(rate=float(SEC_RATE_LIMIT),   per=1.0)
yahoo_limiter = RateLimiter(rate=float(YAHOO_RATE_LIMIT), per=1.0)
