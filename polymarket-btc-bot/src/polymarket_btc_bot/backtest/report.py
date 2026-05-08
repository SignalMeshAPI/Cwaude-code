"""Backtest summary statistics and serialization."""

from __future__ import annotations

import json
import math
import statistics
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass(frozen=True)
class TradeRecord:
    market_id: str
    side: str
    fusion_score: float
    fusion_confidence: float
    signals: dict[str, dict[str, float]]
    weights: dict[str, float]
    opened_at: float
    closed_at: float
    size_usd: float
    pnl_usd: float
    won: bool
    bankroll_after: float


@dataclass
class BacktestReport:
    days: int
    initial_bankroll_usd: float
    final_bankroll_usd: float
    markets_evaluated: int
    trades_taken: int
    wins: int
    losses: int
    win_rate: float
    total_pnl_usd: float
    return_pct: float
    avg_pnl_usd: float
    pnl_stdev_usd: float
    sharpe_per_trade: float
    max_drawdown_usd: float
    longest_loss_streak: int
    edge_gate_pass_rate: float
    skipped_reasons: dict[str, int]
    final_weights: dict[str, float]
    per_signal_correlation: dict[str, float]
    trades: list[TradeRecord] = field(default_factory=list)

    @classmethod
    def build(
        cls,
        *,
        params: Any,
        trades: list[TradeRecord],
        final_bankroll: float,
        final_weights: dict[str, float],
        skipped_reasons: dict[str, int],
        markets_evaluated: int,
    ) -> "BacktestReport":
        wins = sum(1 for t in trades if t.won)
        losses = len(trades) - wins
        win_rate = wins / len(trades) if trades else 0.0
        total_pnl = sum(t.pnl_usd for t in trades)
        avg_pnl = total_pnl / len(trades) if trades else 0.0
        stdev = statistics.pstdev([t.pnl_usd for t in trades]) if len(trades) > 1 else 0.0
        sharpe = avg_pnl / stdev if stdev > 0 else 0.0

        # max drawdown over the bankroll curve
        peak = params.initial_bankroll_usd
        max_dd = 0.0
        for t in trades:
            peak = max(peak, t.bankroll_after)
            dd = peak - t.bankroll_after
            if dd > max_dd:
                max_dd = dd

        # longest losing streak
        longest = streak = 0
        for t in trades:
            if not t.won:
                streak += 1
                longest = max(longest, streak)
            else:
                streak = 0

        # per-signal correlation: corr(direction_i * confidence_i, win {-1,+1})
        per_signal_corr = _per_signal_correlation(trades)

        attempted = markets_evaluated
        passed = len(trades)
        gate_rate = passed / attempted if attempted else 0.0

        return cls(
            days=params.days,
            initial_bankroll_usd=params.initial_bankroll_usd,
            final_bankroll_usd=final_bankroll,
            markets_evaluated=markets_evaluated,
            trades_taken=len(trades),
            wins=wins,
            losses=losses,
            win_rate=win_rate,
            total_pnl_usd=total_pnl,
            return_pct=(final_bankroll - params.initial_bankroll_usd) / params.initial_bankroll_usd
            if params.initial_bankroll_usd > 0
            else 0.0,
            avg_pnl_usd=avg_pnl,
            pnl_stdev_usd=stdev,
            sharpe_per_trade=sharpe,
            max_drawdown_usd=max_dd,
            longest_loss_streak=longest,
            edge_gate_pass_rate=gate_rate,
            skipped_reasons=dict(skipped_reasons),
            final_weights=dict(final_weights),
            per_signal_correlation=per_signal_corr,
            trades=trades,
        )

    def to_json(self) -> str:
        return json.dumps(asdict(self), indent=2, default=str)

    def render_text(self) -> str:
        lines = [
            "=" * 72,
            f"  Polymarket BTC Bot Backtest — {self.days}-day window",
            "=" * 72,
            f"  Markets evaluated:        {self.markets_evaluated}",
            f"  Trades taken:             {self.trades_taken}  (edge-gate pass {self.edge_gate_pass_rate:.1%})",
            f"  Wins / Losses:            {self.wins} / {self.losses}",
            f"  Win rate:                 {self.win_rate:.2%}",
            "",
            f"  Initial bankroll:         ${self.initial_bankroll_usd:.2f}",
            f"  Final bankroll:           ${self.final_bankroll_usd:.2f}",
            f"  Total PnL:                ${self.total_pnl_usd:+.2f}  ({self.return_pct:+.2%})",
            f"  Avg PnL / trade:          ${self.avg_pnl_usd:+.4f}",
            f"  PnL stdev:                ${self.pnl_stdev_usd:.4f}",
            f"  Sharpe (per-trade):       {self.sharpe_per_trade:+.3f}",
            f"  Max drawdown:             ${self.max_drawdown_usd:.2f}",
            f"  Longest loss streak:      {self.longest_loss_streak}",
            "",
            "  Final learned weights:",
        ]
        for name, w in sorted(self.final_weights.items(), key=lambda kv: -kv[1]):
            lines.append(f"    {name:<16}  {w:.4f}")
        if self.per_signal_correlation:
            lines.append("")
            lines.append("  Per-signal correlation with outcome (higher = more predictive):")
            for name, c in sorted(self.per_signal_correlation.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {name:<16}  {c:+.3f}")
        if self.skipped_reasons:
            lines.append("")
            lines.append("  Risk-gate skip reasons:")
            for reason, count in sorted(self.skipped_reasons.items(), key=lambda kv: -kv[1]):
                lines.append(f"    {reason:<24}  {count}")
        lines.append("=" * 72)
        return "\n".join(lines)


def _per_signal_correlation(trades: list[TradeRecord]) -> dict[str, float]:
    if not trades:
        return {}
    names = sorted({n for t in trades for n in t.signals})
    out: dict[str, float] = {}
    for name in names:
        xs: list[float] = []
        ys: list[float] = []
        for t in trades:
            sig = t.signals.get(name)
            if not sig:
                continue
            # Map win/loss to ±1 aligned with bet side.
            # Signal direction is in market frame (+ = UP); outcome must also
            # be in market frame: +1 if UP won else -1.
            d = float(sig.get("d", 0.0))
            c = float(sig.get("c", 0.0))
            up_won = (t.won and t.side == "UP") or (not t.won and t.side == "DOWN")
            xs.append(d * c)
            ys.append(1.0 if up_won else -1.0)
        out[name] = _pearson(xs, ys)
    return out


def _pearson(xs: list[float], ys: list[float]) -> float:
    n = len(xs)
    if n < 2:
        return 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    cov = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    vx = sum((x - mx) ** 2 for x in xs)
    vy = sum((y - my) ** 2 for y in ys)
    if vx <= 0 or vy <= 0:
        return 0.0
    return cov / math.sqrt(vx * vy)
