"""End-to-end backtest runner.

Walks each historical Polymarket BTC market sequentially in time order. For
each market, builds a MarketState snapshot at `decision_ts = end_ts - lookback_seconds`,
evaluates the same Signal/Fusion/Risk pipeline used live, then "settles"
against the known outcome. Same online learner runs after every settled
trade so weights evolve through the simulation.

The runner returns a BacktestReport plus the final TradeStore (in-memory
SQLite by default). The CLI wraps this with a Typer command.
"""

from __future__ import annotations

import asyncio
import math
from dataclasses import dataclass
from typing import Any

from polymarket_btc_bot.backtest.data_fetcher import (
    HistoricalMarket,
    fetch_binance_klines,
    fetch_closed_btc_markets,
    fetch_fear_greed_history,
    fetch_funding_history,
)
from polymarket_btc_bot.backtest.replay_state import ReplayDataset
from polymarket_btc_bot.backtest.report import BacktestReport, TradeRecord
from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.learning.learning_engine import LearningEngine, TradeOutcome
from polymarket_btc_bot.learning.store import TradeStore
from polymarket_btc_bot.monitoring.logger import get_logger
from polymarket_btc_bot.risk.risk_engine import RiskEngine
from polymarket_btc_bot.signals.base import Signal, SignalReading
from polymarket_btc_bot.signals.fusion import FusedDecision, fuse
from polymarket_btc_bot.signals.microstructure import Microstructure
from polymarket_btc_bot.signals.price_divergence import PriceDivergence
from polymarket_btc_bot.signals.sentiment import SentimentAnalyzer
from polymarket_btc_bot.signals.spike_detector import SpikeDetector

log = get_logger(__name__)


@dataclass
class BacktestParams:
    days: int
    initial_bankroll_usd: float = 100.0
    decision_lookback_sec: int = 5 * 60   # decide 5 min before close
    entry_price_assumption: float = 0.50  # per-share cost in absence of historical book


def _build_signals(cfg: Settings) -> list[Signal]:
    return [
        SpikeDetector(threshold_z=cfg.spike_threshold_z),
        SentimentAnalyzer(),
        PriceDivergence(threshold_bps=cfg.divergence_threshold_bps),
        Microstructure(),
    ]


async def run_backtest(
    *,
    cfg: Settings,
    params: BacktestParams,
    db_path: str = ":memory:",
    binance_base: str = "https://api.binance.com",
    binance_futures_base: str = "https://fapi.binance.com",
    fng_url: str = "https://api.alternative.me/fng/",
    gamma_host: str | None = None,
) -> BacktestReport:
    gamma_host = gamma_host or cfg.polymarket_gamma_host
    log.info("backtest.start", days=params.days, bankroll=params.initial_bankroll_usd)

    # 1. Fetch historical markets first so we know the time window required.
    markets = await fetch_closed_btc_markets(gamma_host=gamma_host, limit=500)
    if not markets:
        raise RuntimeError("no historical markets returned from gamma-api")

    # Filter to last N days
    cutoff = max(m.end_ts for m in markets) - params.days * 86400
    markets = [m for m in markets if m.end_ts >= cutoff]
    markets.sort(key=lambda m: m.end_ts)
    log.info("backtest.markets", count=len(markets), span_hours=(markets[-1].end_ts - markets[0].end_ts) / 3600)

    start_ts = markets[0].end_ts - params.decision_lookback_sec - 60 * 60  # +1h headroom
    end_ts = markets[-1].end_ts + 60

    # 2. Fetch supporting data
    klines, funding, fng = await asyncio.gather(
        fetch_binance_klines(symbol="BTCUSDT", start_ts=start_ts, end_ts=end_ts, base_url=binance_base),
        fetch_funding_history(symbol="BTCUSDT", start_ts=start_ts, end_ts=end_ts, base_url=binance_futures_base),
        fetch_fear_greed_history(days=max(params.days + 7, 30), base_url=fng_url),
    )
    log.info("backtest.data", klines=len(klines), funding=len(funding), fng=len(fng))
    dataset = ReplayDataset(klines=klines, funding=funding, fng=fng)

    # 3. Wire pipeline (using throwaway TradeStore; won't pollute live db)
    store = TradeStore(db_path)
    signals = _build_signals(cfg)
    learner = LearningEngine(cfg, store, [s.name for s in signals])
    risk = RiskEngine(cfg)
    bankroll = params.initial_bankroll_usd
    consecutive_losses = 0
    daily_pnl_window: list[tuple[float, float]] = []  # (ts, pnl)
    weekly_pnl_window: list[tuple[float, float]] = []

    trades: list[TradeRecord] = []
    skipped_reasons: dict[str, int] = {}

    for m in markets:
        decision_ts = m.end_ts - params.decision_lookback_sec
        state = dataset.state_at(decision_ts)
        readings: list[SignalReading] = []
        for s in signals:
            try:
                readings.append(await s.value(state))
            except Exception as exc:  # noqa: BLE001
                log.warning("backtest.signal.error", name=s.name, error=str(exc))
                readings.append(SignalReading(s.name, 0.0, 0.0))

        weights = learner.current_weights()
        decision: FusedDecision = fuse(readings, weights)

        # daily/weekly pnl rolling window
        daily_pnl_window = [(ts, p) for ts, p in daily_pnl_window if ts >= decision_ts - 86400]
        weekly_pnl_window = [(ts, p) for ts, p in weekly_pnl_window if ts >= decision_ts - 7 * 86400]
        daily_pnl = sum(p for _, p in daily_pnl_window)
        weekly_pnl = sum(p for _, p in weekly_pnl_window)

        risk_dec = risk.evaluate(
            decision=decision,
            bankroll_usd=bankroll,
            open_positions=0,  # 15-min markets don't overlap in backtest
            consecutive_losses=consecutive_losses,
            daily_pnl_usd=daily_pnl,
            weekly_pnl_usd=weekly_pnl,
            is_halted=False,
            now=decision_ts,
        )
        if not risk_dec.allow:
            skipped_reasons[risk_dec.reason] = skipped_reasons.get(risk_dec.reason, 0) + 1
            continue

        # 4. Settle trade against known outcome
        size_usd = risk_dec.sized_usd
        entry_price = params.entry_price_assumption
        shares = size_usd / entry_price
        won = (decision.side == "UP" and m.up_won) or (decision.side == "DOWN" and not m.up_won)
        pnl = (shares * 1.0 - size_usd) if won else (-size_usd)

        bankroll += pnl
        if won:
            consecutive_losses = 0
        else:
            consecutive_losses += 1
        daily_pnl_window.append((m.end_ts, pnl))
        weekly_pnl_window.append((m.end_ts, pnl))

        outcome = TradeOutcome(
            market_id=m.market_id,
            side=decision.side or "UP",
            entry_price=entry_price,
            shares=shares,
            notional_usd=size_usd,
            readings=tuple(readings),
            weights_at_open=dict(weights),
            opened_at=decision_ts,
            closed_at=m.end_ts,
            pnl_usd=pnl,
        )
        learner.update(outcome)
        trades.append(
            TradeRecord(
                market_id=m.market_id,
                side=decision.side or "UP",
                fusion_score=decision.score,
                fusion_confidence=decision.confidence,
                signals={r.name: {"d": r.direction, "c": r.confidence} for r in readings},
                weights={k: v for k, v in weights.items()},
                opened_at=decision_ts,
                closed_at=m.end_ts,
                size_usd=size_usd,
                pnl_usd=pnl,
                won=won,
                bankroll_after=bankroll,
            )
        )

    final_weights = learner.current_weights()
    return BacktestReport.build(
        params=params,
        trades=trades,
        final_bankroll=bankroll,
        final_weights=final_weights,
        skipped_reasons=skipped_reasons,
        markets_evaluated=len(markets),
    )


def _safe_div(a: float, b: float) -> float:
    return a / b if b else math.nan


__all__ = ["BacktestParams", "run_backtest"]
