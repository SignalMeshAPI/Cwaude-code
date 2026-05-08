"""Fusion engine: weighted vote across signal readings.

Only signals with confidence > 0 ("active") participate. Their learned
weights are renormalized so abstaining signals don't dilute the result.
This separates "what the bot believes" from "how much data the bot has".

Output:
  score      = Σ wᵢ' · directionᵢ · confidenceᵢ           (in [-1, 1])
  confidence = Σ wᵢ' · confidenceᵢ                        (in [0, 1])
  side       = "UP" | "DOWN" | None (None when no active signals or score≈0)

Where wᵢ' is the learned weight for signal i renormalized over active
signals to sum to 1. Weights are clamped to >= 0 defensively.

`active_count` exposes how many signals contributed so the orchestrator
can apply a separate "minimum corroboration" guard if it wants to.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from polymarket_btc_bot.signals.base import SignalReading, clip

Side = Literal["UP", "DOWN"]


@dataclass(frozen=True)
class FusedDecision:
    score: float
    confidence: float
    side: Side | None
    components: tuple[SignalReading, ...]
    weights: dict[str, float]  # renormalized; only active entries
    active_count: int


def _renormalize_active(weights: dict[str, float], active: list[str]) -> dict[str, float]:
    if not active:
        return {}
    raw = {n: max(0.0, weights.get(n, 0.0)) for n in active}
    total = sum(raw.values())
    if total <= 0:
        return {n: 1.0 / len(active) for n in active}
    return {n: v / total for n, v in raw.items()}


def fuse(readings: list[SignalReading], weights: dict[str, float]) -> FusedDecision:
    if not readings:
        return FusedDecision(0.0, 0.0, None, (), {}, 0)

    active = [r.name for r in readings if r.confidence > 0]
    if not active:
        return FusedDecision(0.0, 0.0, None, tuple(readings), {}, 0)

    w = _renormalize_active(weights, active)

    score = 0.0
    confidence = 0.0
    for r in readings:
        if r.confidence <= 0:
            continue
        wi = w[r.name]
        score += wi * r.direction * r.confidence
        confidence += wi * r.confidence

    score = clip(score, -1.0, 1.0)
    confidence = clip(confidence, 0.0, 1.0)

    side: Side | None
    if abs(score) < 1e-9:
        side = None
    else:
        side = "UP" if score > 0 else "DOWN"

    return FusedDecision(
        score=score,
        confidence=confidence,
        side=side,
        components=tuple(readings),
        weights=w,
        active_count=len(active),
    )
