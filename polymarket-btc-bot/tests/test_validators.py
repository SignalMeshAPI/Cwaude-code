import time

import pytest

from polymarket_btc_bot.data.rate_limiter import TokenBucket
from polymarket_btc_bot.data.validator import (
    InvalidTick,
    validate_fear_greed,
    validate_funding_rate,
    validate_price,
)


def test_price_in_range_passes():
    validate_price(60_000.0, time.time())


def test_price_too_low_rejected():
    with pytest.raises(InvalidTick):
        validate_price(5.0, time.time())


def test_price_too_old_rejected():
    with pytest.raises(InvalidTick):
        validate_price(60_000.0, time.time() - 60)


def test_fear_greed_in_range():
    validate_fear_greed(50)
    with pytest.raises(InvalidTick):
        validate_fear_greed(150)


def test_funding_rate_extreme_rejected():
    validate_funding_rate(0.0005)
    with pytest.raises(InvalidTick):
        validate_funding_rate(0.5)


@pytest.mark.asyncio
async def test_token_bucket_acquires_immediately_when_full():
    b = TokenBucket(rate_per_sec=10, capacity=5)
    assert await b.try_acquire(1)


@pytest.mark.asyncio
async def test_token_bucket_blocks_when_empty():
    b = TokenBucket(rate_per_sec=100, capacity=1)
    assert await b.try_acquire(1)
    # next try without refill window should fail immediately
    assert not await b.try_acquire(1)
