"""Learning engine: weight update, decay, floor, normalization."""

import math

import pytest

from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.learning.learning_engine import LearningEngine, TradeOutcome
from polymarket_btc_bot.learning.store import TradeStore
from polymarket_btc_bot.signals.base import SignalReading


def _settings() -> Settings:
    return Settings(
        polymarket_private_key="",
        learning_rate=0.5,  # large lr so test is sensitive
        learning_decay=1.0,
        learning_weight_floor=0.05,
        max_bet_usd=2.0,
    )


@pytest.fixture
def engine(tmp_path):
    cfg = _settings()
    store = TradeStore(str(tmp_path / "learn.db"))
    return LearningEngine(cfg, store, ["a", "b", "c"])


def _outcome(readings, pnl):
    return TradeOutcome(
        market_id="m", side="UP", entry_price=0.5, shares=4.0, notional_usd=2.0,
        readings=tuple(readings), weights_at_open={}, opened_at=0, closed_at=0, pnl_usd=pnl,
    )


def test_initial_weights_are_uniform(engine):
    w = engine.current_weights()
    assert pytest.approx(sum(w.values()), abs=1e-9) == 1.0
    assert all(abs(v - 1.0 / 3) < 1e-9 for v in w.values())


def test_winning_trade_rewards_aligned_signal(engine):
    readings = [
        SignalReading("a", 1.0, 1.0),   # bullish, fully confident — won
        SignalReading("b", -1.0, 1.0),  # bearish, fully confident — wrong
        SignalReading("c", 0.0, 0.0),   # abstained
    ]
    engine.update(_outcome(readings, pnl=1.0))
    w = engine.current_weights()
    assert w["a"] > w["b"]
    assert pytest.approx(sum(w.values()), abs=1e-9) == 1.0


def test_abstaining_signal_neither_punished_nor_rewarded(engine):
    readings = [
        SignalReading("a", 1.0, 1.0),
        SignalReading("b", -1.0, 1.0),
        SignalReading("c", 0.0, 0.0),
    ]
    before = engine.current_weights()["c"]
    engine.update(_outcome(readings, pnl=1.0))
    after = engine.current_weights()["c"]
    # 'c' should drift slightly because softmax renormalizes when others change,
    # but not because of its own gradient. Check it stayed within a tight band.
    assert abs(after - before) < 0.2


def test_weights_remain_positive_after_many_losses(engine):
    readings = [
        SignalReading("a", 1.0, 1.0),
        SignalReading("b", 1.0, 1.0),
        SignalReading("c", 1.0, 1.0),
    ]
    for _ in range(50):
        engine.update(_outcome(readings, pnl=-1.0))
    w = engine.current_weights()
    assert all(v > 0 for v in w.values())
    assert pytest.approx(sum(w.values()), abs=1e-9) == 1.0


def test_floor_prevents_zero_weight(tmp_path):
    cfg = Settings(
        polymarket_private_key="",
        learning_rate=10.0,    # absurd lr to drive logits very negative
        learning_decay=1.0,
        learning_weight_floor=0.05,
        max_bet_usd=2.0,
    )
    store = TradeStore(str(tmp_path / "learn.db"))
    engine = LearningEngine(cfg, store, ["a", "b"])

    readings = [
        SignalReading("a", 1.0, 1.0),
        SignalReading("b", -1.0, 1.0),
    ]
    for _ in range(20):
        engine.update(_outcome(readings, pnl=-1.0))
    w = engine.current_weights()
    # Every weight should remain >= floor regardless of how aggressive the gradients are
    assert w["a"] >= cfg.learning_weight_floor - 1e-9
    assert math.isclose(sum(w.values()), 1.0, abs_tol=1e-9)
