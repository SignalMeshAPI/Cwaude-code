"""Redis-backed cross-process state. Used for the live/paper toggle and
in-flight position tracking that must survive a bot restart.

Keys:
  pmbot:mode                    -> "live" | "paper"
  pmbot:halt_until              -> unix-seconds float; bot will not trade until past
  pmbot:positions:<market_id>   -> JSON: open position record
  pmbot:consecutive_losses      -> int counter for kill switch
"""

from __future__ import annotations

import json
import time
from typing import Any, Literal

from redis.asyncio import Redis


Mode = Literal["live", "paper"]


class RedisState:
    def __init__(self, redis: Redis):
        self._r = redis

    @classmethod
    def from_url(cls, url: str) -> "RedisState":
        return cls(Redis.from_url(url, encoding="utf-8", decode_responses=True))

    async def close(self) -> None:
        await self._r.aclose()

    # ----- mode --------------------------------------------------------------

    async def get_mode(self, default: Mode = "paper") -> Mode:
        v = await self._r.get("pmbot:mode")
        if v in ("live", "paper"):
            return v  # type: ignore[return-value]
        return default

    async def set_mode(self, mode: Mode) -> None:
        await self._r.set("pmbot:mode", mode)

    # ----- halt --------------------------------------------------------------

    async def is_halted(self) -> bool:
        v = await self._r.get("pmbot:halt_until")
        if not v:
            return False
        try:
            return float(v) > time.time()
        except (TypeError, ValueError):
            return False

    async def halt_for(self, seconds: float) -> None:
        await self._r.set("pmbot:halt_until", str(time.time() + seconds))

    async def clear_halt(self) -> None:
        await self._r.delete("pmbot:halt_until")

    # ----- positions ---------------------------------------------------------

    async def save_position(self, market_id: str, payload: dict[str, Any]) -> None:
        await self._r.set(f"pmbot:positions:{market_id}", json.dumps(payload))

    async def get_position(self, market_id: str) -> dict[str, Any] | None:
        raw = await self._r.get(f"pmbot:positions:{market_id}")
        if not raw:
            return None
        return json.loads(raw)

    async def delete_position(self, market_id: str) -> None:
        await self._r.delete(f"pmbot:positions:{market_id}")

    async def list_positions(self) -> list[dict[str, Any]]:
        out = []
        async for key in self._r.scan_iter("pmbot:positions:*"):
            raw = await self._r.get(key)
            if raw:
                out.append(json.loads(raw))
        return out

    # ----- losses ------------------------------------------------------------

    async def get_consecutive_losses(self) -> int:
        v = await self._r.get("pmbot:consecutive_losses")
        return int(v) if v else 0

    async def record_loss(self) -> int:
        return int(await self._r.incr("pmbot:consecutive_losses"))

    async def reset_losses(self) -> None:
        await self._r.set("pmbot:consecutive_losses", "0")
