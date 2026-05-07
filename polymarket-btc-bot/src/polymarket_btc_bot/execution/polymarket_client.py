"""Wrapper around py-clob-client for the BTC 15-minute neg-risk markets.

Auth flow (per Polymarket docs):
  1. Construct ClobClient(host, key=PRIVATE_KEY, chain_id=137).
  2. Call create_or_derive_api_creds() -> ApiCreds (HMAC L2 keys).
  3. Re-instantiate ClobClient(host, key=..., chain_id=..., creds=creds)
     so subsequent L2 endpoints (orders, trades) sign correctly.

Order placement:
  - 15-minute BTC markets are NEG-RISK. The CLOB SDK accepts a
    `neg_risk=True` flag on order creation; if absent, orders post but
    the matching engine will not pair them.
  - Tick size is 0.01 in [0.01, 0.99]. Round prices to nearest tick.
  - Default order type is FOK (fill or kill) for fast 15-min cycles.

This module exposes a tiny surface so the rest of the bot doesn't depend
on the SDK directly. A `PaperTradingClient` mock with the same interface
is injected when running in paper mode.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol

from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


Side = Literal["BUY", "SELL"]


@dataclass(frozen=True)
class OrderResult:
    success: bool
    order_id: str
    filled_size: float
    avg_price: float
    raw: dict[str, Any]


class PolymarketClientProtocol(Protocol):
    async def get_book(self, token_id: str) -> dict[str, Any]: ...
    async def get_balance_usdc(self) -> float: ...
    async def place_order(
        self,
        *,
        token_id: str,
        side: Side,
        price: float,
        size_shares: float,
        neg_risk: bool = True,
        order_type: str = "FOK",
    ) -> OrderResult: ...
    async def cancel_order(self, order_id: str) -> bool: ...


def _round_to_tick(price: float) -> float:
    p = round(price * 100) / 100.0
    return max(0.01, min(0.99, p))


class PolymarketClient:
    """Thin async-friendly wrapper. The underlying SDK is sync; calls are
    offloaded to a thread by the orchestrator if needed (most calls are
    quick enough that direct sync use is acceptable inside a worker)."""

    def __init__(self, host: str, private_key: str, chain_id: int = 137):
        self._host = host
        self._pk = private_key
        self._chain_id = chain_id
        self._client = None

    def init(self) -> None:
        """3-step init. Synchronous; call once at startup."""
        try:
            from py_clob_client.client import ClobClient
            from py_clob_client.clob_types import ApiCreds
        except ImportError as exc:
            raise RuntimeError(
                "py-clob-client not installed. Run `pip install py-clob-client`."
            ) from exc

        # Step 1: construct
        c0 = ClobClient(self._host, key=self._pk, chain_id=self._chain_id)
        # Step 2: derive L2 creds
        creds: ApiCreds = c0.create_or_derive_api_creds()
        # Step 3: reinit with creds attached
        self._client = ClobClient(
            self._host, key=self._pk, chain_id=self._chain_id, creds=creds
        )
        log.info("polymarket_client.init", host=self._host)

    def _sdk(self) -> Any:
        if self._client is None:
            raise RuntimeError("PolymarketClient.init() must be called first")
        return self._client

    async def get_book(self, token_id: str) -> dict[str, Any]:
        try:
            book = self._sdk().get_order_book(token_id)
            return _book_to_dict(book)
        except Exception as exc:  # noqa: BLE001
            metrics.API_ERRORS.labels(source="polymarket_clob").inc()
            log.warning("polymarket.get_book.error", error=str(exc))
            return {"bids": [], "asks": []}

    async def get_balance_usdc(self) -> float:
        try:
            from py_clob_client.clob_types import BalanceAllowanceParams, AssetType

            params = BalanceAllowanceParams(asset_type=AssetType.COLLATERAL)
            res = self._sdk().get_balance_allowance(params)
            # SDK returns balance in 1e6 fixed-point USDC
            raw = res.get("balance") if isinstance(res, dict) else getattr(res, "balance", 0)
            return float(raw) / 1_000_000.0
        except Exception as exc:  # noqa: BLE001
            metrics.API_ERRORS.labels(source="polymarket_clob").inc()
            log.warning("polymarket.get_balance.error", error=str(exc))
            return 0.0

    async def place_order(
        self,
        *,
        token_id: str,
        side: Side,
        price: float,
        size_shares: float,
        neg_risk: bool = True,
        order_type: str = "FOK",
    ) -> OrderResult:
        try:
            from py_clob_client.clob_types import OrderArgs, OrderType
        except ImportError as exc:
            raise RuntimeError("py-clob-client not installed") from exc

        rounded = _round_to_tick(price)
        otype = getattr(OrderType, order_type, OrderType.FOK)

        args = OrderArgs(
            price=rounded,
            size=round(size_shares, 4),
            side=side,
            token_id=token_id,
        )

        start = time.monotonic()
        try:
            signed = self._sdk().create_order(args, options={"neg_risk": neg_risk})
            resp = self._sdk().post_order(signed, otype)
        except Exception as exc:  # noqa: BLE001
            metrics.API_ERRORS.labels(source="polymarket_clob").inc()
            log.error("polymarket.place_order.error", error=str(exc))
            return OrderResult(success=False, order_id="", filled_size=0.0, avg_price=0.0, raw={"error": str(exc)})
        finally:
            metrics.ORDER_LATENCY.observe(time.monotonic() - start)

        success = bool(resp.get("success", False)) if isinstance(resp, dict) else False
        order_id = str(resp.get("orderID") or resp.get("order_id") or "") if isinstance(resp, dict) else ""
        filled = float(resp.get("makingAmount", 0.0)) / 1_000_000.0 if isinstance(resp, dict) else 0.0
        return OrderResult(
            success=success,
            order_id=order_id,
            filled_size=filled,
            avg_price=rounded,
            raw=resp if isinstance(resp, dict) else {},
        )

    async def cancel_order(self, order_id: str) -> bool:
        try:
            res = self._sdk().cancel(order_id)
            return bool(res)
        except Exception as exc:  # noqa: BLE001
            metrics.API_ERRORS.labels(source="polymarket_clob").inc()
            log.warning("polymarket.cancel.error", error=str(exc))
            return False


def _book_to_dict(book: Any) -> dict[str, Any]:
    if isinstance(book, dict):
        return book
    bids = getattr(book, "bids", []) or []
    asks = getattr(book, "asks", []) or []
    return {
        "bids": [{"price": float(b.price), "size": float(b.size)} for b in bids],
        "asks": [{"price": float(a.price), "size": float(a.size)} for a in asks],
    }


class PaperTradingClient:
    """In-memory mock that fills any FOK order at the chosen price.

    Used in --paper mode; orders never touch the network. Fills are
    recorded so the executor and learning engine can do post-mortem
    accounting against real market resolution.
    """

    def __init__(self, starting_balance: float = 100.0):
        self._balance = starting_balance
        self._next_id = 1
        self._open: dict[str, dict[str, Any]] = {}

    async def get_book(self, token_id: str) -> dict[str, Any]:
        # Empty book; executor falls back to "trade at signal-implied price"
        return {"bids": [], "asks": []}

    async def get_balance_usdc(self) -> float:
        return self._balance

    async def place_order(
        self,
        *,
        token_id: str,
        side: Side,
        price: float,
        size_shares: float,
        neg_risk: bool = True,
        order_type: str = "FOK",
    ) -> OrderResult:
        rounded = _round_to_tick(price)
        oid = f"paper-{self._next_id}"
        self._next_id += 1
        notional = rounded * size_shares
        if side == "BUY":
            if notional > self._balance:
                return OrderResult(False, "", 0.0, 0.0, {"error": "insufficient_paper_balance"})
            self._balance -= notional
        else:
            self._balance += notional
        self._open[oid] = {"token_id": token_id, "side": side, "price": rounded, "size": size_shares}
        return OrderResult(True, oid, size_shares, rounded, {"paper": True})

    async def cancel_order(self, order_id: str) -> bool:
        return self._open.pop(order_id, None) is not None
