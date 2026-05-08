"""SQLite-backed trade history and per-signal weight persistence."""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Iterator


@dataclass
class TradeRecord:
    id: int | None
    market_id: str
    side: str
    entry_price: float
    shares: float
    notional_usd: float
    signals_json: str
    weights_json: str
    opened_at: float
    closed_at: float
    pnl: float
    won: int  # 0/1


_SCHEMA = """
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    market_id TEXT NOT NULL,
    side TEXT NOT NULL,
    entry_price REAL NOT NULL,
    shares REAL NOT NULL,
    notional_usd REAL NOT NULL,
    signals_json TEXT NOT NULL,
    weights_json TEXT NOT NULL,
    opened_at REAL NOT NULL,
    closed_at REAL NOT NULL,
    pnl REAL NOT NULL,
    won INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trades_closed_at ON trades(closed_at);
CREATE INDEX IF NOT EXISTS idx_trades_market ON trades(market_id);

CREATE TABLE IF NOT EXISTS weights (
    name TEXT PRIMARY KEY,
    value REAL NOT NULL,
    updated_at REAL NOT NULL
);
"""


class TradeStore:
    def __init__(self, db_path: str):
        self._path = db_path
        self._init()

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self._path, isolation_level=None)  # autocommit
        try:
            yield c
        finally:
            c.close()

    def _init(self) -> None:
        with self._conn() as c:
            c.executescript(_SCHEMA)

    # ----- trades ------------------------------------------------------------

    def insert_trade(
        self,
        *,
        market_id: str,
        side: str,
        entry_price: float,
        shares: float,
        notional_usd: float,
        signals: dict[str, dict[str, float]],
        weights: dict[str, float],
        opened_at: float,
        closed_at: float,
        pnl: float,
        won: bool,
    ) -> int:
        with self._conn() as c:
            cur = c.execute(
                "INSERT INTO trades(market_id,side,entry_price,shares,notional_usd,signals_json,weights_json,opened_at,closed_at,pnl,won) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (
                    market_id,
                    side,
                    entry_price,
                    shares,
                    notional_usd,
                    json.dumps(signals),
                    json.dumps(weights),
                    opened_at,
                    closed_at,
                    pnl,
                    1 if won else 0,
                ),
            )
            return int(cur.lastrowid or 0)

    def recent_trades(self, n: int) -> list[TradeRecord]:
        with self._conn() as c:
            rows = c.execute(
                "SELECT id,market_id,side,entry_price,shares,notional_usd,signals_json,weights_json,opened_at,closed_at,pnl,won "
                "FROM trades ORDER BY id DESC LIMIT ?",
                (n,),
            ).fetchall()
        return [
            TradeRecord(
                id=r[0], market_id=r[1], side=r[2], entry_price=r[3], shares=r[4],
                notional_usd=r[5], signals_json=r[6], weights_json=r[7],
                opened_at=r[8], closed_at=r[9], pnl=r[10], won=r[11],
            )
            for r in rows
        ]

    def daily_pnl(self, since_ts: float) -> float:
        with self._conn() as c:
            row = c.execute(
                "SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE closed_at >= ?",
                (since_ts,),
            ).fetchone()
        return float(row[0]) if row else 0.0

    # ----- weights -----------------------------------------------------------

    def get_weights(self) -> dict[str, float]:
        with self._conn() as c:
            rows = c.execute("SELECT name, value FROM weights").fetchall()
        return {r[0]: float(r[1]) for r in rows}

    def set_weights(self, weights: dict[str, float]) -> None:
        now = time.time()
        with self._conn() as c:
            c.executemany(
                "INSERT INTO weights(name, value, updated_at) VALUES (?,?,?) "
                "ON CONFLICT(name) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at",
                [(n, v, now) for n, v in weights.items()],
            )
