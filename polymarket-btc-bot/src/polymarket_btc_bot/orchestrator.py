"""Bot orchestrator: wires data sources, signals, fusion, risk, execution,
learning, and state into a single asyncio event loop.

Trading cycle (once per ~60s, locked to the 15-minute market boundary):
  1. find_next_market via gamma-api.
  2. evaluate all signals from MarketState.
  3. fuse with current learned weights.
  4. risk-engine vet.
  5. open position if approved (skip if already open on this market).
  6. (background) check for SL/TP every 5s while position is open.
  7. on market end, fetch resolution, compute PnL, persist trade,
     update learner.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import httpx

from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.data.binance_futures import (
    run_funding_rate,
    run_orderbook_imbalance,
)
from polymarket_btc_bot.data.binance_ws import run_binance_ws
from polymarket_btc_bot.data.coinbase_rest import run_coinbase
from polymarket_btc_bot.data.fear_greed import run_fear_greed
from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.data.social import run_social
from polymarket_btc_bot.execution.executor import Executor, OpenPosition
from polymarket_btc_bot.execution.market_finder import MarketFinder, MarketInfo
from polymarket_btc_bot.execution.polymarket_client import (
    PaperTradingClient,
    PolymarketClient,
    PolymarketClientProtocol,
)
from polymarket_btc_bot.learning.learning_engine import LearningEngine, TradeOutcome
from polymarket_btc_bot.learning.store import TradeStore
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger
from polymarket_btc_bot.risk.risk_engine import RiskEngine
from polymarket_btc_bot.signals.base import Signal, SignalReading
from polymarket_btc_bot.signals.fusion import fuse
from polymarket_btc_bot.signals.microstructure import Microstructure
from polymarket_btc_bot.signals.price_divergence import PriceDivergence
from polymarket_btc_bot.signals.sentiment import SentimentAnalyzer
from polymarket_btc_bot.signals.spike_detector import SpikeDetector
from polymarket_btc_bot.state.redis_state import RedisState

log = get_logger(__name__)


def _build_signals() -> list[Signal]:
    return [SpikeDetector(), SentimentAnalyzer(), PriceDivergence(), Microstructure()]


class Orchestrator:
    def __init__(self, cfg: Settings, *, force_paper: bool = False):
        self._cfg = cfg
        self._force_paper = force_paper
        self._state = MarketState()
        self._signals: list[Signal] = _build_signals()
        self._signal_names = [s.name for s in self._signals]

        self._store = TradeStore(cfg.learning_db_path)
        self._learner = LearningEngine(cfg, self._store, self._signal_names)
        self._risk = RiskEngine(cfg)
        self._market_finder = MarketFinder(cfg.polymarket_gamma_host)
        self._redis = RedisState.from_url(cfg.redis_url)
        self._client: PolymarketClientProtocol | None = None
        self._executor: Executor | None = None

        # In-memory open positions keyed by market_id; mirror in Redis
        self._open: dict[str, OpenPosition] = {}
        self._readings_at_open: dict[str, tuple[SignalReading, ...]] = {}
        self._weights_at_open: dict[str, dict[str, float]] = {}
        self._cumulative_pnl = 0.0
        self._trade_session_start = time.time()

    # ----- lifecycle ---------------------------------------------------------

    async def setup(self) -> None:
        mode = "paper" if self._force_paper else await self._redis.get_mode(self._cfg.pmbot_mode)
        if mode == "live":
            client = PolymarketClient(
                host=self._cfg.polymarket_host,
                private_key=self._cfg.polymarket_private_key.get_secret_value(),
                chain_id=self._cfg.polygon_chain_id,
            )
            client.init()
            self._client = client
        else:
            self._client = PaperTradingClient(starting_balance=100.0)
        self._executor = Executor(self._client, self._cfg.max_bet_usd)
        log.info("orchestrator.setup", mode=mode)

    async def shutdown(self) -> None:
        await self._redis.close()

    # ----- top-level ---------------------------------------------------------

    async def run(self) -> None:
        await self.setup()
        async with asyncio.TaskGroup() as tg:
            tg.create_task(run_binance_ws(self._state, self._cfg.binance_ws_url), name="binance_ws")
            tg.create_task(run_funding_rate(self._state, self._cfg.binance_futures_rest), name="funding")
            tg.create_task(run_orderbook_imbalance(self._state, self._cfg.binance_futures_ws), name="depth")
            tg.create_task(run_coinbase(self._state, self._cfg.coinbase_rest), name="coinbase")
            tg.create_task(run_fear_greed(self._state, self._cfg.fear_greed_url), name="fear_greed")
            tg.create_task(
                run_social(
                    self._state,
                    client_id=self._cfg.reddit_client_id,
                    client_secret=self._cfg.reddit_client_secret.get_secret_value(),
                    user_agent=self._cfg.reddit_user_agent,
                    subreddits=self._cfg.reddit_subreddit_list(),
                ),
                name="social",
            )
            tg.create_task(self._trading_loop(), name="trading")
            tg.create_task(self._monitor_loop(), name="monitor")

    # ----- loops -------------------------------------------------------------

    async def _trading_loop(self) -> None:
        """Decision and entry loop. Runs every 30s; only opens once per market."""
        while True:
            try:
                await self._maybe_open_position()
            except Exception as exc:  # noqa: BLE001
                log.exception("trading_loop.error", error=str(exc))
            await asyncio.sleep(30)

    async def _monitor_loop(self) -> None:
        """Settlement & SL/TP check loop. Runs every 5s."""
        while True:
            try:
                await self._tick_open_positions()
            except Exception as exc:  # noqa: BLE001
                log.exception("monitor_loop.error", error=str(exc))
            await asyncio.sleep(5)

    # ----- decision pipeline --------------------------------------------------

    async def _maybe_open_position(self) -> None:
        if self._executor is None:
            return

        market = await self._market_finder.find_next()
        if market is None:
            log.info("no_active_market")
            return

        # Don't double-enter on the same market
        if market.market_id in self._open:
            return

        # Need at least 60s before close to make trading worthwhile
        if market.seconds_to_close < 60:
            return

        readings = await self._evaluate_signals()
        weights = self._learner.current_weights()
        decision = fuse(readings, weights)

        metrics.FUSION_SCORE.set(decision.score)
        metrics.FUSION_CONFIDENCE.set(decision.confidence)
        for r in readings:
            metrics.SIGNAL_VALUE.labels(name=r.name).set(r.direction)
            metrics.SIGNAL_CONFIDENCE.labels(name=r.name).set(r.confidence)

        bankroll = await self._client.get_balance_usdc() if self._client else 0.0
        metrics.BANKROLL_USD.set(bankroll)

        risk = self._risk.evaluate(
            decision=decision,
            bankroll_usd=bankroll,
            open_positions=len(self._open),
            consecutive_losses=await self._redis.get_consecutive_losses(),
            daily_pnl_usd=self._store.daily_pnl(time.time() - 86400),
            weekly_pnl_usd=self._store.daily_pnl(time.time() - 7 * 86400),
            is_halted=await self._redis.is_halted(),
        )
        metrics.EDGE_GATE_PASS.labels(passed=str(risk.allow).lower()).inc()
        if not risk.allow:
            log.info("risk.blocked", reason=risk.reason, score=decision.score, conf=decision.confidence)
            return

        # Execute — sizing comes from the risk engine
        self._executor._max_bet_usd = risk.sized_usd  # noqa: SLF001
        pos = await self._executor.open_position(market, decision)
        if not pos:
            return

        self._open[market.market_id] = pos
        self._readings_at_open[market.market_id] = tuple(readings)
        self._weights_at_open[market.market_id] = dict(weights)
        await self._redis.save_position(market.market_id, pos.to_dict())

    async def _evaluate_signals(self) -> list[SignalReading]:
        out: list[SignalReading] = []
        for s in self._signals:
            try:
                out.append(await s.value(self._state))
            except Exception as exc:  # noqa: BLE001
                log.warning("signal.error", name=s.name, error=str(exc))
                out.append(SignalReading(s.name, 0.0, 0.0))
        return out

    # ----- settlement --------------------------------------------------------

    async def _tick_open_positions(self) -> None:
        if not self._open or self._executor is None:
            return
        now = time.time()
        for market_id, pos in list(self._open.items()):
            # SL/TP check
            try:
                await self._executor.maybe_exit_early(
                    pos,
                    stop_loss_pct=self._cfg.stop_loss_pct,
                    take_profit_pct=self._cfg.take_profit_pct,
                )
            except Exception as exc:  # noqa: BLE001
                log.warning("exit.error", market=market_id, error=str(exc))

            # Settlement after expiry
            if now >= pos.end_ts + 30:  # 30s grace for resolution to land
                await self._settle(market_id, pos)

    async def _settle(self, market_id: str, pos: OpenPosition) -> None:
        won = await self._fetch_resolution(pos)
        pnl = Executor.settle_payoff(pos, won)
        self._cumulative_pnl += pnl
        metrics.PNL_USD.set(self._cumulative_pnl)
        metrics.TRADE_OUTCOME.set(1 if won else 0)
        metrics.TRADES_TOTAL.labels(outcome="won" if won else "lost").inc()

        readings = self._readings_at_open.pop(market_id, ())
        weights = self._weights_at_open.pop(market_id, {})

        signals_payload = {
            r.name: {"direction": r.direction, "confidence": r.confidence} for r in readings
        }
        self._store.insert_trade(
            market_id=market_id,
            side=pos.side,
            entry_price=pos.entry_price,
            shares=pos.shares,
            notional_usd=pos.notional_usd,
            signals=signals_payload,
            weights=weights,
            opened_at=pos.opened_ts,
            closed_at=time.time(),
            pnl=pnl,
            won=won,
        )

        outcome = TradeOutcome(
            market_id=market_id,
            side=pos.side,
            entry_price=pos.entry_price,
            shares=pos.shares,
            notional_usd=pos.notional_usd,
            readings=readings,
            weights_at_open=weights,
            opened_at=pos.opened_ts,
            closed_at=time.time(),
            pnl_usd=pnl,
        )
        self._learner.update(outcome)

        # consecutive-loss kill switch
        if won:
            await self._redis.reset_losses()
        else:
            losses = await self._redis.record_loss()
            if losses >= self._cfg.kill_switch_consecutive_losses:
                metrics.KILL_SWITCH_TRIPS.inc()
                await self._redis.halt_for(3600)
                log.warning("kill_switch.tripped", consecutive_losses=losses)

        del self._open[market_id]
        await self._redis.delete_position(market_id)
        log.info("settled", market=market_id, won=won, pnl=pnl)

    async def _fetch_resolution(self, pos: OpenPosition) -> bool:
        """Hit gamma-api for the market record; check `umaResolutionStatus` /
        `outcomePrices`. The "Up" outcome wins iff outcomePrices[0] == 1."""
        try:
            url = f"{self._cfg.polymarket_gamma_host}/markets/{pos.market_id}"
            async with httpx.AsyncClient(timeout=10.0) as client:
                r = await client.get(url)
                r.raise_for_status()
                data = r.json()
        except httpx.HTTPError as exc:
            log.warning("settle.fetch_error", market=pos.market_id, error=str(exc))
            return False  # treat unresolved as loss; better safe than reward false win

        outcomes_raw = data.get("outcomePrices") or "[]"
        if isinstance(outcomes_raw, str):
            import json as _json

            try:
                outcomes = _json.loads(outcomes_raw)
            except (ValueError, TypeError):
                outcomes = []
        else:
            outcomes = outcomes_raw

        if not outcomes or len(outcomes) < 2:
            return False

        up_won = float(outcomes[0]) >= 0.5
        if pos.side == "UP":
            return up_won
        return not up_won

    # ----- expose for paper backfill / scripts --------------------------------

    def state(self) -> MarketState:
        return self._state

    def market_finder(self) -> MarketFinder:
        return self._market_finder

    def store(self) -> TradeStore:
        return self._store

    def signals(self) -> list[Signal]:
        return self._signals

    def client(self) -> PolymarketClientProtocol | None:
        return self._client

    def open_positions(self) -> dict[str, OpenPosition]:
        return dict(self._open)
