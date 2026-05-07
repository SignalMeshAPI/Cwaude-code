"""Signal protocol shared by all processors.

A signal returns a SignalReading: direction in [-1, 1] (negative = DOWN,
positive = UP) and confidence in [0, 1]. A signal that lacks data must
return confidence=0 (it will not move the fused score).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from polymarket_btc_bot.data.market_state import MarketState


@dataclass(frozen=True)
class SignalReading:
    name: str
    direction: float  # in [-1, 1]
    confidence: float  # in [0, 1]

    def __post_init__(self) -> None:
        if not (-1.0 <= self.direction <= 1.0):
            raise ValueError(f"direction {self.direction} out of [-1, 1]")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence {self.confidence} out of [0, 1]")


class Signal(Protocol):
    name: str

    async def value(self, state: MarketState) -> SignalReading: ...


def clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
