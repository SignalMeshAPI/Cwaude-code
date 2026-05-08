"""Binance Futures: funding rate (REST) and orderbook imbalance (WS depth)."""

from __future__ import annotations

import asyncio
import json
import time

import httpx
import websockets

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.data.validator import InvalidTick, validate_funding_rate
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


async def run_funding_rate(state: MarketState, base_url: str, interval: float = 60.0) -> None:
    url = f"{base_url}/fapi/v1/premiumIndex?symbol=BTCUSDT"
    async with httpx.AsyncClient(timeout=10.0) as client:
        while True:
            try:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
                rate = float(data["lastFundingRate"])
                validate_funding_rate(rate)
                state.funding_rate = rate
                state.funding_rate_ts = time.time()
            except (httpx.HTTPError, KeyError, ValueError, InvalidTick) as exc:
                metrics.API_ERRORS.labels(source="binance_futures_rest").inc()
                log.warning("funding.error", error=str(exc))
            await asyncio.sleep(interval)


async def run_orderbook_imbalance(state: MarketState, ws_url: str) -> None:
    url = f"{ws_url}/btcusdt@depth20@100ms"
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                log.info("binance_futures_ws.connected", url=url)
                backoff = 1.0
                async for msg in ws:
                    _handle_depth(state, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            metrics.WS_RECONNECTS.labels(source="binance_futures").inc()
            metrics.API_ERRORS.labels(source="binance_futures_ws").inc()
            log.warning("binance_futures_ws.disconnect", error=str(exc), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _handle_depth(state: MarketState, raw: str | bytes) -> None:
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return
    bids = msg.get("b") or msg.get("bids") or []
    asks = msg.get("a") or msg.get("asks") or []
    bid_qty = sum(float(b[1]) for b in bids[:10])
    ask_qty = sum(float(a[1]) for a in asks[:10])
    total = bid_qty + ask_qty
    if total <= 0:
        return
    # imbalance in [-1, 1]: positive = buy pressure
    state.orderbook_imbalance = (bid_qty - ask_qty) / total
