"""Trade executor: turn a fused decision into a placed order, then track
fill and final settlement.

A 15-minute Polymarket BTC market resolves automatically at expiry (Pyth
oracle resolution). For our binary-bet sizing scheme:

  * We open by placing a FOK BUY on the chosen outcome (UP or DOWN).
  * The implied price is also our entry cost per share.
  * Settlement payoff per share at expiry: 1.0 USDC if correct, 0 if not.
  * Stop-loss / take-profit are emulated by placing exit SELL orders if
    the live mid-price drifts beyond thresholds before expiry. These are
    "best effort" since liquidity is thin in the final minute.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from polymarket_btc_bot.execution.market_finder import MarketInfo
from polymarket_btc_bot.execution.polymarket_client import (
    OrderResult,
    PolymarketClientProtocol,
    _round_to_tick,
)
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger
from polymarket_btc_bot.signals.fusion import FusedDecision

log = get_logger(__name__)

# Default mid-fallback if order book is empty
FALLBACK_PRICE = 0.50


@dataclass
class OpenPosition:
    market_id: str
    condition_id: str
    token_id: str
    side: str  # "UP" | "DOWN"
    entry_price: float
    shares: float
    notional_usd: float
    opened_ts: float
    end_ts: float
    order_id: str
    extras: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "market_id": self.market_id,
            "condition_id": self.condition_id,
            "token_id": self.token_id,
            "side": self.side,
            "entry_price": self.entry_price,
            "shares": self.shares,
            "notional_usd": self.notional_usd,
            "opened_ts": self.opened_ts,
            "end_ts": self.end_ts,
            "order_id": self.order_id,
            "extras": self.extras,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "OpenPosition":
        return cls(
            market_id=d["market_id"],
            condition_id=d["condition_id"],
            token_id=d["token_id"],
            side=d["side"],
            entry_price=float(d["entry_price"]),
            shares=float(d["shares"]),
            notional_usd=float(d["notional_usd"]),
            opened_ts=float(d["opened_ts"]),
            end_ts=float(d["end_ts"]),
            order_id=d["order_id"],
            extras=d.get("extras", {}),
        )


def implied_price_from_book(book: dict, side: str) -> float:
    """For BUY: take best ask. For SELL: take best bid. Fallback to 0.5."""
    if side == "BUY":
        asks = book.get("asks") or []
        if asks:
            return float(asks[0]["price"]) if isinstance(asks[0], dict) else float(asks[0].price)
    else:  # SELL
        bids = book.get("bids") or []
        if bids:
            return float(bids[0]["price"]) if isinstance(bids[0], dict) else float(bids[0].price)
    return FALLBACK_PRICE


class Executor:
    def __init__(self, client: PolymarketClientProtocol, max_bet_usd: float):
        self._client = client
        self._max_bet_usd = max_bet_usd

    async def open_position(
        self, market: MarketInfo, decision: FusedDecision
    ) -> OpenPosition | None:
        if decision.side is None:
            return None

        if decision.side == "UP":
            token_id = market.yes_token_id
        else:
            token_id = market.no_token_id

        book = await self._client.get_book(token_id)
        ask = implied_price_from_book(book, "BUY")

        # Bid one tick over the best ask to maximize fill probability under FOK
        price = _round_to_tick(min(0.99, ask + 0.01))
        if price <= 0.0:
            log.warning("executor.no_price_available", market=market.market_id)
            return None

        # Notional = max_bet_usd; shares = notional / price
        notional = self._max_bet_usd
        shares = round(notional / price, 4)
        if shares < 1.0:  # Polymarket SDK requires at least 1 share for many markets
            log.warning("executor.size_too_small", price=price, notional=notional, shares=shares)
            return None

        result: OrderResult = await self._client.place_order(
            token_id=token_id,
            side="BUY",
            price=price,
            size_shares=shares,
            neg_risk=True,
            order_type="FOK",
        )
        if not result.success or result.filled_size <= 0:
            metrics.TRADES_TOTAL.labels(outcome="rejected").inc()
            log.warning(
                "executor.order_rejected",
                market=market.market_id,
                side=decision.side,
                price=price,
                shares=shares,
                raw=result.raw,
            )
            return None

        metrics.TRADES_TOTAL.labels(outcome="opened").inc()
        log.info(
            "executor.opened",
            market=market.market_id,
            side=decision.side,
            price=price,
            shares=result.filled_size,
            order_id=result.order_id,
        )

        return OpenPosition(
            market_id=market.market_id,
            condition_id=market.condition_id,
            token_id=token_id,
            side=decision.side,
            entry_price=result.avg_price,
            shares=result.filled_size,
            notional_usd=result.avg_price * result.filled_size,
            opened_ts=time.time(),
            end_ts=market.end_ts,
            order_id=result.order_id,
        )

    async def maybe_exit_early(
        self,
        pos: OpenPosition,
        *,
        stop_loss_pct: float,
        take_profit_pct: float,
    ) -> OrderResult | None:
        """Inspect current mid; if PnL crosses SL/TP thresholds, sell out."""
        book = await self._client.get_book(pos.token_id)
        bid = implied_price_from_book(book, "SELL")
        if bid <= 0.0:
            return None

        # PnL per share if we sold now
        pnl_per_share = bid - pos.entry_price
        pnl_pct = pnl_per_share / pos.entry_price if pos.entry_price > 0 else 0.0

        if pnl_pct >= take_profit_pct or pnl_pct <= -stop_loss_pct:
            sell_price = _round_to_tick(max(0.01, bid - 0.01))
            return await self._client.place_order(
                token_id=pos.token_id,
                side="SELL",
                price=sell_price,
                size_shares=pos.shares,
                neg_risk=True,
                order_type="FOK",
            )
        return None

    @staticmethod
    def settle_payoff(pos: OpenPosition, won: bool) -> float:
        """Realized PnL in USD assuming the market resolved automatically."""
        if won:
            payoff = pos.shares * 1.0  # YES/NO pays 1.0 per winning share
        else:
            payoff = 0.0
        return payoff - pos.notional_usd
