"""Backtest harness tests using synthetic data (no network)."""

from __future__ import annotations

import asyncio

import pytest

from polymarket_btc_bot.backtest.data_fetcher import FngPoint, FundingPoint, HistoricalMarket, Kline
from polymarket_btc_bot.backtest.replay_state import ReplayDataset
from polymarket_btc_bot.backtest.report import BacktestReport, TradeRecord
from polymarket_btc_bot.backtest.runner import BacktestParams
from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.learning.learning_engine import LearningEngine
from polymarket_btc_bot.learning.store import TradeStore


def _settings() -> Settings:
    return Settings(
        polymarket_private_key="",
        learning_db_path=":memory:",
    )


def test_replay_state_uses_most_recent_data():
    klines = [
        Kline(open_ts=0, close_ts=60, open=100, high=101, low=99, close=100, volume=1.0),
        Kline(open_ts=60, close_ts=120, open=100, high=102, low=99, close=101, volume=1.0),
        Kline(open_ts=120, close_ts=180, open=101, high=103, low=100, close=102, volume=1.0),
    ]
    funding = [FundingPoint(ts=30, rate=0.0001), FundingPoint(ts=180, rate=0.0002)]
    fng = [FngPoint(ts=0, value=50), FngPoint(ts=170, value=70)]

    ds = ReplayDataset(klines=klines, funding=funding, fng=fng)
    state = ds.state_at(target_ts=200)

    assert state.binance_last is not None
    assert state.binance_last.price == 102
    assert list(state.binance_1m_closes)[-3:] == [100.0, 101.0, 102.0]
    assert state.funding_rate == 0.0002
    assert state.fear_greed == 70


def test_replay_state_excludes_future_bars():
    klines = [
        Kline(open_ts=0, close_ts=60, open=100, high=101, low=99, close=100, volume=1.0),
        Kline(open_ts=120, close_ts=180, open=101, high=103, low=100, close=102, volume=1.0),
    ]
    ds = ReplayDataset(klines=klines, funding=[], fng=[])
    state = ds.state_at(target_ts=90)
    # only the bar that closed at t=60 should be included
    assert list(state.binance_1m_closes) == [100.0]


def test_pearson_correlation_zero_for_constant():
    # Build trades where every signal reading is identical -> pearson == 0
    trades: list[TradeRecord] = []
    for i in range(10):
        trades.append(
            TradeRecord(
                market_id=f"m{i}",
                side="UP",
                fusion_score=0.5,
                fusion_confidence=0.7,
                signals={"a": {"d": 0.5, "c": 0.5}},
                weights={"a": 1.0},
                opened_at=0,
                closed_at=0,
                size_usd=2.0,
                pnl_usd=2.0 if i % 2 == 0 else -2.0,
                won=i % 2 == 0,
                bankroll_after=100.0,
            )
        )
    params = BacktestParams(days=1, initial_bankroll_usd=100.0)
    report = BacktestReport.build(
        params=params, trades=trades, final_bankroll=100.0, final_weights={"a": 1.0},
        skipped_reasons={}, markets_evaluated=10,
    )
    # constant x, varying y -> correlation is 0 (degenerate)
    assert report.per_signal_correlation["a"] == 0.0


def test_report_summary_includes_all_keys():
    trade = TradeRecord(
        market_id="m1", side="UP", fusion_score=0.7, fusion_confidence=0.8,
        signals={"spike": {"d": 0.5, "c": 0.6}},
        weights={"spike": 1.0},
        opened_at=0, closed_at=900,
        size_usd=2.0, pnl_usd=2.0, won=True,
        bankroll_after=102.0,
    )
    params = BacktestParams(days=1, initial_bankroll_usd=100.0)
    report = BacktestReport.build(
        params=params, trades=[trade], final_bankroll=102.0, final_weights={"spike": 1.0},
        skipped_reasons={"below_edge_gate": 3}, markets_evaluated=10,
    )
    text = report.render_text()
    assert "Markets evaluated:" in text
    assert "Win rate:" in text
    assert "below_edge_gate" in text
    json_blob = report.to_json()
    assert "\"win_rate\"" in json_blob
    assert "\"final_weights\"" in json_blob


def test_max_drawdown_tracks_peak_to_trough():
    trades: list[TradeRecord] = []
    bankrolls = [105, 110, 95, 100, 90]  # peak 110, trough 90 -> dd=20
    pnls = [5, 5, -15, 5, -10]
    for i, (b, p) in enumerate(zip(bankrolls, pnls)):
        trades.append(
            TradeRecord(
                market_id=f"m{i}", side="UP", fusion_score=0, fusion_confidence=0,
                signals={}, weights={}, opened_at=0, closed_at=0, size_usd=2,
                pnl_usd=p, won=p > 0, bankroll_after=b,
            )
        )
    params = BacktestParams(days=1, initial_bankroll_usd=100.0)
    report = BacktestReport.build(
        params=params, trades=trades, final_bankroll=90.0, final_weights={},
        skipped_reasons={}, markets_evaluated=5,
    )
    assert report.max_drawdown_usd == pytest.approx(20.0)


def test_longest_loss_streak_counted():
    trades: list[TradeRecord] = []
    pattern = [True, False, False, False, True, False, False]  # longest LL streak = 3
    for i, won in enumerate(pattern):
        trades.append(
            TradeRecord(
                market_id=f"m{i}", side="UP", fusion_score=0, fusion_confidence=0,
                signals={}, weights={}, opened_at=0, closed_at=0, size_usd=2,
                pnl_usd=2 if won else -2, won=won, bankroll_after=100.0,
            )
        )
    params = BacktestParams(days=1, initial_bankroll_usd=100.0)
    report = BacktestReport.build(
        params=params, trades=trades, final_bankroll=100.0, final_weights={},
        skipped_reasons={}, markets_evaluated=len(trades),
    )
    assert report.longest_loss_streak == 3


def test_backtest_runner_end_to_end_with_synthetic_data(monkeypatch, tmp_path):
    """Run the full backtest pipeline against monkeypatched fetchers."""
    from polymarket_btc_bot.backtest import runner as runner_mod

    # Build 4 hours of 1-minute klines: gentle baseline drift + UP spike
    # right before each market closes so the spike detector fires bullish.
    import random
    rng = random.Random(42)
    klines = []
    base_price = 60_000.0
    # Decisions happen `decision_lookback_sec` (5 min) before each market end.
    # Schedule the bullish spike 6 min before close so it's visible at decision time.
    spike_minutes = {(60 + 30 * i) - 6 for i in range(4)}
    for minute in range(240):
        # gentle noise + drift
        delta = rng.gauss(0.0, 5.0) + 0.5
        # bullish spike just before each decision window
        if minute in spike_minutes:
            delta += 200.0
        base_price += delta
        ts = float(minute * 60)
        klines.append(
            Kline(open_ts=ts, close_ts=ts + 60, open=base_price - delta, high=base_price + 1,
                  low=base_price - abs(delta) - 1, close=base_price, volume=1.0)
        )

    funding = [FundingPoint(ts=0.0, rate=0.0003)]  # strong positive funding -> bullish
    fng = [FngPoint(ts=0.0, value=80)]  # very greedy

    # 4 markets, one every 30 minutes; UP wins all of them (matching the drift+spike)
    markets = [
        HistoricalMarket(market_id=f"m{i}", end_ts=float(60 * (60 + 30 * i)), up_won=True)
        for i in range(4)
    ]

    async def fake_klines(**kwargs):
        return klines

    async def fake_funding(**kwargs):
        return funding

    async def fake_fng(**kwargs):
        return fng

    async def fake_markets(**kwargs):
        return markets

    monkeypatch.setattr(runner_mod, "fetch_binance_klines", fake_klines)
    monkeypatch.setattr(runner_mod, "fetch_funding_history", fake_funding)
    monkeypatch.setattr(runner_mod, "fetch_fear_greed_history", fake_fng)
    monkeypatch.setattr(runner_mod, "fetch_closed_btc_markets", fake_markets)

    cfg = _settings()
    params = BacktestParams(days=1, initial_bankroll_usd=100.0, decision_lookback_sec=300)

    report = asyncio.run(
        runner_mod.run_backtest(cfg=cfg, params=params, db_path=str(tmp_path / "bt.db"))
    )
    assert report.markets_evaluated == 4
    # The drift is consistently UP and F&G is greedy, so the pipeline should
    # take some trades (edge gate may still gate some; just assert >= 1).
    assert report.trades_taken >= 1
    # Whatever it traded, against UP-winning markets that aligned with signals,
    # the win rate should be >= 50%.
    assert report.win_rate >= 0.5
