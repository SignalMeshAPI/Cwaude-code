"""Cross-exchange price divergence between Binance and Coinbase.

A persistent positive (binance - coinbase) divergence implies Binance is
leading higher (UP bias on the 15-minute horizon, since Coinbase tends to
reprice). Magnitude is measured in basis points, capped at the threshold.
"""

from __future__ import annotations

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.signals.base import SignalReading, clip

THRESHOLD_BPS = 5.0  # 5 bps divergence = full confidence


class PriceDivergence:
    name = "divergence"

    async def value(self, state: MarketState) -> SignalReading:
        if not state.binance_last or not state.coinbase_last:
            return SignalReading(self.name, 0.0, 0.0)
        # require both ticks within last 30s
        if abs(state.binance_last.ts - state.coinbase_last.ts) > 30.0:
            return SignalReading(self.name, 0.0, 0.0)

        bp = state.binance_last.price
        cp = state.coinbase_last.price
        if cp <= 0:
            return SignalReading(self.name, 0.0, 0.0)

        bps = (bp - cp) / cp * 10_000.0
        direction = clip(bps / THRESHOLD_BPS, -1.0, 1.0)
        confidence = clip(abs(bps) / THRESHOLD_BPS, 0.0, 1.0)
        return SignalReading(self.name, direction, confidence)
