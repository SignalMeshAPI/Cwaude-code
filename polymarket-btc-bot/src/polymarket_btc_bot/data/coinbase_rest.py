"""Coinbase Exchange REST: BTC-USD spot ticker, polled every 5s."""

from __future__ import annotations

import asyncio
import time

import httpx

from polymarket_btc_bot.data.market_state import MarketState, Tick
from polymarket_btc_bot.data.rate_limiter import TokenBucket
from polymarket_btc_bot.data.validator import InvalidTick, validate_price
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


async def run_coinbase(state: MarketState, base_url: str, interval: float = 5.0) -> None:
    url = f"{base_url}/products/BTC-USD/ticker"
    bucket = TokenBucket(rate_per_sec=1.0, capacity=5)  # well under public limits
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await bucket.acquire()
            try:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                price = float(data["price"])
                # Coinbase returns ISO timestamp; trust it but bound to now
                ts = time.time()
                validate_price(price, ts)
                state.coinbase_last = Tick(ts=ts, price=price)
            except (httpx.HTTPError, KeyError, ValueError, InvalidTick) as exc:
                metrics.API_ERRORS.labels(source="coinbase").inc()
                log.warning("coinbase.error", error=str(exc))
            await asyncio.sleep(interval)
