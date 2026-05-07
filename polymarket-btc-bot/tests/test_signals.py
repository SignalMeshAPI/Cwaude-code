"""Unit tests for signal processors. Pure-function tests against MarketState."""

import time

import pytest

from polymarket_btc_bot.data.market_state import MarketState, Tick
from polymarket_btc_bot.signals.microstructure import Microstructure
from polymarket_btc_bot.signals.price_divergence import PriceDivergence
from polymarket_btc_bot.signals.sentiment import SentimentAnalyzer
from polymarket_btc_bot.signals.spike_detector import SpikeDetector


@pytest.mark.asyncio
async def test_spike_detector_warmup_returns_zero_confidence():
    state = MarketState()
    state.binance_1m_closes.extend([100.0] * 5)  # below WARMUP_BARS
    r = await SpikeDetector().value(state)
    assert r.confidence == 0.0


@pytest.mark.asyncio
async def test_spike_detector_flags_outlier_up():
    state = MarketState()
    base = [100.0 + i * 0.001 for i in range(30)]  # gentle drift
    base.append(110.0)  # huge spike
    state.binance_1m_closes.extend(base)
    r = await SpikeDetector().value(state)
    assert r.direction > 0
    assert r.confidence > 0.5


@pytest.mark.asyncio
async def test_spike_detector_flags_outlier_down():
    state = MarketState()
    base = [100.0 - i * 0.001 for i in range(30)]
    base.append(90.0)
    state.binance_1m_closes.extend(base)
    r = await SpikeDetector().value(state)
    assert r.direction < 0
    assert r.confidence > 0.5


@pytest.mark.asyncio
async def test_sentiment_blend_greedy():
    state = MarketState()
    state.fear_greed = 90
    state.fear_greed_ts = time.time()
    state.social_sentiment = 0.4
    state.social_sentiment_ts = time.time()
    r = await SentimentAnalyzer().value(state)
    assert r.direction > 0
    assert 0 < r.confidence <= 1


@pytest.mark.asyncio
async def test_sentiment_blend_fearful():
    state = MarketState()
    state.fear_greed = 10
    state.fear_greed_ts = time.time()
    r = await SentimentAnalyzer().value(state)
    assert r.direction < 0


@pytest.mark.asyncio
async def test_sentiment_no_data_yields_zero_confidence():
    r = await SentimentAnalyzer().value(MarketState())
    assert r.confidence == 0.0


@pytest.mark.asyncio
async def test_price_divergence_positive_when_binance_above_coinbase():
    state = MarketState()
    now = time.time()
    state.binance_last = Tick(ts=now, price=60_010.0)
    state.coinbase_last = Tick(ts=now, price=60_000.0)
    r = await PriceDivergence().value(state)
    assert r.direction > 0
    assert r.confidence > 0


@pytest.mark.asyncio
async def test_price_divergence_stale_returns_zero():
    state = MarketState()
    state.binance_last = Tick(ts=1000.0, price=60_000.0)
    state.coinbase_last = Tick(ts=2000.0, price=60_000.0)
    r = await PriceDivergence().value(state)
    assert r.confidence == 0.0


@pytest.mark.asyncio
async def test_microstructure_blends_imbalance_and_funding():
    state = MarketState()
    state.orderbook_imbalance = 0.6
    state.funding_rate = 0.0003  # positive funding -> longs pay -> bullish bias
    r = await Microstructure().value(state)
    assert r.direction > 0
    assert r.confidence > 0


@pytest.mark.asyncio
async def test_microstructure_no_data_yields_zero_confidence():
    r = await Microstructure().value(MarketState())
    assert r.confidence == 0.0
