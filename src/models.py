"""
SQLAlchemy ORM models for trades, signals, sentiment snapshots, and performance.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    Column,
    DateTime,
    Enum,
    Float,
    Integer,
    String,
    Text,
    Boolean,
    JSON,
)
from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


class Trade(Base):
    """Record of every executed (paper or live) trade."""

    __tablename__ = "trades"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    side = Column(String(4), nullable=False)  # BUY / SELL
    entry_price = Column(Float, nullable=False)
    exit_price = Column(Float, nullable=True)
    amount = Column(Float, nullable=False)
    pnl = Column(Float, nullable=True)
    pnl_pct = Column(Float, nullable=True)
    strategy = Column(String(50), nullable=False)
    status = Column(String(10), nullable=False, default="open")  # open / closed
    entry_time = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    exit_time = Column(DateTime, nullable=True)
    stop_loss = Column(Float, nullable=True)
    take_profit = Column(Float, nullable=True)
    notes = Column(Text, nullable=True)
    mode = Column(String(5), nullable=False, default="paper")  # paper / live

    def __repr__(self) -> str:
        return (
            f"<Trade {self.id} {self.side} {self.symbol} "
            f"@{self.entry_price} → {self.exit_price} P&L={self.pnl}>"
        )


class Signal(Base):
    """An individual signal emitted by the analysis engine."""

    __tablename__ = "signals"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    source = Column(String(30), nullable=False)  # technical / sentiment / composite
    direction = Column(String(4), nullable=False)  # BUY / SELL / HOLD
    confidence = Column(Float, nullable=False)  # 0.0 – 1.0
    details = Column(JSON, nullable=True)
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    outcome_pnl = Column(Float, nullable=True)  # filled in by learner
    was_correct = Column(Boolean, nullable=True)

    def __repr__(self) -> str:
        return (
            f"<Signal {self.source} {self.direction} "
            f"{self.symbol} conf={self.confidence:.2f}>"
        )


class SentimentSnapshot(Base):
    """Point-in-time sentiment reading for a coin."""

    __tablename__ = "sentiment_snapshots"

    id = Column(Integer, primary_key=True, autoincrement=True)
    symbol = Column(String(20), nullable=False, index=True)
    source = Column(String(20), nullable=False)  # news / reddit / twitter
    score = Column(Float, nullable=False)  # -1.0 (bearish) to +1.0 (bullish)
    sample_size = Column(Integer, nullable=False, default=0)
    headlines = Column(JSON, nullable=True)  # list of top headlines
    created_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        return f"<Sentiment {self.symbol} {self.source} score={self.score:.2f}>"


class SignalWeight(Base):
    """Learnable weights for each signal source, adjusted by the feedback loop."""

    __tablename__ = "signal_weights"

    id = Column(Integer, primary_key=True, autoincrement=True)
    source = Column(String(30), nullable=False, unique=True)
    weight = Column(Float, nullable=False, default=1.0)
    total_signals = Column(Integer, nullable=False, default=0)
    correct_signals = Column(Integer, nullable=False, default=0)
    updated_at = Column(
        DateTime, nullable=False, default=lambda: datetime.now(timezone.utc)
    )

    def __repr__(self) -> str:
        accuracy = (
            (self.correct_signals / self.total_signals * 100)
            if self.total_signals
            else 0
        )
        return f"<Weight {self.source} w={self.weight:.3f} acc={accuracy:.1f}%>"
