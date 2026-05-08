# Polymarket BTC 15-Minute Trading Bot

A 7-phase Python bot that trades Polymarket's recurring "Bitcoin Up or Down"
15-minute binary markets. Live trading on Polygon with real USDC; an in-memory
paper mode is provided for first-run verification.

> Trading binary markets is high-risk and the bot defaults are small for a
> reason. Read the **risk floors** section below before going live.

## Architecture

| Phase | Module | Role |
|-------|--------|------|
| 1 — Data | `src/polymarket_btc_bot/data/` | Binance spot WS, Binance futures funding + depth, Coinbase REST, Fear & Greed, Reddit/VADER, **SOL spot (crypto-wide pulse)**. Each writes into a single `MarketState`. |
| 2 — Signals | `src/polymarket_btc_bot/signals/` | `spike_detector`, `sentiment` (F&G + social + SOL momentum), `price_divergence`, `microstructure`. Each emits `(direction in [-1,1], confidence in [0,1])`. |
| 3 — Fusion | `src/polymarket_btc_bot/signals/fusion.py` | Weighted vote with **active-only renormalization** so abstaining signals don't dilute confident ones. Returns `(score, confidence, side, active_count)`. |
| 4 — Risk | `src/polymarket_btc_bot/risk/risk_engine.py` | Edge gate, kill switch, daily/weekly caps, position sizing. |
| 5 — Execution | `src/polymarket_btc_bot/execution/` | `approvals.py` (USDC + CTF), `polymarket_client.py` (3-step CLOB auth), `market_finder.py` (gamma-api), `executor.py` (FOK orders, SL/TP, settlement). |
| 6 — Monitoring | `src/polymarket_btc_bot/monitoring/` + `prometheus/` + `grafana/` | Prometheus metrics on `:9100`, 22-panel Grafana dashboard auto-loaded by `docker compose`. |
| 7 — Learning | `src/polymarket_btc_bot/learning/` | Online softmax weight update with floor blend (every weight ≥ floor regardless of gradient magnitude); SQLite trade history. |
| Backtest | `src/polymarket_btc_bot/backtest/` | Replay closed Polymarket BTC markets through the same signal/risk/learning code. Used to validate the model before going live. |

## Quick start

```bash
cp .env.example .env
# edit .env: set POLYMARKET_PRIVATE_KEY, optionally Reddit creds.
docker compose up --build
```

Grafana: http://localhost:3000 (admin/admin) — the "Polymarket BTC Bot" dashboard auto-loads.
Prometheus: http://localhost:9090
Bot metrics: http://localhost:9100/metrics

## Validate the model offline (recommended first step)

Before any real money, replay the live signal/risk/learning pipeline against
historical markets:

```bash
pmbot backtest --days 7 --initial-bankroll 100 --out report.json
```

Prints a textual summary (win rate, return %, Sharpe, max drawdown, longest
loss streak, per-signal correlation with outcomes, edge-gate skip reasons,
final learned weights) and writes a JSON report. Under the hood this fetches
1m Binance klines, 8h funding rate history, daily F&G index, and closed
Polymarket BTC markets, then drives them through `MarketState` → signals →
fusion → risk → executor (synthetic fill) → learner. Same code paths as live;
no network mocks.

The backtest reveals whether the bot's edge gate is firing, whether the
learner is converging, and which signals correlate with outcomes. If the
backtest's win rate is below 50% across enough markets, do not go live —
fix the signals or thresholds first.

## Going live (do this in order)

1. **Static**
   ```bash
   pip install -e '.[dev]'
   pytest -q
   ```
2. **Backtest** (above): make sure the model isn't obviously losing.
3. **Approvals dry run** — prints which contracts still need approval.
   ```bash
   pmbot approvals --dry-run
   ```
4. **Send approvals** — one-time, idempotent.
   ```bash
   pmbot approvals --execute
   ```
5. **Auth + market verify** — derives L2 creds, fetches the next BTC market and its order books.
   ```bash
   pmbot verify
   ```
6. **Paper run** (`--test-mode` runs the decision loop every 10s instead of every 30s for fast feedback):
   ```bash
   pmbot run --paper --test-mode
   ```
   Watch Grafana: ticks flow, signals populate, simulated trades land, learner updates weights.
7. **Inspect trades** any time:
   ```bash
   pmbot trades --limit 50
   ```
8. **First live trade with tightened caps** in `.env`:
   ```
   MAX_BET_USD=2
   KILL_SWITCH_CONSECUTIVE_LOSSES=1
   MAX_DAILY_LOSS_USD=2
   ```
   Then:
   ```bash
   pmbot run --live
   ```
9. **Halt path** — kill the bot mid-cycle, verify the open position survives in Redis, restart and confirm it tracks to settlement.

## Switching mode at runtime

```bash
docker exec pmbot-redis-1 redis-cli SET pmbot:mode paper
docker exec pmbot-redis-1 redis-cli SET pmbot:mode live
```

The orchestrator picks up the new mode at the start of the next cycle.

## Risk floors (non-overridable)

`config.py` enforces hard ceilings; env values that exceed them raise at startup.

| Setting | Default | Floor (max allowed by env) |
|---------|---------|---------------------------|
| `MAX_BET_USD` | 2.0 | 25.0 |
| `STOP_LOSS_PCT` | 0.30 | 0.50 |
| `TAKE_PROFIT_PCT` | 0.20 | 0.50 |
| `MIN_EDGE_CONFIDENCE` | 0.55 | floor at 0.50 (must be ≥) |
| `MAX_CONCURRENT_POSITIONS` | 1 | 3 |
| `MAX_DAILY_LOSS_USD` | 10.0 | 100.0 |
| `MAX_WEEKLY_LOSS_USD` | 30.0 | 300.0 |
| `KILL_SWITCH_CONSECUTIVE_LOSSES` | 5 | n/a (smaller is safer) |
| `MIN_BANKROLL_USD` | 10.0 | floor at 10.0 (must be ≥) |

## Polymarket-specific gotchas

- BTC 15-min markets are **neg-risk**. Three USDC ERC-20 approvals plus three
  ERC-1155 `setApprovalForAll` calls are required. `approvals.py` handles all
  six idempotently. Missing any → orders post but never fill.
- The `py-clob-client` SDK requires a 3-step auth: construct → derive API
  creds → re-instantiate with creds. `PolymarketClient.init()` does this.
- Tick size 0.01 in `[0.01, 0.99]`; orders are FOK by default.
- Polymarket protocol minimum order is ~$1 notional. Default bet is $2 to
  avoid rounding rejects on the floor.

## Grafana dashboard

The dashboard at `grafana/dashboard.json` ships 22 panels organized into 5 rows:

- **Performance KPIs** — bankroll, cumulative PnL, rolling win rate, trades today, edge-gate pass rate, kill-switch trips.
- **Equity & PnL** — bankroll curve and cumulative PnL over time.
- **Per-Signal Direction** — direction + confidence time series for each of `spike`, `sentiment`, `divergence`, `microstructure`.
- **Fusion & Learning** — stacked-area learned weights over time, fusion score, fusion confidence vs. the 0.55 gate threshold.
- **Risk Diagnostics** — risk-block reasons (donut), edge-gate pass-vs-block rate, trade outcome rate.
- **Infrastructure & IO** — WS reconnects per source, API errors per source, order placement latency p50/p95/p99, rate-limit drops.

Auto-loaded by `docker compose up`. To re-import after edits:
`http://localhost:3000` → Dashboards → Import → upload `grafana/dashboard.json`.

## Compatibility with the upstream aulekator bot

This bot is a clean reimplementation but accepts the upstream env-var names so
configs are portable:

| Upstream | Here | Notes |
|---|---|---|
| `POLYMARKET_PK` | `POLYMARKET_PRIVATE_KEY` | both accepted |
| `MAX_POSITION_SIZE` | `MAX_BET_USD` | both accepted |
| `REDIS_HOST` / `REDIS_PORT` / `REDIS_DB` | `REDIS_URL` | URL preferred; host/port/db used as fallback |
| `SPIKE_THRESHOLD` | `SPIKE_THRESHOLD_Z` | now expressed as a z-score multiplier |
| `DIVERGENCE_THRESHOLD` | `DIVERGENCE_THRESHOLD_BPS` | now expressed in basis points |

## License

Inherits the host repository's license (UNLICENSED — proprietary).
