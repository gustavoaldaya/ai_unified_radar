"""Rate limiting with exponential backoff and a hard-limit guard.

Inherited lesson (FinOps agent): do **not** retry inside the backoff window of
APIs with a hard quota (e.g. Azure Cost Management ~15 reads/h). For those
sources set ``no_retry_on_hard_limit=True`` so the limiter raises
:class:`HardRateLimitExceeded` instead of sleeping-and-retrying.

``sleep`` and ``clock`` are injectable so tests can assert backoff durations
without real waiting (AC-1.3-4).
"""

from __future__ import annotations

import time
from collections import deque
from collections.abc import Callable

_SECONDS_PER_HOUR = 3600.0


class HardRateLimitExceeded(RuntimeError):
    """Hard-limited source exhausted; caller MUST NOT retry."""


class RateLimiter:
    def __init__(
        self,
        *,
        min_interval_s: float = 0.0,
        max_per_hour: int | None = None,
        base_backoff_s: float = 1.0,
        max_backoff_s: float = 60.0,
        no_retry_on_hard_limit: bool = False,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._min_interval = min_interval_s
        self._max_per_hour = max_per_hour
        self._base = base_backoff_s
        self._max = max_backoff_s
        self._no_retry = no_retry_on_hard_limit
        self._sleep = sleep
        self._clock = clock
        self._calls: deque[float] = deque()
        self._last_call: float | None = None
        self._attempt = 0

    def _evict(self, now: float) -> None:
        while self._calls and (now - self._calls[0]) >= _SECONDS_PER_HOUR:
            self._calls.popleft()

    def before_request(self) -> None:
        """Block until it is safe to issue the next request."""
        now = self._clock()
        if self._max_per_hour is not None:
            self._evict(now)
            if len(self._calls) >= self._max_per_hour:
                if self._no_retry:
                    raise HardRateLimitExceeded(
                        f"hourly quota {self._max_per_hour} exhausted; no retry"
                    )
                wait = _SECONDS_PER_HOUR - (now - self._calls[0])
                self._sleep(max(0.0, wait))
                now = self._clock()
                self._evict(now)
        if self._min_interval > 0.0 and self._last_call is not None:
            elapsed = now - self._last_call
            if elapsed < self._min_interval:
                self._sleep(self._min_interval - elapsed)
                now = self._clock()
        self._last_call = now
        self._calls.append(now)

    def on_response(self, status_code: int, retry_after: float | None = None) -> float:
        """Handle a response. On 429, sleep per ``Retry-After`` (no immediate
        retry) and return the slept duration. Returns 0.0 otherwise.
        """
        if status_code == 429:
            if self._no_retry:
                raise HardRateLimitExceeded("429 on hard-limited source; no retry")
            self._attempt += 1
            if retry_after is not None:
                wait = float(retry_after)
            else:
                wait = min(self._base * (2 ** (self._attempt - 1)), self._max)
            self._sleep(wait)
            return wait
        self._attempt = 0
        return 0.0
