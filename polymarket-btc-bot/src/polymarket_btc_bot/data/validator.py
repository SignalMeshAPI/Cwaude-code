"""Sanity checks before a tick enters MarketState."""

from __future__ import annotations

import time

PRICE_MIN = 10_000.0
PRICE_MAX = 1_000_000.0
MAX_AGE_SEC = 10.0


class InvalidTick(ValueError):
    pass


def validate_price(price: float, ts: float, now: float | None = None) -> None:
    n = now if now is not None else time.time()
    if not (PRICE_MIN <= price <= PRICE_MAX):
        raise InvalidTick(f"price {price} outside sanity range")
    if n - ts > MAX_AGE_SEC:
        raise InvalidTick(f"tick age {n - ts:.2f}s exceeds {MAX_AGE_SEC}s")
    if ts > n + 1.0:  # 1s clock skew tolerance
        raise InvalidTick(f"tick ts is in the future ({ts - n:.2f}s)")


def validate_funding_rate(rate: float) -> None:
    # Funding rates beyond ±5% per 8h are absurd
    if abs(rate) > 0.05:
        raise InvalidTick(f"funding rate {rate} out of plausible range")


def validate_fear_greed(value: int) -> None:
    if not (0 <= value <= 100):
        raise InvalidTick(f"fear&greed value {value} out of [0,100]")
