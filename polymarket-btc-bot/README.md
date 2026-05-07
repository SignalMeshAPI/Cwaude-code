# Polymarket BTC 15-Minute Trading Bot

A 7-phase Python bot that trades Polymarket's recurring "Bitcoin Up or Down"
15-minute binary markets. Live trading on Polygon with real USDC; an in-memory
paper mode is provided for first-run verification.

> Trading binary markets is high-risk and the bot defaults are small for a
> reason. Read the **risk floors** section below before going live.

## Architecture

| Phase | Module | Role |
|-------|--------|------|
| 1 — Data | `src/polymarket_btc_bot/data/` | Binance spot WS, Binance futures funding+depth, Coinbase REST, Fear & Greed, Reddit/VADER. Each writes into a single `MarketState`. |
| 2 — Signals | `src/polymarket_btc_bot/signals/` | `spike_detector`, `sentiment`, `price_divergence`, `microstructure`. Each emits `(direction in [-1,1], confidence in [0,1])`. |
| 3 — Fusion | `src/polymarket_btc_bot/signals/fusion.py` | Weighted vote into `(score, confidence, side)`. |
| 4 — Risk | `src/polymarket_btc_bot/risk/risk_engine.py` | Edge gate, kill switch, daily/weekly caps, position sizing. |
| 5 — Execution | `src/polymarket_btc_bot/execution/` | `approvals.py` (USDC + CTF), `polymarket_client.py` (3-step CLOB auth), `market_finder.py` (gamma-api), `executor.py` (FOK orders, SL/TP, settlement). |
| 6 — Monitoring | `src/polymarket_btc_bot/monitoring/` + `prometheus/` + `grafana/` | Prometheus metrics on `:9100`, importable Grafana dashboard. |
| 7 — Learning | `src/polymarket_btc_bot/learning/` | Online softmax weight update with floor blend; SQLite trade history. |

## Quick start

```bash
cp .env.example .env
# edit .env: set POLYMARKET_PRIVATE_KEY, optionally Reddit creds.
docker compose up --build
```

Grafana: http://localhost:3000 (admin/admin) — the "Polymarket BTC Bot" dashboard auto-loads.
Prometheus: http://localhost:9090
Bot metrics: http://localhost:9100/metrics

## Going live (do this in order)

1. **Static**
   ```bash
   pip install -e '.[dev]'
   pytest -q
   ```
2. **Approvals dry run** — prints which contracts still need approval.
   ```bash
   pmbot approvals --dry-run
   ```
3. **Send approvals** — one-time, idempotent.
   ```bash
   pmbot approvals --execute
   ```
4. **Auth + market verify** — derives L2 creds, fetches the next BTC market and its order books.
   ```bash
   python scripts/verify_polymarket_auth.py
   ```
5. **Paper run for 4+ cycles** (one hour). Watch Grafana: ticks should flow, signals should populate, simulated trades land, learner updates weights.
   ```bash
   pmbot run --paper
   ```
6. **First live trade with tightened caps** in `.env`:
   ```
   MAX_BET_USD=2
   KILL_SWITCH_CONSECUTIVE_LOSSES=1
   MAX_DAILY_LOSS_USD=2
   ```
   Then:
   ```bash
   pmbot run --live
   ```
7. **Halt path** — kill the bot mid-cycle, verify the open position survives in Redis, restart and confirm it tracks to settlement.

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

## License

Inherits the host repository's license (UNLICENSED — proprietary).
