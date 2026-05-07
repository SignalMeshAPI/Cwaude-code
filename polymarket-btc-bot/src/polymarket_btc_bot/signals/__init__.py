"""Phase 2-4: signal processors and fusion."""

from polymarket_btc_bot.signals.base import Signal, SignalReading
from polymarket_btc_bot.signals.fusion import FusedDecision, fuse

__all__ = ["Signal", "SignalReading", "FusedDecision", "fuse"]
