#!/usr/bin/env python3
"""Seed the learning store from past closed bitcoin-up-or-down markets.

Useful before going live to give the learner a head-start on signal weights
based on known-resolved markets. Pulls historical markets from gamma-api,
filters to closed BTC 15-min markets, and writes synthetic trade rows where
the bot would have taken the *neutral* position (size=0) so PnL is recorded
but no learning gradient is applied. The orchestrator's online learner then
takes over from real trades onward.
"""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from polymarket_btc_bot.config import get_settings  # noqa: E402
from polymarket_btc_bot.learning.store import TradeStore  # noqa: E402
from polymarket_btc_bot.monitoring.logger import configure_logging, get_logger  # noqa: E402


async def main(limit: int = 200) -> int:
    cfg = get_settings()
    configure_logging(level=cfg.log_level, json_output=False)
    log = get_logger("backfill")

    store = TradeStore(cfg.learning_db_path)
    url = f"{cfg.polymarket_gamma_host}/markets"
    params = {
        "closed": "true",
        "series_slug": "bitcoin-up-or-down",
        "limit": limit,
        "order": "endDate",
        "ascending": "false",
    }
    async with httpx.AsyncClient(timeout=15.0) as client:
        r = await client.get(url, params=params)
        r.raise_for_status()
        markets = r.json()

    written = 0
    for m in markets:
        try:
            outcomes_raw = m.get("outcomePrices") or "[]"
            if isinstance(outcomes_raw, str):
                import json as _json

                outcomes = _json.loads(outcomes_raw)
            else:
                outcomes = outcomes_raw
            if not outcomes or len(outcomes) < 2:
                continue
            up_won = float(outcomes[0]) >= 0.5
            store.insert_trade(
                market_id=str(m.get("id")),
                side="UP" if up_won else "DOWN",
                entry_price=0.5,
                shares=0.0,
                notional_usd=0.0,
                signals={},
                weights={},
                opened_at=0.0,
                closed_at=float(m.get("endDate", 0) or 0) / 1000.0,
                pnl=0.0,
                won=up_won,
            )
            written += 1
        except (ValueError, KeyError, TypeError):
            continue
    log.info("backfill.done", written=written, fetched=len(markets))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
