"""In-memory rolling market state. Single owner: the orchestrator.

Data-source workers write into MarketState. Signal evaluators read from it.
All access is from the asyncio event loop (no locks needed).
"""

from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field


@dataclass
class Tick:
    ts: float
    price: float


@dataclass
class MarketState:
    # Spot prices keyed by source -> latest tick
    binance_last: Tick | None = None
    coinbase_last: Tick | None = None

    # 1-minute close prices for spike detection (60 minutes of history)
    binance_1m_closes: deque[float] = field(default_factory=lambda: deque(maxlen=60))

    # Futures microstructure
    funding_rate: float | None = None  # fraction, e.g. 0.0001 = 1bp
    funding_rate_ts: float = 0.0
    orderbook_imbalance: float | None = None  # in [-1, 1]

    # Sentiment
    fear_greed: int | None = None  # 0..100
    fear_greed_ts: float = 0.0
    social_sentiment: float | None = None  # VADER compound EMA in [-1, 1]
    social_sentiment_ts: float = 0.0

    # Crypto-wide pulse from SOL spot price; 1-hour return as a risk-on indicator.
    sol_last: Tick | None = None
    sol_1h_return: float | None = None  # fraction, e.g. 0.02 for +2%
    sol_1h_return_ts: float = 0.0

    def freshness(self, now: float | None = None) -> dict[str, float]:
        """Age in seconds of each data point. Useful for validators / signals."""
        n = now if now is not None else time.time()
        return {
            "binance": (n - self.binance_last.ts) if self.binance_last else float("inf"),
            "coinbase": (n - self.coinbase_last.ts) if self.coinbase_last else float("inf"),
            "funding": (n - self.funding_rate_ts) if self.funding_rate is not None else float("inf"),
            "fear_greed": (n - self.fear_greed_ts) if self.fear_greed is not None else float("inf"),
            "social": (n - self.social_sentiment_ts) if self.social_sentiment is not None else float("inf"),
            "sol": (n - self.sol_last.ts) if self.sol_last else float("inf"),
        }
