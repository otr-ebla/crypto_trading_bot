"""
Learning feedback loop.

After each trade closes, the learner:
1. Tags the contributing signals with the trade outcome.
2. Updates signal-source weights (increase for profitable, decrease for unprofitable).
3. Periodically retrains weights using logistic regression.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import numpy as np
from sqlalchemy import desc

from src.database import get_db
from src.models import Signal, SignalWeight, Trade

logger = logging.getLogger(__name__)

# Learning rate for incremental weight updates
LEARNING_RATE = 0.05
MIN_WEIGHT = 0.1
MAX_WEIGHT = 3.0


class Learner:
    """Adjusts signal weights based on trade outcomes."""

    def __init__(self) -> None:
        self._ensure_weights_exist()

    def _ensure_weights_exist(self) -> None:
        """Create default weight rows if they don't exist."""
        defaults = {"technical": 1.0, "sentiment": 1.0}
        with get_db() as db:
            for source, default_w in defaults.items():
                existing = (
                    db.query(SignalWeight)
                    .filter(SignalWeight.source == source)
                    .first()
                )
                if not existing:
                    db.add(
                        SignalWeight(source=source, weight=default_w)
                    )

    # ── Record a signal ─────────────────────────────────────────────

    def record_signal(
        self,
        symbol: str,
        source: str,
        direction: str,
        confidence: float,
        details: Optional[Dict] = None,
    ) -> int:
        """Persist a signal to the database. Returns the signal ID."""
        signal = Signal(
            symbol=symbol,
            source=source,
            direction=direction,
            confidence=confidence,
            details=details or {},
        )
        with get_db() as db:
            db.add(signal)
            db.flush()
            return signal.id

    # ── Process a closed trade ──────────────────────────────────────

    def process_trade_outcome(self, trade: Trade) -> None:
        """
        After a trade closes, update signal accuracy and adjust weights.
        """
        if trade.pnl is None:
            return

        was_profitable = trade.pnl > 0

        # Find signals that preceded this trade (within 5 minutes before entry)
        with get_db() as db:
            recent_signals = (
                db.query(Signal)
                .filter(
                    Signal.symbol == trade.symbol,
                    Signal.created_at <= trade.entry_time,
                )
                .order_by(desc(Signal.created_at))
                .limit(10)
                .all()
            )

            for signal in recent_signals:
                signal_agreed_with_trade = (
                    (signal.direction == "BUY" and trade.side == "BUY")
                    or (signal.direction == "SELL" and trade.side == "SELL")
                )

                if signal_agreed_with_trade:
                    signal.was_correct = was_profitable
                    signal.outcome_pnl = trade.pnl

                # Update the weight for this signal source
                weight_row = (
                    db.query(SignalWeight)
                    .filter(SignalWeight.source == signal.source)
                    .first()
                )
                if weight_row:
                    weight_row.total_signals += 1
                    if signal_agreed_with_trade and was_profitable:
                        weight_row.correct_signals += 1
                        # Increase weight
                        delta = LEARNING_RATE * signal.confidence
                        weight_row.weight = min(
                            weight_row.weight + delta, MAX_WEIGHT
                        )
                    elif signal_agreed_with_trade and not was_profitable:
                        # Decrease weight
                        delta = LEARNING_RATE * signal.confidence
                        weight_row.weight = max(
                            weight_row.weight - delta, MIN_WEIGHT
                        )
                    weight_row.updated_at = datetime.now(timezone.utc)

        logger.info(
            f"📊 Learner processed trade #{trade.id}: "
            f"{'✅ profitable' if was_profitable else '❌ loss'} "
            f"(P&L: ${trade.pnl:+.2f}), updated {len(recent_signals)} signal weights"
        )

    # ── Full retraining pass ────────────────────────────────────────

    def retrain_weights(self, lookback_trades: int = 100) -> Dict[str, float]:
        """
        Retrain signal weights using recent trade history.

        Uses a simple accuracy-based approach:
        weight = accuracy_ratio * base_weight

        For enough data, could be extended to logistic regression.
        """
        with get_db() as db:
            # Get recent closed trades
            recent_trades = (
                db.query(Trade)
                .filter(Trade.status == "closed", Trade.pnl.isnot(None))
                .order_by(desc(Trade.exit_time))
                .limit(lookback_trades)
                .all()
            )

            if len(recent_trades) < 10:
                logger.info(
                    f"Not enough trades for retraining ({len(recent_trades)}/10)"
                )
                return self._get_current_weights()

            # Calculate accuracy per source
            source_stats: Dict[str, Dict[str, int]] = {}

            for signal_weight in db.query(SignalWeight).all():
                src = signal_weight.source
                if signal_weight.total_signals > 0:
                    accuracy = (
                        signal_weight.correct_signals / signal_weight.total_signals
                    )
                    # Scale weight: 0.5 accuracy → 1.0 weight, 1.0 accuracy → 2.0
                    new_weight = max(accuracy * 2.0, MIN_WEIGHT)
                    new_weight = min(new_weight, MAX_WEIGHT)

                    old_weight = signal_weight.weight
                    # Smooth update (70% new, 30% old)
                    signal_weight.weight = round(
                        0.7 * new_weight + 0.3 * old_weight, 4
                    )
                    signal_weight.updated_at = datetime.now(timezone.utc)
                    logger.info(
                        f"Retrained {src}: {old_weight:.3f} → {signal_weight.weight:.3f} "
                        f"(accuracy={accuracy:.1%})"
                    )

        weights = self._get_current_weights()
        logger.info(f"📊 Retraining complete. Updated weights: {weights}")
        return weights

    def _get_current_weights(self) -> Dict[str, float]:
        """Return current weights from the database."""
        weights = {}
        with get_db() as db:
            for row in db.query(SignalWeight).all():
                weights[row.source] = row.weight
        return weights

    def get_performance_summary(self) -> Dict:
        """Return a summary of the learning state."""
        with get_db() as db:
            weights = {}
            for row in db.query(SignalWeight).all():
                accuracy = (
                    (row.correct_signals / row.total_signals * 100)
                    if row.total_signals > 0
                    else 0.0
                )
                weights[row.source] = {
                    "weight": row.weight,
                    "total_signals": row.total_signals,
                    "correct_signals": row.correct_signals,
                    "accuracy_pct": round(accuracy, 1),
                }

            total_trades = db.query(Trade).filter(Trade.status == "closed").count()
            winning = (
                db.query(Trade)
                .filter(Trade.status == "closed", Trade.pnl > 0)
                .count()
            )
            total_pnl_rows = (
                db.query(Trade.pnl)
                .filter(Trade.status == "closed", Trade.pnl.isnot(None))
                .all()
            )
            total_pnl = sum(row[0] for row in total_pnl_rows) if total_pnl_rows else 0

        return {
            "signal_weights": weights,
            "total_trades": total_trades,
            "winning_trades": winning,
            "win_rate_pct": round(winning / total_trades * 100, 1) if total_trades else 0,
            "total_pnl": round(total_pnl, 2),
        }
