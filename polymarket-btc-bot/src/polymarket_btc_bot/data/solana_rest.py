"""Solana spot price tracker.

SOL is correlated with BTC at a 15-minute horizon but tends to amplify moves;
when BTC is undecided and SOL is decisively rallying or dumping, that "crypto-
wide pulse" is informative. We track SOL/USD via the public Coinbase v2 spot
endpoint (no auth) and compute a rolling 1-hour return.
"""

from __future__ import annotations

import asyncio
import time
from collections import deque

import httpx

from polymarket_btc_bot.data.market_state import MarketState, Tick
from polymarket_btc_bot.data.rate_limiter import TokenBucket
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)

# Keep ~1 hour of 1-minute spot snapshots
WINDOW = 60


async def run_solana(state: MarketState, base_url: str, interval: float = 60.0) -> None:
    """Poll SOL-USD spot every `interval` seconds."""
    url = f"{base_url.rstrip('/')}/prices/SOL-USD/spot"
    bucket = TokenBucket(rate_per_sec=0.5, capacity=2)  # well under public limits
    history: deque[tuple[float, float]] = deque(maxlen=WINDOW)
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            await bucket.acquire()
            try:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                # Coinbase v2: {"data": {"amount": "150.42", "currency": "USD", ...}}
                price = float(data["data"]["amount"])
                ts = time.time()
                state.sol_last = Tick(ts=ts, price=price)
                history.append((ts, price))
                # 1h return: oldest sample in the window vs newest
                if len(history) >= 2:
                    oldest_ts, oldest_p = history[0]
                    if oldest_p > 0 and ts - oldest_ts >= 30 * 60:  # need >=30min of history
                        state.sol_1h_return = (price - oldest_p) / oldest_p
                        state.sol_1h_return_ts = ts
            except (httpx.HTTPError, KeyError, ValueError) as exc:
                metrics.API_ERRORS.labels(source="solana").inc()
                log.warning("solana.error", error=str(exc))
            await asyncio.sleep(interval)
