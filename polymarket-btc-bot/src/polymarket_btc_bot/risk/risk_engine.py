"""Risk engine: decides whether a candidate trade is allowed.

Independent of the market or signal — operates on (decision, bankroll,
recent PnL history). Hard floors live in `config.py`; this module reads
the already-validated Settings.

Returns a RiskDecision(allow, reason). The orchestrator must check
`allow` and if False, increment the matching `pmbot_risk_block_total`
counter and skip the trade.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.signals.fusion import FusedDecision


@dataclass(frozen=True)
class RiskDecision:
    allow: bool
    reason: str
    sized_usd: float = 0.0


class RiskEngine:
    def __init__(self, settings: Settings):
        self._cfg = settings

    def evaluate(
        self,
        *,
        decision: FusedDecision,
        bankroll_usd: float,
        open_positions: int,
        consecutive_losses: int,
        daily_pnl_usd: float,
        weekly_pnl_usd: float,
        is_halted: bool,
        now: float | None = None,
    ) -> RiskDecision:
        cfg = self._cfg
        _ = now or time.time()

        if is_halted:
            return self._block("halted")

        if decision.side is None:
            return self._block("no_direction")

        if decision.confidence < cfg.min_edge_confidence:
            return self._block("below_edge_gate")

        if consecutive_losses >= cfg.kill_switch_consecutive_losses:
            return self._block("kill_switch")

        if open_positions >= cfg.max_concurrent_positions:
            return self._block("max_concurrent")

        if bankroll_usd < cfg.min_bankroll_usd:
            return self._block("below_min_bankroll")

        # daily/weekly caps are signed losses (negative). Block if loss exceeds cap.
        if daily_pnl_usd <= -cfg.max_daily_loss_usd:
            return self._block("daily_loss_cap")
        if weekly_pnl_usd <= -cfg.max_weekly_loss_usd:
            return self._block("weekly_loss_cap")

        # Position sizing: never exceed max_bet_usd, and never exceed 5% of bankroll.
        sized = min(cfg.max_bet_usd, bankroll_usd * 0.05)
        if sized < 1.0:  # Polymarket protocol min
            return self._block("size_below_protocol_min")

        return RiskDecision(allow=True, reason="ok", sized_usd=sized)

    @staticmethod
    def _block(reason: str) -> RiskDecision:
        metrics.RISK_BLOCK.labels(reason=reason).inc()
        return RiskDecision(allow=False, reason=reason, sized_usd=0.0)
