"""Spike detector: z-score of latest 1-minute log-return vs trailing window."""

from __future__ import annotations

import math

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.signals.base import SignalReading, clip

WARMUP_BARS = 20  # need at least this many 1m closes to score


class SpikeDetector:
    name = "spike"

    async def value(self, state: MarketState) -> SignalReading:
        closes = list(state.binance_1m_closes)
        if len(closes) < WARMUP_BARS:
            return SignalReading(self.name, 0.0, 0.0)

        # log returns
        rets: list[float] = []
        for a, b in zip(closes[:-1], closes[1:]):
            if a > 0 and b > 0:
                rets.append(math.log(b / a))
        if len(rets) < WARMUP_BARS - 1:
            return SignalReading(self.name, 0.0, 0.0)

        last = rets[-1]
        prior = rets[:-1]
        mean = sum(prior) / len(prior)
        var = sum((r - mean) ** 2 for r in prior) / max(len(prior) - 1, 1)
        sd = math.sqrt(var) if var > 0 else 0.0
        if sd == 0.0:
            return SignalReading(self.name, 0.0, 0.0)

        z = (last - mean) / sd
        # direction = sign of z; magnitude/3 mapped to confidence
        direction = clip(z / 3.0, -1.0, 1.0)
        confidence = clip(abs(z) / 3.0, 0.0, 1.0)
        return SignalReading(self.name, direction, confidence)
