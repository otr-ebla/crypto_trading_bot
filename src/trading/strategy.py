"""
Trading strategies.

BaseStrategy ABC with built-in concrete strategies that combine
technical and sentiment signals.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional

import pandas as pd

from src.analysis.signal_aggregator import TradeSignal, aggregate
from src.analysis.technical import TechnicalSignal, analyse as ta_analyse
from src.analysis.sentiment import SentimentSignal

logger = logging.getLogger(__name__)


class BaseStrategy(ABC):
    """Abstract base class for all trading strategies."""

    name: str = "base"

    @abstractmethod
    def evaluate(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        sentiment: Optional[SentimentSignal] = None,
    ) -> TradeSignal:
        """Evaluate data and return a composite trade signal."""
        ...


class MomentumSentimentStrategy(BaseStrategy):
    """
    Combines RSI/MACD momentum with sentiment signals.

    - Enters long when momentum is bullish AND sentiment is positive.
    - Enters short when momentum is bearish AND sentiment is negative.
    - Stays flat when signals conflict (risk reduction).
    """

    name = "momentum_sentiment"

    def __init__(self, min_confidence: float = 0.3) -> None:
        self.min_confidence = min_confidence

    def evaluate(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        sentiment: Optional[SentimentSignal] = None,
    ) -> TradeSignal:
        tech = ta_analyse(ohlcv)
        signal = aggregate(symbol, technical=tech, sentiment=sentiment)

        # Extra filter: require minimum confidence
        if signal.confidence < self.min_confidence:
            signal.direction = "HOLD"
            signal.details["filtered"] = (
                f"confidence {signal.confidence:.2f} < {self.min_confidence}"
            )
            logger.info(
                f"[{self.name}] {symbol}: HOLD (low confidence {signal.confidence:.2f})"
            )

        return signal


class BreakoutNewsStrategy(BaseStrategy):
    """
    Detects price breakouts confirmed by positive news sentiment spikes.

    - Looks for Bollinger Band breakouts with above-average volume.
    - Requires supporting positive news sentiment for confirmation.
    """

    name = "breakout_news"

    def __init__(
        self,
        volume_threshold: float = 1.5,
        sentiment_threshold: float = 0.2,
    ) -> None:
        self.volume_threshold = volume_threshold
        self.sentiment_threshold = sentiment_threshold

    def evaluate(
        self,
        symbol: str,
        ohlcv: pd.DataFrame,
        sentiment: Optional[SentimentSignal] = None,
    ) -> TradeSignal:
        tech = ta_analyse(ohlcv)

        # Check for breakout conditions
        indicators = tech.indicators
        close = ohlcv["close"].iloc[-1]
        bb_upper = indicators.get("bb_upper", 0)
        bb_lower = indicators.get("bb_lower", 0)
        volume_ratio = indicators.get("volume_ratio", 1.0)

        breakout_up = close > bb_upper and volume_ratio > self.volume_threshold
        breakout_down = close < bb_lower and volume_ratio > self.volume_threshold

        if breakout_up and sentiment and sentiment.score > self.sentiment_threshold:
            direction = "BUY"
            confidence = min(
                tech.confidence * 0.6 + sentiment.confidence * 0.4, 1.0
            )
            reason = "Bullish breakout + positive news"
        elif breakout_down and sentiment and sentiment.score < -self.sentiment_threshold:
            direction = "SELL"
            confidence = min(
                tech.confidence * 0.6 + sentiment.confidence * 0.4, 1.0
            )
            reason = "Bearish breakdown + negative news"
        else:
            direction = "HOLD"
            confidence = 0.0
            reason = "No breakout confirmation"

        signal = TradeSignal(
            symbol=symbol,
            direction=direction,
            confidence=round(confidence, 4),
            technical=tech,
            sentiment=sentiment,
            details={
                "strategy": self.name,
                "breakout_up": breakout_up,
                "breakout_down": breakout_down,
                "volume_ratio": volume_ratio,
                "reason": reason,
            },
        )

        logger.info(f"[{self.name}] {symbol}: {direction} — {reason}")
        return signal


# Strategy registry
STRATEGIES = {
    "momentum_sentiment": MomentumSentimentStrategy,
    "breakout_news": BreakoutNewsStrategy,
}


def get_strategy(name: str = "momentum_sentiment", **kwargs) -> BaseStrategy:
    """Instantiate a strategy by name."""
    cls = STRATEGIES.get(name)
    if cls is None:
        raise ValueError(f"Unknown strategy: {name}. Available: {list(STRATEGIES)}")
    return cls(**kwargs)
