"""Binance spot WebSocket: trade ticks and 1-minute kline closes."""

from __future__ import annotations

import asyncio
import json
import time

import websockets

from polymarket_btc_bot.data.market_state import MarketState, Tick
from polymarket_btc_bot.data.validator import InvalidTick, validate_price
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


async def run_binance_ws(state: MarketState, base_url: str) -> None:
    """Single-stream connection to BTCUSDT trade + 1m kline.
    Reconnects forever with exponential backoff capped at 30s."""
    url = f"{base_url}/btcusdt@trade/btcusdt@kline_1m"
    backoff = 1.0
    while True:
        try:
            async with websockets.connect(url, ping_interval=20, ping_timeout=20) as ws:
                log.info("binance_ws.connected", url=url)
                backoff = 1.0
                async for msg in ws:
                    _handle(state, msg)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001
            metrics.WS_RECONNECTS.labels(source="binance_spot").inc()
            metrics.API_ERRORS.labels(source="binance_spot").inc()
            log.warning("binance_ws.disconnect", error=str(exc), backoff=backoff)
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 30.0)


def _handle(state: MarketState, raw: str | bytes) -> None:
    try:
        msg = json.loads(raw)
    except (ValueError, TypeError):
        return

    # Combined-stream wrapper has {"stream": "...", "data": {...}}
    payload = msg.get("data", msg)
    event = payload.get("e")

    if event == "trade":
        try:
            price = float(payload["p"])
            ts = payload["T"] / 1000.0
            validate_price(price, ts)
        except (KeyError, ValueError, InvalidTick):
            return
        state.binance_last = Tick(ts=ts, price=price)

    elif event == "kline":
        k = payload.get("k", {})
        if not k.get("x"):  # only on bar close
            return
        try:
            close = float(k["c"])
            ts = k["T"] / 1000.0
            validate_price(close, ts, now=time.time() + 5)  # bar close ts is bar end
        except (KeyError, ValueError, InvalidTick):
            return
        state.binance_1m_closes.append(close)
