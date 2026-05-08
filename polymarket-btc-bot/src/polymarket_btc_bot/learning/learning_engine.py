"""Online weight update for the fusion engine.

Update rule (per closed trade, per signal i):

    raw_i  <- decay * raw_i + lr * direction_i * confidence_i * pnl_norm
    raw_i  <- max(weight_floor, raw_i)
    w      <- softmax(raw)

Where:
  * `decay`        is config (default 0.999) — old reads slowly forgotten.
  * `lr`           is config (default 0.02).
  * `pnl_norm`     = pnl_usd / max_bet_usd, clipped to [-1, 1].
  * `direction_i`  is the signed reading at trade open (positive = UP).
  * `confidence_i` weights how much the signal "asserted itself".

Notes:
  * We update `raw` (pre-softmax logits), not the softmax outputs, so
    floors and decay compose cleanly without re-renormalization drift.
  * The store persists the post-softmax weights for fast lookup; raw
    logits are recomputed by inverting softmax (via log) at startup.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from polymarket_btc_bot.config import Settings
from polymarket_btc_bot.learning.store import TradeStore
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger
from polymarket_btc_bot.signals.base import SignalReading

log = get_logger(__name__)


@dataclass
class TradeOutcome:
    market_id: str
    side: str
    entry_price: float
    shares: float
    notional_usd: float
    readings: tuple[SignalReading, ...]
    weights_at_open: dict[str, float]
    opened_at: float
    closed_at: float
    pnl_usd: float

    @property
    def won(self) -> bool:
        return self.pnl_usd > 0


def _softmax(logits: dict[str, float]) -> dict[str, float]:
    if not logits:
        return {}
    m = max(logits.values())
    exps = {k: math.exp(v - m) for k, v in logits.items()}
    z = sum(exps.values()) or 1.0
    return {k: v / z for k, v in exps.items()}


def _floor_blend(weights: dict[str, float], floor: float) -> dict[str, float]:
    """Mix softmax output with uniform so every weight >= floor while keeping sum=1.

        w' = (1 - n*floor) * w + floor   for each entry, where n = len(weights).

    Requires n*floor <= 1; otherwise clamps floor to 1/n.
    """
    n = len(weights)
    if n == 0:
        return {}
    f = min(floor, 1.0 / n)
    scale = 1.0 - n * f
    return {k: scale * v + f for k, v in weights.items()}


def _logit_from_softmax(weights: dict[str, float]) -> dict[str, float]:
    """Recover unnormalized logits from softmax weights (up to a constant).
    softmax(log w) == w, so storing log w gives an inverse."""
    out: dict[str, float] = {}
    for k, v in weights.items():
        out[k] = math.log(max(v, 1e-9))
    return out


class LearningEngine:
    def __init__(self, settings: Settings, store: TradeStore, signal_names: list[str]):
        self._cfg = settings
        self._store = store
        self._names = signal_names
        # logits live in memory; weights persist
        self._logits: dict[str, float] = {}
        self._reload()

    def _reload(self) -> None:
        weights = self._store.get_weights()
        if not weights or set(weights) != set(self._names):
            uniform = 1.0 / len(self._names)
            weights = {n: uniform for n in self._names}
            self._store.set_weights(weights)
        # Recover logits from the *softmax* component (before floor blend).
        # Inverting the blend: softmax = (w - f) / (1 - n*f). If w == f exactly,
        # treat it as the smallest possible logit and rebuild from there.
        self._logits = _logit_from_softmax(weights)
        for n, w in weights.items():
            metrics.SIGNAL_WEIGHT.labels(name=n).set(w)

    def current_weights(self) -> dict[str, float]:
        return _floor_blend(_softmax(self._logits), self._cfg.learning_weight_floor)

    def update(self, outcome: TradeOutcome) -> dict[str, float]:
        cfg = self._cfg
        pnl_norm = max(-1.0, min(1.0, outcome.pnl_usd / max(cfg.max_bet_usd, 1e-6)))

        # Reward = direction_i * confidence_i * pnl_norm. Positive when the signal
        # agreed with the winning side; abstaining signals get zero gradient.
        readings_by_name = {r.name: r for r in outcome.readings}
        for name in self._names:
            r = readings_by_name.get(name)
            d = r.direction if r else 0.0
            c = r.confidence if r else 0.0
            decayed = cfg.learning_decay * self._logits.get(name, 0.0)
            grad = cfg.learning_rate * d * c * pnl_norm
            self._logits[name] = decayed + grad

        weights = _floor_blend(_softmax(self._logits), cfg.learning_weight_floor)
        self._store.set_weights(weights)
        for n, w in weights.items():
            metrics.SIGNAL_WEIGHT.labels(name=n).set(w)
        log.info("learning.updated", weights=weights, pnl=outcome.pnl_usd)
        return weights
