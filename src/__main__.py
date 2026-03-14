"""
Main trading engine — can run standalone or as a background thread
controlled by the dashboard.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys
import time
import threading
from datetime import datetime, timezone
from typing import Optional, List, Dict, Any

from src.config import settings
from src.database import init_db, get_db
from src.logger import setup_logging, get_logger
from src.data.exchange import ExchangeClient
from src.data.news import fetch_news_for_symbol
from src.analysis.technical import analyse as ta_analyse
from src.analysis.sentiment import analyse_news, SentimentSignal
from src.analysis.signal_aggregator import aggregate
from src.trading.strategy import get_strategy
from src.trading.risk_manager import RiskManager
from src.trading.executor import PaperExecutor, LiveExecutor
from src.trading.learner import Learner

logger: logging.Logger = None  # type: ignore


# ══════════════════════════════════════════════════════════════════════
# Bot Engine (thread-safe singleton)
# ══════════════════════════════════════════════════════════════════════

class BotEngine:
    """Trading bot engine that can run in a background thread."""

    _instance: Optional["BotEngine"] = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialised = False
            return cls._instance

    def __init__(self):
        if self._initialised:
            return
        self._initialised = True
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._cycle = 0
        self._last_activity: Optional[str] = None
        self._activity_log: List[Dict[str, Any]] = []
        self._max_log_size = 200
        self.executor: Optional[PaperExecutor] = None
        self.exchange: Optional[ExchangeClient] = None
        self.strategy = None
        self.risk: Optional[RiskManager] = None
        self.learner: Optional[Learner] = None

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def cycle(self) -> int:
        return self._cycle

    @property
    def last_activity(self) -> Optional[str]:
        return self._last_activity

    @property
    def activity_log(self) -> List[Dict[str, Any]]:
        return list(self._activity_log)

    def _log_activity(self, msg: str, level: str = "info", data: Optional[Dict] = None):
        """Log an activity event for the dashboard feed."""
        entry = {
            "time": datetime.now(timezone.utc).isoformat(),
            "message": msg,
            "level": level,
            "cycle": self._cycle,
            "data": data or {},
        }
        self._activity_log.append(entry)
        if len(self._activity_log) > self._max_log_size:
            self._activity_log = self._activity_log[-self._max_log_size:]
        self._last_activity = msg

    def start(
        self,
        strategy_name: str = "momentum_sentiment",
    ) -> str:
        """Start paper trading in a background thread."""
        if self._running:
            return "Bot is already running"

        init_db()

        self.exchange = ExchangeClient()
        self.strategy = get_strategy(strategy_name)
        self.risk = RiskManager()
        self.learner = Learner()
        self.executor = PaperExecutor()
        self._cycle = 0
        self._activity_log = []

        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

        self._log_activity(
            f"🚀 Bot started — {strategy_name} strategy, "
            f"symbols: {', '.join(settings.trading.symbols)}"
        )
        return "Bot started"

    def stop(self) -> str:
        """Stop the bot gracefully."""
        if not self._running:
            return "Bot is not running"

        self._running = False
        self._log_activity("🛑 Bot stopped by user")

        if self.executor:
            pnl = self.executor.get_total_pnl()
            self._log_activity(
                f"📊 Final P&L: ${pnl:+,.2f} | Balance: ${self.executor.balance:,.2f}"
            )

        return "Bot stopped"

    def get_status(self) -> Dict[str, Any]:
        """Return the current status of the bot."""
        return {
            "running": self._running,
            "cycle": self._cycle,
            "last_activity": self._last_activity,
            "balance": self.executor.balance if self.executor else settings.risk.paper_starting_balance,
            "total_pnl": self.executor.get_total_pnl() if self.executor else 0,
            "strategy": self.strategy.name if self.strategy else None,
            "symbols": settings.trading.symbols,
            "interval": settings.trading.interval_seconds,
        }

    # ── Main loop ─────────────────────────────────────────────────

    def _run_loop(self):
        global logger
        if logger is None:
            logger = get_logger("bot")

        logger.info("Trading loop started")

        while self._running:
            self._cycle += 1
            self._log_activity(f"⏳ Cycle {self._cycle} starting…")

            for symbol in settings.trading.symbols:
                if not self._running:
                    break
                try:
                    self._process_symbol(symbol)
                except Exception as e:
                    msg = f"❌ Error processing {symbol}: {e}"
                    logger.error(msg, exc_info=True)
                    self._log_activity(msg, "error")

            # Check open positions for SL/TP exits
            if self._running:
                self._check_exits()

            # Periodic learning retrain
            if self._cycle % 20 == 0 and self.learner:
                self.learner.retrain_weights()
                self._log_activity("🧠 Signal weights retrained")

            self._log_activity(
                f"✅ Cycle {self._cycle} complete — "
                f"balance: ${self.executor.balance:,.2f}"
            )

            # Sleep with interruptibility + heartbeat countdown
            interval = settings.trading.interval_seconds
            self._log_activity(f"⏱️ Next cycle in {interval}s…", "dim")
            elapsed = 0
            while elapsed < interval:
                if not self._running:
                    break
                time.sleep(1)
                elapsed += 1
                remaining = interval - elapsed
                if remaining > 0 and remaining % 10 == 0:
                    self._log_activity(f"⏱️ Next cycle in {remaining}s…", "dim")

        logger.info("Trading loop ended")

    def _process_symbol(self, symbol: str):
        """Run the full pipeline for one symbol."""
        # 1. Check risk limits
        can_trade, reason = self.risk.can_trade(self.executor.get_portfolio_value())
        if not can_trade:
            self._log_activity(f"⚠️ {symbol}: blocked — {reason}", "warning")
            return

        # 2. Skip if already have an open position
        open_trades = self.executor.get_open_trades(symbol)
        if open_trades:
            self._log_activity(f"ℹ️ {symbol}: already have {len(open_trades)} open position(s)")
            return

        # 3. Fetch market data
        self._log_activity(f"📡 {symbol}: fetching market data…")
        ohlcv = self.exchange.fetch_ohlcv(symbol, settings.trading.timeframe)
        ticker = self.exchange.fetch_ticker(symbol)
        current_price = ticker["last"]

        # 4. Fetch sentiment (free scraper first, NewsAPI as bonus)
        sentiment_signal: Optional[SentimentSignal] = None
        try:
            from src.data.free_scraper import scrape_all
            from src.data.news import NewsFeed, NewsArticle

            scraped = scrape_all(symbol)
            total_sources = len(scraped.articles) + len(scraped.reddit_posts)

            if total_sources > 0:
                self._log_activity(
                    f"🌐 {symbol}: scraped {len(scraped.articles)} news articles "
                    f"+ {len(scraped.reddit_posts)} Reddit posts (free, no API key)"
                )

                # Convert scraped articles into a NewsFeed for sentiment analysis
                converted_articles = [
                    NewsArticle(
                        title=a.title,
                        source=a.source,
                        description=a.description,
                        url=a.url,
                        published_at=a.published_at,
                    )
                    for a in scraped.articles
                ]
                # Add Reddit post titles as pseudo-articles
                for post in scraped.reddit_posts:
                    converted_articles.append(
                        NewsArticle(
                            title=f"{post.title} [r/{post.subreddit} ↑{post.score}]",
                            source=f"Reddit r/{post.subreddit}",
                            description="",
                            url=post.url,
                            published_at=post.created_at,
                        )
                    )

                combined_feed = NewsFeed(symbol=symbol, articles=converted_articles)
                sentiment_signal = analyse_news(combined_feed)

                if sentiment_signal.sample_size > 0:
                    self._log_activity(
                        f"📰 {symbol}: sentiment={sentiment_signal.direction} "
                        f"(score={sentiment_signal.score:+.3f}, "
                        f"{sentiment_signal.sample_size} sources)"
                    )
                    # Log top headlines
                    for hl in sentiment_signal.top_headlines[:3]:
                        self._log_activity(f"   📄 {hl}", "dim")

        except Exception as e:
            self._log_activity(f"⚠️ {symbol}: sentiment scrape failed: {e}", "warning")

        # 5. Run strategy
        signal_result = self.strategy.evaluate(symbol, ohlcv, sentiment_signal)

        self._log_activity(
            f"🔎 {symbol} @${current_price:,.2f}: "
            f"signal={signal_result.direction} (conf={signal_result.confidence:.2f})"
        )

        # 6. Record signals
        if signal_result.technical and self.learner:
            self.learner.record_signal(
                symbol=symbol, source="technical",
                direction=signal_result.technical.direction,
                confidence=signal_result.technical.confidence,
                details=signal_result.technical.indicators,
            )
        if sentiment_signal and self.learner:
            self.learner.record_signal(
                symbol=symbol, source="sentiment",
                direction=sentiment_signal.direction,
                confidence=sentiment_signal.confidence,
                details=sentiment_signal.details,
            )

        # 7. Execute trade
        if signal_result.direction == "HOLD":
            self._log_activity(f"➡️ {symbol}: HOLD — no action")
            return

        position_size = self.risk.calculate_position_size(
            self.executor.get_portfolio_value(),
            signal_result.confidence,
            current_price,
        )

        if position_size <= 0:
            self._log_activity(f"⚠️ {symbol}: position too small — skipping", "warning")
            return

        atr = (signal_result.technical.indicators.get("atr", 0)
               if signal_result.technical else 0)
        sl = (self.risk.dynamic_stop_loss(current_price, signal_result.direction, atr)
              if atr else self.risk.stop_loss_price(current_price, signal_result.direction))
        tp = self.risk.take_profit_price(current_price, signal_result.direction)

        trade = self.executor.open_trade(
            symbol=symbol,
            side=signal_result.direction,
            amount=position_size,
            price=current_price,
            strategy=self.strategy.name,
            stop_loss=sl,
            take_profit=tp,
            leverage=signal_result.leverage,
        )

        lever_str = f" {signal_result.leverage}x" if signal_result.leverage > 1.0 else ""
        emoji = "🟢" if signal_result.direction == "BUY" else "🔴"
        self._log_activity(
            f"{emoji} OPENED {signal_result.direction}{lever_str} {position_size:.6f} {symbol} "
            f"@${current_price:,.2f} (SL=${sl:,.2f} TP=${tp:,.2f})",
            "trade",
            {
                "action": "open",
                "side": signal_result.direction,
                "symbol": symbol,
                "price": current_price,
                "amount": position_size,
                "stop_loss": sl,
                "take_profit": tp,
            },
        )

    def _check_exits(self):
        """Check all open trades for SL/TP exits."""
        open_trades = self.executor.get_open_trades()
        for trade in open_trades:
            try:
                ticker = self.exchange.fetch_ticker(trade.symbol)
                current_price = ticker["last"]

                should_exit, reason = self.risk.should_exit(trade, current_price)
                if should_exit:
                    closed = self.executor.close_trade(trade, current_price, reason)
                    self.risk.record_trade_result(closed.pnl or 0)
                    if self.learner:
                        self.learner.process_trade_outcome(closed)

                    emoji = "✅" if (closed.pnl or 0) >= 0 else "❌"
                    self._log_activity(
                        f"{emoji} CLOSED {trade.side} {trade.symbol} "
                        f"@${current_price:,.2f} — P&L: ${closed.pnl:+,.2f} "
                        f"({closed.pnl_pct:+.1f}%) [{reason}]",
                        "trade",
                        {
                            "action": "close",
                            "side": trade.side,
                            "symbol": trade.symbol,
                            "price": current_price,
                            "pnl": closed.pnl,
                            "reason": reason,
                        },
                    )
            except Exception as e:
                logger.error(f"Error checking exit for trade #{trade.id}: {e}")


# Convenience accessor
def get_bot() -> BotEngine:
    return BotEngine()


# ── CLI standalone entry point ───────────────────────────────────
def run_bot(
    mode: str = "paper",
    strategy_name: str = "momentum_sentiment",
) -> None:
    """Blocking entry point for CLI usage."""
    global logger
    setup_logging(settings.log_level, settings.log_file)
    logger = get_logger("bot")

    signal.signal(signal.SIGINT, lambda s, f: get_bot().stop())
    signal.signal(signal.SIGTERM, lambda s, f: get_bot().stop())

    logger.info("=" * 60)
    logger.info("🚀 Crypto Trading Bot starting up")
    logger.info(f"   Mode:     {mode}")
    logger.info(f"   Strategy: {strategy_name}")
    logger.info(f"   Symbols:  {', '.join(settings.trading.symbols)}")
    logger.info("=" * 60)

    bot = get_bot()
    bot.start(strategy_name=strategy_name)

    # Block until stopped
    try:
        while bot.is_running:
            time.sleep(1)
    except KeyboardInterrupt:
        bot.stop()

    logger.info("👋 Bot shut down.")
