"""Token-bucket rate limiter. Async-friendly, monotonic clock."""

from __future__ import annotations

import asyncio
import time


class RateLimitExceeded(Exception):
    pass


class TokenBucket:
    def __init__(self, rate_per_sec: float, capacity: float):
        if rate_per_sec <= 0 or capacity <= 0:
            raise ValueError("rate_per_sec and capacity must be > 0")
        self._rate = rate_per_sec
        self._capacity = capacity
        self._tokens = capacity
        self._updated = time.monotonic()
        self._lock = asyncio.Lock()

    def _refill(self) -> None:
        now = time.monotonic()
        elapsed = now - self._updated
        self._tokens = min(self._capacity, self._tokens + elapsed * self._rate)
        self._updated = now

    async def try_acquire(self, n: float = 1.0) -> bool:
        async with self._lock:
            self._refill()
            if self._tokens >= n:
                self._tokens -= n
                return True
            return False

    async def acquire(self, n: float = 1.0) -> None:
        while True:
            if await self.try_acquire(n):
                return
            # not enough tokens; sleep until at least n tokens are available
            async with self._lock:
                self._refill()
                deficit = n - self._tokens
            wait = max(deficit / self._rate, 0.01)
            await asyncio.sleep(wait)
