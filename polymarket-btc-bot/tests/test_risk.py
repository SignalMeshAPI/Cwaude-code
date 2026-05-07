"""Risk engine: hard caps, edge gate, kill switch."""

import pytest

from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.risk.risk_engine import RiskEngine
from polymarket_btc_bot.signals.base import SignalReading
from polymarket_btc_bot.signals.fusion import fuse


def _decision(side: str = "UP", confidence: float = 0.7):
    sign = 1.0 if side == "UP" else -1.0
    rs = [SignalReading("a", sign, confidence)]
    return fuse(rs, weights={"a": 1.0})


def _settings(**overrides) -> Settings:
    base = dict(
        polymarket_private_key="",
        max_bet_usd=2.0,
        stop_loss_pct=0.30,
        take_profit_pct=0.20,
        min_edge_confidence=0.55,
        max_concurrent_positions=1,
        max_daily_loss_usd=10.0,
        max_weekly_loss_usd=30.0,
        kill_switch_consecutive_losses=5,
        min_bankroll_usd=10.0,
    )
    base.update(overrides)
    return Settings(**base)


def test_blocks_below_edge_gate():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.50)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=0, consecutive_losses=0,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=False,
    )
    assert not r.allow and r.reason == "below_edge_gate"


def test_allows_when_above_edge_gate():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.7)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=0, consecutive_losses=0,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=False,
    )
    assert r.allow and r.sized_usd > 0


def test_blocks_when_halted():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.9)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=0, consecutive_losses=0,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=True,
    )
    assert not r.allow and r.reason == "halted"


def test_blocks_when_concurrent_at_cap():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.9)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=1, consecutive_losses=0,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=False,
    )
    assert not r.allow and r.reason == "max_concurrent"


def test_blocks_when_kill_switch_engaged():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.9)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=0, consecutive_losses=5,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=False,
    )
    assert not r.allow and r.reason == "kill_switch"


def test_blocks_when_daily_loss_exceeded():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.9)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=0, consecutive_losses=0,
        daily_pnl_usd=-12.0, weekly_pnl_usd=-12.0, is_halted=False,
    )
    assert not r.allow and r.reason == "daily_loss_cap"


def test_blocks_when_bankroll_below_min():
    risk = RiskEngine(_settings())
    d = _decision(confidence=0.9)
    r = risk.evaluate(
        decision=d, bankroll_usd=5, open_positions=0, consecutive_losses=0,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=False,
    )
    assert not r.allow and r.reason == "below_min_bankroll"


def test_settings_floor_rejects_excessive_max_bet():
    with pytest.raises(ValueError):
        _settings(max_bet_usd=100.0)


def test_settings_floor_rejects_low_edge_gate():
    with pytest.raises(ValueError):
        _settings(min_edge_confidence=0.40)


def test_size_caps_at_5pct_of_bankroll():
    risk = RiskEngine(_settings(max_bet_usd=20.0))
    d = _decision(confidence=0.9)
    r = risk.evaluate(
        decision=d, bankroll_usd=100, open_positions=0, consecutive_losses=0,
        daily_pnl_usd=0, weekly_pnl_usd=0, is_halted=False,
    )
    # 5% of 100 = 5 < 20
    assert r.allow
    assert r.sized_usd == 5.0
