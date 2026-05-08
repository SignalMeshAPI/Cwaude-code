"""Fear & Greed index from alternative.me. Hourly poll."""

from __future__ import annotations

import asyncio
import time

import httpx

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.data.validator import InvalidTick, validate_fear_greed
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


async def run_fear_greed(state: MarketState, url: str, interval: float = 3600.0) -> None:
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                r = await client.get(url)
                r.raise_for_status()
                payload = r.json()
                value = int(payload["data"][0]["value"])
                validate_fear_greed(value)
                state.fear_greed = value
                state.fear_greed_ts = time.time()
            except (httpx.HTTPError, KeyError, ValueError, IndexError, InvalidTick) as exc:
                metrics.API_ERRORS.labels(source="fear_greed").inc()
                log.warning("fear_greed.error", error=str(exc))
            await asyncio.sleep(interval)
