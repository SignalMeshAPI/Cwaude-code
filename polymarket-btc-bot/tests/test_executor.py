"""Executor and paper client integration tests."""

import time

import pytest

from polymarket_btc_bot.execution.executor import Executor, implied_price_from_book
from polymarket_btc_bot.execution.market_finder import MarketInfo
from polymarket_btc_bot.execution.polymarket_client import PaperTradingClient
from polymarket_btc_bot.signals.base import SignalReading
from polymarket_btc_bot.signals.fusion import fuse


@pytest.mark.asyncio
async def test_paper_buy_reduces_balance():
    client = PaperTradingClient(starting_balance=10.0)
    bal_before = await client.get_balance_usdc()
    res = await client.place_order(
        token_id="t", side="BUY", price=0.5, size_shares=4.0, neg_risk=True, order_type="FOK"
    )
    assert res.success
    bal_after = await client.get_balance_usdc()
    assert bal_after == pytest.approx(bal_before - 0.5 * 4.0)


@pytest.mark.asyncio
async def test_paper_buy_rejects_insufficient_balance():
    client = PaperTradingClient(starting_balance=1.0)
    res = await client.place_order(
        token_id="t", side="BUY", price=0.5, size_shares=10.0, neg_risk=True, order_type="FOK"
    )
    assert not res.success


def test_implied_price_falls_back_when_book_empty():
    assert implied_price_from_book({"bids": [], "asks": []}, "BUY") == 0.5
    assert implied_price_from_book({"bids": [], "asks": []}, "SELL") == 0.5


def test_implied_price_takes_best_ask_for_buy():
    book = {"asks": [{"price": 0.62, "size": 100}], "bids": [{"price": 0.58, "size": 100}]}
    assert implied_price_from_book(book, "BUY") == 0.62
    assert implied_price_from_book(book, "SELL") == 0.58


@pytest.mark.asyncio
async def test_executor_opens_position_on_paper():
    client = PaperTradingClient(starting_balance=100.0)
    ex = Executor(client, max_bet_usd=2.0)
    market = MarketInfo(
        market_id="m1", condition_id="c1", question="?", end_ts=time.time() + 600,
        yes_token_id="yes", no_token_id="no", series_slug="bitcoin-up-or-down",
    )
    decision = fuse([SignalReading("a", 1.0, 0.9)], weights={"a": 1.0})
    pos = await ex.open_position(market, decision)
    assert pos is not None
    assert pos.side == "UP"
    assert pos.shares > 0


@pytest.mark.asyncio
async def test_executor_skips_when_no_side():
    client = PaperTradingClient()
    ex = Executor(client, max_bet_usd=2.0)
    market = MarketInfo(
        market_id="m", condition_id="c", question="?", end_ts=time.time() + 600,
        yes_token_id="y", no_token_id="n", series_slug="x",
    )
    decision = fuse(
        [SignalReading("a", 1.0, 1.0), SignalReading("b", -1.0, 1.0)],
        weights={"a": 1, "b": 1},
    )
    assert decision.side is None
    assert await ex.open_position(market, decision) is None


def test_settle_payoff_winner():
    from polymarket_btc_bot.execution.executor import OpenPosition

    pos = OpenPosition(
        market_id="m", condition_id="c", token_id="t", side="UP",
        entry_price=0.5, shares=4.0, notional_usd=2.0, opened_ts=0, end_ts=0, order_id="x",
    )
    assert Executor.settle_payoff(pos, won=True) == pytest.approx(2.0)
    assert Executor.settle_payoff(pos, won=False) == pytest.approx(-2.0)
