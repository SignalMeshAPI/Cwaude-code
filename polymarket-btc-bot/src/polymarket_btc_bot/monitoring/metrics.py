"""Prometheus metrics. One global registry, lazy server start."""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server


# Trades
TRADES_TOTAL = Counter(
    "pmbot_trades_total",
    "Trades opened or closed, partitioned by outcome.",
    ["outcome"],  # opened | won | lost | expired_void
)

TRADE_OUTCOME = Gauge(
    "pmbot_trade_outcome",
    "Last trade outcome: 1 win, 0 loss. Time-series sampled for win rate.",
)

# PnL
PNL_USD = Gauge("pmbot_pnl_usd", "Cumulative realized PnL in USD.")
BANKROLL_USD = Gauge("pmbot_bankroll_usd", "Reported bankroll in USDC equivalent.")

# Signals
SIGNAL_VALUE = Gauge(
    "pmbot_signal_value",
    "Latest emitted signal direction in [-1, 1].",
    ["name"],
)
SIGNAL_CONFIDENCE = Gauge(
    "pmbot_signal_confidence",
    "Latest emitted signal confidence in [0, 1].",
    ["name"],
)
SIGNAL_WEIGHT = Gauge(
    "pmbot_signal_weight",
    "Current learned weight per signal.",
    ["name"],
)

# Fusion / decisions
FUSION_SCORE = Gauge("pmbot_fusion_score", "Last fused score in [-1, 1].")
FUSION_CONFIDENCE = Gauge("pmbot_fusion_confidence", "Last fused confidence in [0, 1].")
EDGE_GATE_PASS = Counter(
    "pmbot_edge_gate_total",
    "Decisions evaluated by the edge gate.",
    ["passed"],  # true | false
)

# Risk
RISK_BLOCK = Counter(
    "pmbot_risk_block_total",
    "Trades blocked by the risk engine, by reason.",
    ["reason"],
)
KILL_SWITCH_TRIPS = Counter(
    "pmbot_kill_switch_trips_total",
    "Number of times the consecutive-loss kill switch tripped.",
)

# IO
WS_RECONNECTS = Counter(
    "pmbot_ws_reconnects_total",
    "Websocket reconnect attempts, by source.",
    ["source"],
)
API_ERRORS = Counter(
    "pmbot_api_errors_total",
    "API errors encountered, by source.",
    ["source"],
)
RATE_LIMIT_DROPS = Counter(
    "pmbot_rate_limit_drops_total",
    "Requests dropped at the local rate limiter.",
    ["source"],
)

# Latency
ORDER_LATENCY = Histogram(
    "pmbot_order_latency_seconds",
    "End-to-end order placement latency.",
    buckets=(0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)


_started = False


def start_metrics_server(port: int) -> None:
    global _started
    if _started:
        return
    start_http_server(port)
    _started = True
