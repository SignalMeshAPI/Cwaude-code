"""CLI entrypoint."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import typer

from polymarket_btc_bot import __version__
from polymarket_btc_bot.config import get_settings
from polymarket_btc_bot.execution import approvals as approvals_mod
from polymarket_btc_bot.monitoring import metrics
from polymarket_btc_bot.monitoring.logger import configure_logging, get_logger
from polymarket_btc_bot.orchestrator import Orchestrator

cli = typer.Typer(no_args_is_help=False, add_completion=False)


@cli.callback()
def _root() -> None:
    cfg = get_settings()
    configure_logging(level=cfg.log_level, json_output=cfg.log_json)


@cli.command()
def version() -> None:
    """Print bot version."""
    typer.echo(__version__)


@cli.command()
def run(
    paper: bool = typer.Option(
        False, "--paper/--live", help="Force paper trading; overrides PMBOT_MODE."
    ),
    test_mode: bool = typer.Option(
        False, "--test-mode", help="Faster cycle (every 60s) for sanity-checking the pipeline."
    ),
) -> None:
    """Run the trading bot."""
    cfg = get_settings()
    log = get_logger(__name__)
    metrics.start_metrics_server(cfg.metrics_port)
    orch = Orchestrator(cfg, force_paper=paper, test_mode=test_mode)
    log.info("bot.starting", paper=paper, test_mode=test_mode, version=__version__)
    try:
        asyncio.run(orch.run())
    except KeyboardInterrupt:
        log.info("bot.shutdown")


@cli.command()
def approvals(
    dry_run: bool = typer.Option(
        True, "--dry-run/--execute", help="Dry-run prints needed approvals; --execute sends txs."
    ),
) -> None:
    """Inspect or send Polymarket on-chain approvals (USDC + CTF)."""
    cfg = get_settings()
    log = get_logger(__name__)
    pk = cfg.polymarket_private_key.get_secret_value()
    if not pk:
        typer.echo("POLYMARKET_PRIVATE_KEY missing in .env", err=True)
        raise typer.Exit(code=1)

    if dry_run:
        from web3 import Web3

        addr = Web3().eth.account.from_key(pk).address
        checks = approvals_mod.check_all(cfg.polygon_rpc_url, addr)
    else:
        checks = approvals_mod.ensure_all(cfg.polygon_rpc_url, pk, dry_run=False)

    log.info("approvals.summary", checks=[
        {"contract": c.contract, "spender": c.spender, "needed": c.needed, "note": c.note}
        for c in checks
    ])

    pending = [c for c in checks if c.needed]
    if pending and dry_run:
        typer.echo(f"{len(pending)} approval(s) pending. Re-run with --execute.")
        raise typer.Exit(code=2)


@cli.command()
def verify() -> None:
    """Verify Polymarket auth + show next 15-min market book."""
    asyncio.run(_verify_async())


@cli.command()
def backtest(
    days: int = typer.Option(7, "--days", min=1, max=60, help="Backtest window in days."),
    initial_bankroll: float = typer.Option(100.0, "--initial-bankroll", min=10.0),
    out: str = typer.Option("", "--out", help="Optional path to write JSON report."),
) -> None:
    """Replay closed Polymarket BTC markets through the live signal pipeline."""
    cfg = get_settings()
    log = get_logger(__name__)
    from polymarket_btc_bot.backtest.runner import BacktestParams, run_backtest

    log.info("backtest.cli.starting", days=days, initial_bankroll=initial_bankroll)
    params = BacktestParams(days=days, initial_bankroll_usd=initial_bankroll)
    report = asyncio.run(run_backtest(cfg=cfg, params=params))
    typer.echo(report.render_text())
    if out:
        Path(out).write_text(report.to_json())
        typer.echo(f"\nJSON report written to {out}")


@cli.command()
def trades(
    limit: int = typer.Option(20, "--limit", min=1, max=500),
    json_out: bool = typer.Option(False, "--json", help="Emit JSON instead of a table."),
) -> None:
    """Show recent trades from the learning store (paper or live)."""
    cfg = get_settings()
    from polymarket_btc_bot.learning.store import TradeStore

    store = TradeStore(cfg.learning_db_path)
    records = store.recent_trades(limit)
    if json_out:
        typer.echo(json.dumps([r.__dict__ for r in records], indent=2, default=str))
        return

    if not records:
        typer.echo("(no trades recorded)")
        return

    typer.echo(
        f"{'side':<5} {'entry':>6} {'shares':>8} {'pnl':>9} {'won':<4} {'market':<24}"
    )
    typer.echo("-" * 64)
    for r in records:
        typer.echo(
            f"{r.side:<5} {r.entry_price:>6.3f} {r.shares:>8.3f} "
            f"{r.pnl:>+9.4f} {('Y' if r.won else 'N'):<4} {r.market_id[:24]}"
        )


async def _verify_async() -> None:
    from polymarket_btc_bot.execution.market_finder import MarketFinder
    from polymarket_btc_bot.execution.polymarket_client import PolymarketClient

    cfg = get_settings()
    log = get_logger(__name__)

    pk = cfg.polymarket_private_key.get_secret_value()
    if not pk:
        typer.echo("POLYMARKET_PRIVATE_KEY missing", err=True)
        raise typer.Exit(code=1)

    client = PolymarketClient(cfg.polymarket_host, pk, cfg.polygon_chain_id)
    client.init()

    bal = await client.get_balance_usdc()
    log.info("verify.balance_usdc", balance=bal)

    finder = MarketFinder(cfg.polymarket_gamma_host)
    market = await finder.find_next()
    if not market:
        typer.echo("No active 15-min BTC market found.")
        raise typer.Exit(code=1)
    log.info(
        "verify.market",
        question=market.question,
        end_ts=market.end_ts,
        seconds_to_close=market.seconds_to_close,
    )

    yes_book = await client.get_book(market.yes_token_id)
    no_book = await client.get_book(market.no_token_id)
    log.info("verify.books", yes=yes_book, no=no_book)


if __name__ == "__main__":
    cli()
