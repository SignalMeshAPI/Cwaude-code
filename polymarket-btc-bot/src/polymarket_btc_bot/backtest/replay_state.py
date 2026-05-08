"""Build a `MarketState` snapshot at a target point-in-time from historical data.

Backtest fidelity caveats:
  * 1m kline closes feed the spike detector (full fidelity).
  * Funding rate is sampled at 8h granularity; we use the most recent point
    on or before `target_ts`.
  * Fear & Greed is daily; we use the most recent point on or before `target_ts`.
  * Coinbase ticks aren't available historically — divergence signal is fed
    Binance==Coinbase, so it returns confidence ~0 (correct: no edge).
  * Orderbook imbalance and social sentiment aren't available historically —
    left as None (signals correctly emit confidence=0 when data missing).
  * SOL 1h return can optionally be supplied as a precomputed series; if absent,
    sentiment falls back to F&G + social only.
"""

from __future__ import annotations

import bisect
from dataclasses import dataclass

from polymarket_btc_bot.backtest.data_fetcher import FngPoint, FundingPoint, Kline
from polymarket_btc_bot.data.market_state import MarketState, Tick


@dataclass
class ReplayDataset:
    klines: list[Kline]
    funding: list[FundingPoint]
    fng: list[FngPoint]

    def __post_init__(self) -> None:
        # Pre-sort for binary search lookups
        self.klines.sort(key=lambda k: k.open_ts)
        self.funding.sort(key=lambda f: f.ts)
        self.fng.sort(key=lambda f: f.ts)
        self._kline_open_ts = [k.open_ts for k in self.klines]
        self._funding_ts = [f.ts for f in self.funding]
        self._fng_ts = [f.ts for f in self.fng]

    def state_at(self, target_ts: float, *, kline_window: int = 60) -> MarketState:
        """Build a MarketState as of `target_ts`. Uses up to `kline_window`
        most recent closed 1m bars, the latest funding point, and the latest
        F&G value at or before `target_ts`."""
        state = MarketState()

        # klines: include bars that closed strictly before target_ts
        idx = bisect.bisect_right(self._kline_open_ts, target_ts) - 1
        if idx >= 0:
            window = self.klines[max(0, idx - kline_window + 1) : idx + 1]
            for k in window:
                if k.close_ts <= target_ts:
                    state.binance_1m_closes.append(k.close)
            last = window[-1]
            state.binance_last = Tick(ts=min(last.close_ts, target_ts), price=last.close)
            # Treat coinbase as identical to Binance close in backtest mode.
            state.coinbase_last = Tick(ts=state.binance_last.ts, price=last.close)

        # funding: most recent point at or before target_ts
        f_idx = bisect.bisect_right(self._funding_ts, target_ts) - 1
        if f_idx >= 0:
            f = self.funding[f_idx]
            state.funding_rate = f.rate
            state.funding_rate_ts = f.ts

        # fear & greed: most recent point at or before target_ts
        g_idx = bisect.bisect_right(self._fng_ts, target_ts) - 1
        if g_idx >= 0:
            g = self.fng[g_idx]
            state.fear_greed = g.value
            state.fear_greed_ts = g.ts

        # orderbook imbalance and social sentiment intentionally left None.
        return state
