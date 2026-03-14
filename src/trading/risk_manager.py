"""
Risk management module.

Enforces position sizing, stop-loss, take-profit, drawdown limits,
and cooldown after consecutive losses.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from src.config import settings
from src.database import get_db
from src.models import Trade

logger = logging.getLogger(__name__)


class RiskManager:
    """Stateful risk manager that validates trades against risk rules."""

    def __init__(self) -> None:
        self.cfg = settings.risk
        self._consecutive_losses = 0
        self._cooldown_until: Optional[datetime] = None
        self._daily_pnl = 0.0
        self._daily_reset: Optional[datetime] = None

    # ── Position sizing ──────────────────────────────────────────────

    def max_position_size(self, portfolio_value: float) -> float:
        """Max amount to allocate to a single position."""
        return portfolio_value * (self.cfg.max_position_pct / 100.0)

    def calculate_position_size(
        self,
        portfolio_value: float,
        confidence: float,
        current_price: float,
    ) -> float:
        """
        Calculate position size factoring in confidence and risk limits.

        Higher confidence → larger position (up to the max).
        """
        max_size = self.max_position_size(portfolio_value)
        # Scale between 30% and 100% of max based on confidence
        scale = 0.3 + 0.7 * min(confidence, 1.0)
        dollar_amount = max_size * scale
        units = dollar_amount / current_price
        return round(units, 8)

    # ── Stop-loss / Take-profit ──────────────────────────────────────

    def stop_loss_price(self, entry_price: float, side: str) -> float:
        """Calculate stop-loss price."""
        pct = self.cfg.stop_loss_pct / 100.0
        if side.upper() == "BUY":
            return round(entry_price * (1 - pct), 8)
        return round(entry_price * (1 + pct), 8)

    def take_profit_price(self, entry_price: float, side: str) -> float:
        """Calculate take-profit price."""
        pct = self.cfg.take_profit_pct / 100.0
        if side.upper() == "BUY":
            return round(entry_price * (1 + pct), 8)
        return round(entry_price * (1 - pct), 8)

    def dynamic_stop_loss(
        self, entry_price: float, side: str, atr: float, multiplier: float = 2.0
    ) -> float:
        """ATR-based dynamic stop-loss."""
        distance = atr * multiplier
        if side.upper() == "BUY":
            return round(entry_price - distance, 8)
        return round(entry_price + distance, 8)

    # ── Trade validation ─────────────────────────────────────────────

    def can_trade(self, portfolio_value: float) -> tuple[bool, str]:
        """Check whether a new trade is allowed under current risk rules."""
        now = datetime.now(timezone.utc)

        # Reset daily P&L tracking
        if self._daily_reset is None or now.date() > self._daily_reset.date():
            self._daily_pnl = 0.0
            self._daily_reset = now

        # 1. Cooldown check
        if self._cooldown_until and now < self._cooldown_until:
            remaining = (self._cooldown_until - now).seconds // 60
            msg = f"In cooldown — {remaining} min remaining after {self.cfg.max_consecutive_losses} consecutive losses"
            logger.warning(msg)
            return False, msg

        # 2. Daily drawdown check
        max_dd = portfolio_value * (self.cfg.max_daily_drawdown_pct / 100.0)
        if self._daily_pnl < -max_dd:
            msg = (
                f"Daily drawdown limit reached: "
                f"${self._daily_pnl:.2f} < -${max_dd:.2f}"
            )
            logger.warning(msg)
            return False, msg

        return True, "OK"

    def record_trade_result(self, pnl: float) -> None:
        """Update risk state after a trade closes."""
        self._daily_pnl += pnl

        if pnl < 0:
            self._consecutive_losses += 1
            if self._consecutive_losses >= self.cfg.max_consecutive_losses:
                self._cooldown_until = datetime.now(timezone.utc) + timedelta(
                    minutes=self.cfg.cooldown_minutes
                )
                logger.warning(
                    f"Entering cooldown for {self.cfg.cooldown_minutes} min "
                    f"after {self._consecutive_losses} consecutive losses"
                )
        else:
            self._consecutive_losses = 0
            self._cooldown_until = None

    # ── Check open positions for exit ────────────────────────────────

    def should_exit(
        self, trade: Trade, current_price: float
    ) -> tuple[bool, str]:
        """Check if an open trade should be closed (SL/TP hit)."""
        if trade.side.upper() == "BUY":
            if trade.stop_loss and current_price <= trade.stop_loss:
                return True, "stop_loss"
            if trade.take_profit and current_price >= trade.take_profit:
                return True, "take_profit"
        else:  # SELL
            if trade.stop_loss and current_price >= trade.stop_loss:
                return True, "stop_loss"
            if trade.take_profit and current_price <= trade.take_profit:
                return True, "take_profit"

        return False, ""
