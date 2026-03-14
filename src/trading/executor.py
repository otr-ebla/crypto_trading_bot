"""
Order executor.

PaperExecutor — simulates trades using last price, tracks virtual portfolio.
LiveExecutor  — places real orders via the exchange client.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.config import settings
from src.database import get_db
from src.models import Trade
from src.data.exchange import ExchangeClient

logger = logging.getLogger(__name__)


class BaseExecutor(ABC):
    """Abstract order executor."""

    @abstractmethod
    def open_trade(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        strategy: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Trade:
        ...

    @abstractmethod
    def close_trade(self, trade: Trade, price: float, reason: str = "") -> Trade:
        ...

    @abstractmethod
    def get_open_trades(self, symbol: Optional[str] = None) -> List[Trade]:
        ...

    @abstractmethod
    def get_portfolio_value(self) -> float:
        ...


class PaperExecutor(BaseExecutor):
    """Paper trading executor — no real money at risk."""

    def __init__(self, starting_balance: Optional[float] = None) -> None:
        self.balance = starting_balance or settings.risk.paper_starting_balance
        self._initial_balance = self.balance
        logger.info(
            f"Paper executor initialised with ${self.balance:,.2f}"
        )

    def open_trade(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        strategy: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Trade:
        cost = amount * price
        if cost > self.balance:
            logger.warning(
                f"Insufficient balance: need ${cost:.2f}, have ${self.balance:.2f}"
            )
            amount = self.balance / price * 0.99  # use 99% of balance
            cost = amount * price

        self.balance -= cost

        trade = Trade(
            symbol=symbol,
            side=side.upper(),
            entry_price=price,
            amount=amount,
            strategy=strategy,
            status="open",
            stop_loss=stop_loss,
            take_profit=take_profit,
            mode="paper",
        )

        with get_db() as db:
            db.add(trade)
            db.flush()
            trade_id = trade.id

        logger.info(
            f"📝 [Paper] Opened {side.upper()} {amount:.6f} {symbol} "
            f"@{price:.2f} (SL={stop_loss}, TP={take_profit}) "
            f"Balance: ${self.balance:,.2f}"
        )
        return trade

    def close_trade(self, trade: Trade, price: float, reason: str = "") -> Trade:
        if trade.side.upper() == "BUY":
            pnl = (price - trade.entry_price) * trade.amount
        else:
            pnl = (trade.entry_price - price) * trade.amount

        pnl_pct = (pnl / (trade.entry_price * trade.amount)) * 100 if trade.amount else 0

        self.balance += trade.amount * price  # return the position value

        with get_db() as db:
            db_trade = db.get(Trade, trade.id)
            if db_trade:
                db_trade.exit_price = price
                db_trade.pnl = round(pnl, 4)
                db_trade.pnl_pct = round(pnl_pct, 2)
                db_trade.status = "closed"
                db_trade.exit_time = datetime.now(timezone.utc)
                db_trade.notes = reason
                trade = db_trade

        emoji = "✅" if pnl >= 0 else "❌"
        logger.info(
            f"{emoji} [Paper] Closed {trade.side} {trade.symbol} "
            f"@{price:.2f} P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%) "
            f"Reason: {reason} | Balance: ${self.balance:,.2f}"
        )
        return trade

    def get_open_trades(self, symbol: Optional[str] = None) -> List[Trade]:
        with get_db() as db:
            q = db.query(Trade).filter(Trade.status == "open", Trade.mode == "paper")
            if symbol:
                q = q.filter(Trade.symbol == symbol)
            return q.all()

    def get_portfolio_value(self) -> float:
        return self.balance

    def get_total_pnl(self) -> float:
        return self.balance - self._initial_balance


class LiveExecutor(BaseExecutor):
    """Live trading executor — places real orders on the exchange."""

    def __init__(self, exchange: Optional[ExchangeClient] = None) -> None:
        self.exchange = exchange or ExchangeClient()
        logger.info("Live executor initialised")

    def open_trade(
        self,
        symbol: str,
        side: str,
        amount: float,
        price: float,
        strategy: str,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None,
    ) -> Trade:
        # Place market order
        order = self.exchange.place_order(symbol, side.lower(), amount)

        fill_price = order.get("average", order.get("price", price))
        fill_amount = order.get("filled", amount)

        trade = Trade(
            symbol=symbol,
            side=side.upper(),
            entry_price=fill_price,
            amount=fill_amount,
            strategy=strategy,
            status="open",
            stop_loss=stop_loss,
            take_profit=take_profit,
            mode="live",
        )

        with get_db() as db:
            db.add(trade)

        logger.info(
            f"🔴 [Live] Opened {side.upper()} {fill_amount:.6f} {symbol} "
            f"@{fill_price:.2f}"
        )
        return trade

    def close_trade(self, trade: Trade, price: float, reason: str = "") -> Trade:
        # Close by placing opposite order
        close_side = "sell" if trade.side.upper() == "BUY" else "buy"
        order = self.exchange.place_order(trade.symbol, close_side, trade.amount)

        fill_price = order.get("average", order.get("price", price))

        if trade.side.upper() == "BUY":
            pnl = (fill_price - trade.entry_price) * trade.amount
        else:
            pnl = (trade.entry_price - fill_price) * trade.amount

        pnl_pct = (pnl / (trade.entry_price * trade.amount)) * 100 if trade.amount else 0

        with get_db() as db:
            db_trade = db.get(Trade, trade.id)
            if db_trade:
                db_trade.exit_price = fill_price
                db_trade.pnl = round(pnl, 4)
                db_trade.pnl_pct = round(pnl_pct, 2)
                db_trade.status = "closed"
                db_trade.exit_time = datetime.now(timezone.utc)
                db_trade.notes = reason

        logger.info(
            f"🔴 [Live] Closed {trade.side} {trade.symbol} "
            f"@{fill_price:.2f} P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)"
        )
        return trade

    def get_open_trades(self, symbol: Optional[str] = None) -> List[Trade]:
        with get_db() as db:
            q = db.query(Trade).filter(Trade.status == "open", Trade.mode == "live")
            if symbol:
                q = q.filter(Trade.symbol == symbol)
            return q.all()

    def get_portfolio_value(self) -> float:
        balance = self.exchange.fetch_balance()
        usdt = balance.get("USDT", 0)
        return float(usdt)
