"""Fusion engine: weighted vote across signal readings.

Output:
  score      = Σ wᵢ · directionᵢ · confidenceᵢ            (in [-1, 1])
  confidence = Σ wᵢ · confidenceᵢ                         (in [0, 1])
  side       = "UP" | "DOWN" | None (None when score ≈ 0)

Weights are supplied by the learning engine; missing weights default to
uniform across the input readings. Weights are normalized to sum=1 here
defensively in case the learner produced unnormalized values.
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
    weights: dict[str, float]


def _normalize(weights: dict[str, float], names: list[str]) -> dict[str, float]:
    if not names:
        return {}
    raw = {n: max(0.0, weights.get(n, 0.0)) for n in names}
    total = sum(raw.values())
    if total <= 0:
        return {n: 1.0 / len(names) for n in names}
    return {n: v / total for n, v in raw.items()}


def fuse(readings: list[SignalReading], weights: dict[str, float]) -> FusedDecision:
    if not readings:
        return FusedDecision(0.0, 0.0, None, (), {})

    names = [r.name for r in readings]
    w = _normalize(weights, names)

    score = 0.0
    confidence = 0.0
    for r in readings:
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

    return FusedDecision(score=score, confidence=confidence, side=side, components=tuple(readings), weights=w)
