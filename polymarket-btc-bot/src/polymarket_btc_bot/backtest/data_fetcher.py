"""Fetch historical data needed to replay the bot offline.

Sources:
  * Binance public klines (1m BTCUSDT): /api/v3/klines, max 1000 bars/call.
  * Binance funding rate history (8h granularity): /fapi/v1/fundingRate.
  * Alternative.me Fear & Greed history: /fng/?limit=N (daily).
  * Polymarket gamma-api for closed bitcoin-up-or-down markets:
      /markets?series_slug=bitcoin-up-or-down&closed=true&limit=N

Each fetcher returns plain Python types so the rest of the backtest module
has no httpx coupling.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass

import httpx

from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class Kline:
    open_ts: float  # seconds, bar open time
    close_ts: float
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class FundingPoint:
    ts: float
    rate: float


@dataclass(frozen=True)
class FngPoint:
    ts: float
    value: int


@dataclass(frozen=True)
class HistoricalMarket:
    market_id: str
    end_ts: float
    up_won: bool


async def fetch_binance_klines(
    *,
    symbol: str,
    start_ts: float,
    end_ts: float,
    interval: str = "1m",
    base_url: str = "https://api.binance.com",
    client: httpx.AsyncClient | None = None,
) -> list[Kline]:
    """Fetch 1m klines in 1000-bar batches between [start_ts, end_ts]."""
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        out: list[Kline] = []
        cursor = int(start_ts * 1000)
        end_ms = int(end_ts * 1000)
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
            r = await client.get(f"{base_url}/api/v3/klines", params=params)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for row in data:
                out.append(
                    Kline(
                        open_ts=row[0] / 1000.0,
                        close_ts=row[6] / 1000.0,
                        open=float(row[1]),
                        high=float(row[2]),
                        low=float(row[3]),
                        close=float(row[4]),
                        volume=float(row[5]),
                    )
                )
            last_open_ms = data[-1][0]
            cursor = last_open_ms + 60_000  # advance one minute past last bar
            if len(data) < 1000:
                break
        return out
    finally:
        if owns:
            await client.aclose()


async def fetch_funding_history(
    *,
    symbol: str,
    start_ts: float,
    end_ts: float,
    base_url: str = "https://fapi.binance.com",
    client: httpx.AsyncClient | None = None,
) -> list[FundingPoint]:
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        out: list[FundingPoint] = []
        cursor = int(start_ts * 1000)
        end_ms = int(end_ts * 1000)
        while cursor < end_ms:
            params = {
                "symbol": symbol,
                "startTime": cursor,
                "endTime": end_ms,
                "limit": 1000,
            }
            r = await client.get(f"{base_url}/fapi/v1/fundingRate", params=params)
            r.raise_for_status()
            data = r.json()
            if not data:
                break
            for row in data:
                out.append(FundingPoint(ts=row["fundingTime"] / 1000.0, rate=float(row["fundingRate"])))
            cursor = data[-1]["fundingTime"] + 1
            if len(data) < 1000:
                break
        return out
    finally:
        if owns:
            await client.aclose()


async def fetch_fear_greed_history(
    *,
    days: int,
    base_url: str = "https://api.alternative.me/fng/",
    client: httpx.AsyncClient | None = None,
) -> list[FngPoint]:
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        r = await client.get(base_url, params={"limit": days})
        r.raise_for_status()
        data = r.json().get("data", [])
        # alternative.me returns newest-first
        return [FngPoint(ts=int(p["timestamp"]), value=int(p["value"])) for p in data]
    finally:
        if owns:
            await client.aclose()


async def fetch_closed_btc_markets(
    *,
    gamma_host: str,
    limit: int = 500,
    series_slug: str = "bitcoin-up-or-down",
    client: httpx.AsyncClient | None = None,
) -> list[HistoricalMarket]:
    owns = client is None
    if owns:
        client = httpx.AsyncClient(timeout=20.0)
    try:
        params = {
            "closed": "true",
            "series_slug": series_slug,
            "limit": limit,
            "order": "endDate",
            "ascending": "false",
        }
        r = await client.get(f"{gamma_host.rstrip('/')}/markets", params=params)
        r.raise_for_status()
        markets = r.json()
        out: list[HistoricalMarket] = []
        for m in markets:
            try:
                outcomes_raw = m.get("outcomePrices") or "[]"
                if isinstance(outcomes_raw, str):
                    outcomes = json.loads(outcomes_raw)
                else:
                    outcomes = outcomes_raw
                if not outcomes or len(outcomes) < 2:
                    continue
                end_iso = m.get("end_date_iso") or m.get("endDate") or m.get("endDateIso")
                if not end_iso:
                    continue
                end_ts = _parse_iso(end_iso) if isinstance(end_iso, str) else float(end_iso) / 1000.0
                up_won = float(outcomes[0]) >= 0.5
                out.append(
                    HistoricalMarket(
                        market_id=str(m.get("id") or m.get("conditionId") or ""),
                        end_ts=end_ts,
                        up_won=up_won,
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("backtest.market.parse_skip", error=str(exc))
                continue
        return out
    finally:
        if owns:
            await client.aclose()


def _parse_iso(value: str) -> float:
    from datetime import datetime, timezone

    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()


def now_ts() -> float:
    return time.time()
