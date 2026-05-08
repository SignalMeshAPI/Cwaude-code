#!/usr/bin/env python3
"""One-shot Polymarket auth + market lookup verifier.

Reads .env, derives L2 API credentials from POLYMARKET_PRIVATE_KEY, fetches
USDC balance, finds the next 15-min BTC market, and prints both order books.
Use this before flipping into live mode for the first time.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_btc_bot.config import get_settings  # noqa: E402
from polymarket_btc_bot.execution.market_finder import MarketFinder  # noqa: E402
from polymarket_btc_bot.execution.polymarket_client import PolymarketClient  # noqa: E402
from polymarket_btc_bot.monitoring.logger import configure_logging, get_logger  # noqa: E402


async def main() -> int:
    cfg = get_settings()
    configure_logging(level=cfg.log_level, json_output=False)
    log = get_logger("verify")

    pk = cfg.polymarket_private_key.get_secret_value()
    if not pk:
        log.error("missing.private_key", help="set POLYMARKET_PRIVATE_KEY in .env")
        return 1

    log.info("step.1.derive_creds")
    client = PolymarketClient(cfg.polymarket_host, pk, cfg.polygon_chain_id)
    client.init()

    log.info("step.2.balance")
    bal = await client.get_balance_usdc()
    log.info("balance.usdc", balance=bal)

    log.info("step.3.find_market")
    finder = MarketFinder(cfg.polymarket_gamma_host)
    market = await finder.find_next()
    if not market:
        log.error("no_market_found")
        return 2
    log.info(
        "market.found",
        question=market.question,
        seconds_to_close=int(market.seconds_to_close),
        condition_id=market.condition_id,
    )

    log.info("step.4.fetch_books")
    yes_book = await client.get_book(market.yes_token_id)
    no_book = await client.get_book(market.no_token_id)
    log.info("book.yes", asks=yes_book.get("asks", [])[:3], bids=yes_book.get("bids", [])[:3])
    log.info("book.no", asks=no_book.get("asks", [])[:3], bids=no_book.get("bids", [])[:3])

    log.info("verify.ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
