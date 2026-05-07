"""Microstructure signal: orderbook imbalance + funding-rate sign.

Both inputs are perp-futures-derived. Funding rate is bounded in practice
to ±0.1% per 8h on BTC; we map ±0.05% to ±1 confidence.
"""

from __future__ import annotations

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.signals.base import SignalReading, clip

FUNDING_FULL_CONF = 0.0005  # 5 bp / 8h funding -> full confidence


class Microstructure:
    name = "microstructure"

    async def value(self, state: MarketState) -> SignalReading:
        components: list[tuple[float, float]] = []  # (direction, confidence)

        if state.orderbook_imbalance is not None:
            d = clip(state.orderbook_imbalance, -1.0, 1.0)
            components.append((d, abs(d)))

        if state.funding_rate is not None:
            d = clip(state.funding_rate / FUNDING_FULL_CONF, -1.0, 1.0)
            components.append((d, abs(d)))

        if not components:
            return SignalReading(self.name, 0.0, 0.0)

        # Equal-weight blend
        direction = sum(d for d, _ in components) / len(components)
        confidence = sum(c for _, c in components) / len(components)
        return SignalReading(self.name, clip(direction, -1.0, 1.0), clip(confidence, 0.0, 1.0))
