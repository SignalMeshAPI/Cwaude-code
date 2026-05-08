"""Discover the next active 15-minute Polymarket BTC UP/DOWN market.

Uses gamma-api: GET /markets?closed=false&active=true&series_slug=bitcoin-up-or-down

Selects the market whose end_date_iso is the next quarter-hour boundary
(the one with the soonest future end timestamp). Polls on demand; caches
the most recent result so callers don't hammer the endpoint.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import get_logger

log = get_logger(__name__)


@dataclass(frozen=True)
class MarketInfo:
    market_id: str  # gamma id
    condition_id: str  # CTF condition id
    question: str
    end_ts: float  # unix seconds
    yes_token_id: str  # CLOB token id for "Up" / "Yes" outcome
    no_token_id: str  # CLOB token id for "Down" / "No" outcome
    series_slug: str

    @property
    def seconds_to_close(self) -> float:
        return self.end_ts - time.time()


class MarketFinder:
    def __init__(self, gamma_host: str, series_slug: str = "bitcoin-up-or-down"):
        self._host = gamma_host.rstrip("/")
        self._slug = series_slug
        self._cache: MarketInfo | None = None
        self._cache_ts: float = 0.0

    async def find_next(
        self, client: httpx.AsyncClient | None = None, max_cache_age: float = 30.0
    ) -> MarketInfo | None:
        if self._cache and (time.time() - self._cache_ts) < max_cache_age:
            if self._cache.seconds_to_close > 0:
                return self._cache

        owns_client = client is None
        if owns_client:
            client = httpx.AsyncClient(timeout=10.0)
        try:
            url = f"{self._host}/markets"
            params = {
                "closed": "false",
                "active": "true",
                "series_slug": self._slug,
                "limit": 50,
            }
            r = await client.get(url, params=params)
            r.raise_for_status()
            markets = r.json()
        except httpx.HTTPError as exc:
            metrics.API_ERRORS.labels(source="gamma_api").inc()
            log.warning("market_finder.error", error=str(exc))
            return self._cache  # stale-but-better-than-nothing
        finally:
            if owns_client and client is not None:
                await client.aclose()

        info = self._pick_next(markets)
        if info:
            self._cache = info
            self._cache_ts = time.time()
        return info

    def _pick_next(self, markets: list[dict]) -> MarketInfo | None:
        now = time.time()
        candidates: list[MarketInfo] = []
        for m in markets:
            try:
                end_iso = m.get("end_date_iso") or m.get("endDate") or m.get("endDateIso")
                if not end_iso:
                    continue
                end_ts = _parse_iso(end_iso)
                if end_ts <= now:
                    continue

                token_ids = m.get("clobTokenIds") or m.get("clob_token_ids")
                if isinstance(token_ids, str):
                    token_ids = json.loads(token_ids)
                if not token_ids or len(token_ids) < 2:
                    continue
                # Outcomes order convention: index 0 = Yes/Up, index 1 = No/Down
                yes_id, no_id = str(token_ids[0]), str(token_ids[1])

                cond_id = m.get("conditionId") or m.get("condition_id") or ""
                if not cond_id:
                    continue

                candidates.append(
                    MarketInfo(
                        market_id=str(m.get("id") or m.get("marketMakerId") or cond_id),
                        condition_id=cond_id,
                        question=str(m.get("question") or m.get("title") or ""),
                        end_ts=end_ts,
                        yes_token_id=yes_id,
                        no_token_id=no_id,
                        series_slug=str(m.get("series_slug") or self._slug),
                    )
                )
            except (ValueError, KeyError, TypeError) as exc:
                log.warning("market_finder.parse_skip", error=str(exc))
                continue

        if not candidates:
            return None
        candidates.sort(key=lambda c: c.end_ts)
        return candidates[0]


def _parse_iso(value: str) -> float:
    # Gamma returns ISO 8601 with trailing Z
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value).astimezone(timezone.utc).timestamp()
