"""Sentiment signal: blend of Fear & Greed, Reddit VADER, and SOL momentum.

The blend is over whichever inputs are available at the time of evaluation.
Each component carries a fixed weight; total active weight is the "coverage"
and bounds confidence so signals with thin data don't dominate.
"""

from __future__ import annotations

from polymarket_btc_bot.data.market_state import MarketState
from polymarket_btc_bot.signals.base import SignalReading, clip

W_FNG = 0.50      # Fear & Greed index (macro market mood)
W_SOCIAL = 0.30   # Reddit VADER (retail sentiment)
W_SOL = 0.20      # SOL 1h return as a crypto-wide pulse

SOL_FULL_CONFIDENCE_RETURN = 0.02  # 2% in 1h = full confidence


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

        if state.sol_1h_return is not None:
            sol_dir = clip(state.sol_1h_return / SOL_FULL_CONFIDENCE_RETURN, -1.0, 1.0)
            components.append((sol_dir, W_SOL))

        if not components:
            return SignalReading(self.name, 0.0, 0.0)

        total_w = sum(w for _, w in components)
        direction = sum(d * w for d, w in components) / total_w
        coverage = total_w  # 0..1, since W_FNG + W_SOCIAL + W_SOL == 1.0
        confidence = clip(coverage * abs(direction), 0.0, 1.0)
        return SignalReading(self.name, direction, confidence)
