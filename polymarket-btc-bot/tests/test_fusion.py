"""Fusion engine: weighted vote, normalization, side selection."""

from polymarket_btc_bot.signals.base import SignalReading
from polymarket_btc_bot.signals.fusion import fuse


def test_uniform_weights_when_unspecified():
    rs = [
        SignalReading("a", 1.0, 1.0),
        SignalReading("b", -1.0, 0.5),
    ]
    d = fuse(rs, weights={})
    # uniform 0.5/0.5: score = 0.5*1*1 + 0.5*(-1)*0.5 = 0.25
    assert d.score == 0.25
    assert d.side == "UP"
    assert abs(d.weights["a"] - 0.5) < 1e-9


def test_renormalizes_arbitrary_weights():
    rs = [SignalReading("a", 1.0, 1.0), SignalReading("b", 1.0, 1.0)]
    d = fuse(rs, weights={"a": 2.0, "b": 8.0})
    assert abs(d.weights["a"] - 0.2) < 1e-9
    assert abs(d.weights["b"] - 0.8) < 1e-9


def test_zero_score_returns_no_side():
    rs = [
        SignalReading("a", 1.0, 1.0),
        SignalReading("b", -1.0, 1.0),
    ]
    d = fuse(rs, weights={"a": 1.0, "b": 1.0})
    assert d.side is None
    assert d.score == 0.0


def test_confidence_aggregates_to_at_most_one():
    rs = [
        SignalReading("a", 1.0, 1.0),
        SignalReading("b", 1.0, 1.0),
        SignalReading("c", 1.0, 1.0),
    ]
    d = fuse(rs, weights={"a": 1, "b": 1, "c": 1})
    assert d.confidence <= 1.0


def test_empty_inputs_safe():
    d = fuse([], {})
    assert d.score == 0.0
    assert d.confidence == 0.0
    assert d.side is None


def test_strong_down_majority():
    rs = [
        SignalReading("a", -1.0, 0.9),
        SignalReading("b", -1.0, 0.9),
        SignalReading("c", 0.5, 0.2),
    ]
    d = fuse(rs, weights={"a": 1, "b": 1, "c": 1})
    assert d.side == "DOWN"
    assert d.score < 0


def test_abstaining_signal_does_not_dilute_confidence():
    rs = [
        SignalReading("active1", 1.0, 1.0),
        SignalReading("active2", 1.0, 1.0),
        SignalReading("abstainer", 0.0, 0.0),
    ]
    d = fuse(rs, weights={"active1": 1, "active2": 1, "abstainer": 1})
    # active signals should split the vote 50/50, abstainer drops out
    assert d.confidence == 1.0
    assert d.active_count == 2
    assert "abstainer" not in d.weights
    assert abs(d.weights["active1"] - 0.5) < 1e-9


def test_no_active_signals_returns_no_side():
    rs = [
        SignalReading("a", 0.5, 0.0),
        SignalReading("b", -0.5, 0.0),
    ]
    d = fuse(rs, weights={"a": 1, "b": 1})
    assert d.side is None
    assert d.confidence == 0.0
    assert d.active_count == 0


def test_active_count_reflects_contributing_signals():
    rs = [
        SignalReading("a", 1.0, 0.7),
        SignalReading("b", 1.0, 0.0),  # abstains
        SignalReading("c", -0.5, 0.4),
    ]
    d = fuse(rs, weights={"a": 1, "b": 1, "c": 1})
    assert d.active_count == 2
