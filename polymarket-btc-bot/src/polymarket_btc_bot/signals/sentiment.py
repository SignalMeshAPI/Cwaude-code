"""Sentiment signal: Fear & Greed (62.5%) blended with Reddit VADER (37.5%)."""

from __future__ import annotations

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.signals.base import SignalReading, clip

W_FNG = 0.625
W_SOCIAL = 0.375


class SentimentAnalyzer:
    name = "sentiment"

    async def value(self, state: MarketState) -> SignalReading:
        components: list[tuple[float, float]] = []  # (direction, weight)

        if state.fear_greed is not None:
            # Map 0..100 to direction in [-1, 1]: 50 = neutral, >50 greedy = UP bias
            fng_dir = clip((state.fear_greed - 50) / 50.0, -1.0, 1.0)
            components.append((fng_dir, W_FNG))

        if state.social_sentiment is not None:
            components.append((clip(state.social_sentiment, -1.0, 1.0), W_SOCIAL))

        if not components:
            return SignalReading(self.name, 0.0, 0.0)

        total_w = sum(w for _, w in components)
        direction = sum(d * w for d, w in components) / total_w
        # Confidence scales with how much sentiment depth we have AND
        # how far from neutral the blended reading is.
        coverage = total_w  # 0..1, since W_FNG + W_SOCIAL == 1.0
        confidence = clip(coverage * abs(direction), 0.0, 1.0)
        return SignalReading(self.name, direction, confidence)
