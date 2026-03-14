"""
CLI interface for the trading bot.

Commands:
  run          — Start the bot (paper or live)
  status       — Show portfolio, recent trades, signal weights
  backtest     — Run strategy on historical data
  learn        — Trigger a manual learning pass
  dashboard    — Launch the web dashboard UI
"""

from __future__ import annotations

import click
from rich.console import Console
from rich.table import Table
from rich.panel import Panel

console = Console()


@click.group()
def cli():
    """🤖 Crypto Trading Bot — AI-powered trading with sentiment analysis."""
    pass


@cli.command()
@click.option(
    "--mode",
    type=click.Choice(["paper", "live"]),
    default="paper",
    help="Trading mode",
)
@click.option(
    "--strategy",
    type=click.Choice(["momentum_sentiment", "breakout_news"]),
    default="momentum_sentiment",
    help="Trading strategy",
)
def run(mode: str, strategy: str):
    """🚀 Start the trading bot."""
    if mode == "live":
        if not click.confirm(
            "⚠️  You are about to trade with REAL money. Continue?", default=False
        ):
            console.print("[yellow]Cancelled.[/]")
            return

    from src.__main__ import run_bot

    run_bot(mode=mode, strategy_name=strategy)


@cli.command()
def status():
    """📊 Show current portfolio status and recent trades."""
    from src.config import settings
    from src.database import init_db, get_db
    from src.logger import setup_logging
    from src.models import Trade, SignalWeight
    from src.trading.learner import Learner

    setup_logging(settings.log_level, settings.log_file)
    init_db()

    learner = Learner()
    summary = learner.get_performance_summary()

    # ── Performance Panel ──
    perf_text = (
        f"Total Trades:  {summary['total_trades']}\n"
        f"Win Rate:      {summary['win_rate_pct']}%\n"
        f"Total P&L:     ${summary['total_pnl']:+,.2f}"
    )
    console.print(Panel(perf_text, title="📈 Performance", border_style="green"))

    # ── Signal Weights ──
    wt = Table(title="🧠 Signal Weights (Learned)")
    wt.add_column("Source", style="cyan")
    wt.add_column("Weight", justify="right")
    wt.add_column("Accuracy", justify="right")
    wt.add_column("Signals", justify="right")

    for src, info in summary.get("signal_weights", {}).items():
        wt.add_row(
            src,
            f"{info['weight']:.3f}",
            f"{info['accuracy_pct']}%",
            str(info["total_signals"]),
        )
    console.print(wt)

    # ── Recent Trades ──
    with get_db() as db:
        recent = (
            db.query(Trade)
            .order_by(Trade.entry_time.desc())
            .limit(15)
            .all()
        )

    if recent:
        t = Table(title="📋 Recent Trades")
        t.add_column("#", style="dim")
        t.add_column("Symbol", style="cyan")
        t.add_column("Side")
        t.add_column("Entry", justify="right")
        t.add_column("Exit", justify="right")
        t.add_column("P&L", justify="right")
        t.add_column("Status")
        t.add_column("Strategy", style="dim")

        for trade in recent:
            pnl_str = f"${trade.pnl:+.2f}" if trade.pnl is not None else "—"
            pnl_style = "green" if (trade.pnl or 0) >= 0 else "red"
            side_style = "green" if trade.side == "BUY" else "red"

            t.add_row(
                str(trade.id),
                trade.symbol,
                f"[{side_style}]{trade.side}[/]",
                f"${trade.entry_price:,.2f}",
                f"${trade.exit_price:,.2f}" if trade.exit_price else "—",
                f"[{pnl_style}]{pnl_str}[/]",
                trade.status,
                trade.strategy,
            )
        console.print(t)
    else:
        console.print("[dim]No trades recorded yet.[/]")


@cli.command()
@click.option("--days", default=30, help="Number of days to backtest")
@click.option(
    "--strategy",
    type=click.Choice(["momentum_sentiment", "breakout_news"]),
    default="momentum_sentiment",
)
def backtest(days: int, strategy: str):
    """📈 Backtest a strategy against historical data."""
    from src.config import settings
    from src.database import init_db
    from src.logger import setup_logging
    from src.data.exchange import ExchangeClient
    from src.analysis.technical import analyse as ta_analyse
    from src.trading.strategy import get_strategy
    from src.trading.risk_manager import RiskManager
    from src.trading.executor import PaperExecutor

    setup_logging(settings.log_level, settings.log_file)
    init_db()

    console.print(f"\n[bold]Backtesting[/] {strategy} over {days} days…\n")

    exchange = ExchangeClient()
    strat = get_strategy(strategy)
    risk = RiskManager()
    executor = PaperExecutor()

    candles_needed = days * 24  # for 1h timeframe

    for symbol in settings.trading.symbols:
        console.print(f"  Processing [cyan]{symbol}[/]…")
        try:
            ohlcv = exchange.fetch_ohlcv(symbol, settings.trading.timeframe, limit=min(candles_needed, 1000))
            if len(ohlcv) < 50:
                console.print(f"    [yellow]Not enough data ({len(ohlcv)} candles)[/]")
                continue

            # Walk-forward simulation
            for i in range(50, len(ohlcv)):
                window = ohlcv.iloc[:i+1]
                signal = strat.evaluate(symbol, window)

                if signal.direction in ("BUY", "SELL"):
                    current_price = window["close"].iloc[-1]
                    can_trade, _ = risk.can_trade(executor.get_portfolio_value())
                    if not can_trade:
                        continue

                    open_trades = executor.get_open_trades(symbol)
                    if open_trades:
                        continue

                    size = risk.calculate_position_size(
                        executor.get_portfolio_value(),
                        signal.confidence,
                        current_price,
                    )
                    sl = risk.stop_loss_price(current_price, signal.direction)
                    tp = risk.take_profit_price(current_price, signal.direction)

                    executor.open_trade(symbol, signal.direction, size, current_price, strat.name, sl, tp)

                # Check exits on open positions
                for trade in executor.get_open_trades():
                    current_price = window["close"].iloc[-1]
                    should_exit, reason = risk.should_exit(trade, current_price)
                    if should_exit:
                        closed = executor.close_trade(trade, current_price, reason)
                        risk.record_trade_result(closed.pnl or 0)

        except Exception as e:
            console.print(f"    [red]Error: {e}[/]")

    # Close any remaining open positions
    pnl = executor.get_total_pnl()
    pnl_style = "green" if pnl >= 0 else "red"
    console.print(f"\n[bold]Backtest Result:[/]")
    console.print(f"  Final Balance: ${executor.balance:,.2f}")
    console.print(f"  Total P&L:     [{pnl_style}]${pnl:+,.2f}[/]")


@cli.command()
def learn():
    """🧠 Trigger a manual learning/retraining pass."""
    from src.config import settings
    from src.database import init_db
    from src.logger import setup_logging
    from src.trading.learner import Learner

    setup_logging(settings.log_level, settings.log_file)
    init_db()

    learner = Learner()
    console.print("[bold]Running learning pass…[/]")
    weights = learner.retrain_weights()
    console.print(f"Updated weights: {weights}")

@cli.command()
@click.option("--host", default=None, help="Dashboard host (default from .env)")
@click.option("--port", default=None, type=int, help="Dashboard port (default from .env)")
def dashboard(host: str, port: int):
    """🌐 Launch the web dashboard UI."""
    from src.dashboard.app import start_dashboard
    start_dashboard(host=host, port=port)


if __name__ == "__main__":
    cli()
