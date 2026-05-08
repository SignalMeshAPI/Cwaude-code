"""Market finder: gamma-api response parsing + quarter-hour selection."""

import json
import time
from datetime import datetime, timedelta, timezone

import httpx
import pytest

from polymarket_btc_bot.execution.market_finder import MarketFinder


def _iso(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def _market(end: datetime, *, market_id: str = "m1", token_ids=None, condition="0xabc"):
    return {
        "id": market_id,
        "conditionId": condition,
        "question": "Will BTC be Up?",
        "end_date_iso": _iso(end),
        "clobTokenIds": token_ids or ["100", "200"],
        "series_slug": "bitcoin-up-or-down",
    }


def _make_handler(payload):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


@pytest.mark.asyncio
async def test_picks_soonest_future_market():
    now = datetime.now(timezone.utc)
    payload = [
        _market(now + timedelta(minutes=30), market_id="later"),
        _market(now + timedelta(minutes=10), market_id="next"),
        _market(now - timedelta(minutes=5), market_id="past"),
    ]
    transport = httpx.MockTransport(_make_handler(payload))
    async with httpx.AsyncClient(transport=transport) as client:
        finder = MarketFinder("https://gamma-api.polymarket.com")
        info = await finder.find_next(client=client)
    assert info is not None
    assert info.market_id == "next"


@pytest.mark.asyncio
async def test_returns_none_when_no_future_markets():
    now = datetime.now(timezone.utc)
    payload = [_market(now - timedelta(minutes=1), market_id="past")]
    transport = httpx.MockTransport(_make_handler(payload))
    async with httpx.AsyncClient(transport=transport) as client:
        finder = MarketFinder("https://gamma-api.polymarket.com")
        info = await finder.find_next(client=client)
    assert info is None


@pytest.mark.asyncio
async def test_handles_string_encoded_token_ids():
    now = datetime.now(timezone.utc)
    m = _market(now + timedelta(minutes=12))
    m["clobTokenIds"] = json.dumps(["111", "222"])  # gamma sometimes returns JSON-as-string
    transport = httpx.MockTransport(_make_handler([m]))
    async with httpx.AsyncClient(transport=transport) as client:
        finder = MarketFinder("https://gamma-api.polymarket.com")
        info = await finder.find_next(client=client)
    assert info is not None
    assert info.yes_token_id == "111"
    assert info.no_token_id == "222"


@pytest.mark.asyncio
async def test_skips_markets_without_condition_id():
    now = datetime.now(timezone.utc)
    payload = [
        {**_market(now + timedelta(minutes=10), market_id="bad"), "conditionId": ""},
        _market(now + timedelta(minutes=20), market_id="good"),
    ]
    transport = httpx.MockTransport(_make_handler(payload))
    async with httpx.AsyncClient(transport=transport) as client:
        finder = MarketFinder("https://gamma-api.polymarket.com")
        info = await finder.find_next(client=client)
    assert info is not None
    assert info.market_id == "good"


@pytest.mark.asyncio
async def test_seconds_to_close_is_positive_for_future_market():
    now = datetime.now(timezone.utc)
    payload = [_market(now + timedelta(minutes=10))]
    transport = httpx.MockTransport(_make_handler(payload))
    async with httpx.AsyncClient(transport=transport) as client:
        finder = MarketFinder("https://gamma-api.polymarket.com")
        info = await finder.find_next(client=client)
    assert info is not None
    assert info.seconds_to_close > 0
    assert info.end_ts > time.time()
