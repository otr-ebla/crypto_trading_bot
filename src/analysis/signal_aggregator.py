"""
Signal aggregator.

Combines technical and sentiment signals into a composite TradeSignal
using learnable weights persisted in the database.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from src.analysis.technical import TechnicalSignal
from src.analysis.sentiment import SentimentSignal
from src.database import get_db
from src.models import SignalWeight

logger = logging.getLogger(__name__)

# Default weights (used if no learned weights exist yet)
DEFAULT_WEIGHTS: Dict[str, float] = {
    "technical": 0.55,
    "sentiment": 0.45,
}


@dataclass
class TradeSignal:
    """Final composite signal used by the trading engine."""

    symbol: str
    direction: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 – 1.0
    leverage: float = 1.0  # 1.0x, 2.0x, 3.0x
    technical: Optional[TechnicalSignal] = None
    sentiment: Optional[SentimentSignal] = None
    weights: Dict[str, float] = field(default_factory=dict)
    details: Dict[str, Any] = field(default_factory=dict)


def _load_weights() -> Dict[str, float]:
    """Load signal weights from the database, falling back to defaults."""
    weights = dict(DEFAULT_WEIGHTS)
    try:
        with get_db() as db:
            for row in db.query(SignalWeight).all():
                if row.source in weights:
                    weights[row.source] = row.weight
    except Exception as e:
        logger.warning(f"Could not load signal weights: {e}")
    return weights


def _direction_score(direction: str) -> float:
    """Convert direction string to numeric score."""
    return {"BUY": 1.0, "SELL": -1.0, "HOLD": 0.0}.get(direction, 0.0)


def aggregate(
    symbol: str,
    technical: Optional[TechnicalSignal] = None,
    sentiment: Optional[SentimentSignal] = None,
) -> TradeSignal:
    """
    Combine technical and sentiment signals into a single TradeSignal.

    The weights are loaded from the database (learned over time) or
    fall back to sensible defaults.
    """
    weights = _load_weights()

    # Normalise weights to available sources
    available: Dict[str, float] = {}
    scores: Dict[str, float] = {}

    if technical and technical.direction != "HOLD":
        available["technical"] = weights.get("technical", 0.55)
        scores["technical"] = _direction_score(technical.direction) * technical.confidence
    elif technical:
        available["technical"] = weights.get("technical", 0.55)
        scores["technical"] = 0.0

    if sentiment and sentiment.direction != "HOLD" and sentiment.sample_size > 0:
        available["sentiment"] = weights.get("sentiment", 0.45)
        scores["sentiment"] = _direction_score(sentiment.direction) * sentiment.confidence
    elif sentiment and sentiment.sample_size > 0:
        available["sentiment"] = weights.get("sentiment", 0.45)
        scores["sentiment"] = 0.0

    if not available:
        return TradeSignal(
            symbol=symbol,
            direction="HOLD",
            confidence=0.0,
            leverage=1.0,
            technical=technical,
            sentiment=sentiment,
            weights=weights,
            details={"reason": "no signal sources available"},
        )

    # Normalise weights
    total_weight = sum(available.values())
    norm_weights = {k: v / total_weight for k, v in available.items()}

    # Weighted composite score (-1 to +1)
    composite = sum(scores[k] * norm_weights[k] for k in scores)

    # Direction
    if composite > 0.15:
        direction = "BUY"
    elif composite < -0.15:
        direction = "SELL"
    else:
        direction = "HOLD"

    confidence = min(abs(composite), 1.0)

    # Dynamic Leverage based on confidence
    leverage = 1.0
    if direction != "HOLD":
        if confidence >= 0.80:
            leverage = 3.0
        elif confidence >= 0.60:
            leverage = 2.0

    signal = TradeSignal(
        symbol=symbol,
        direction=direction,
        confidence=round(confidence, 4),
        leverage=leverage,
        technical=technical,
        sentiment=sentiment,
        weights=norm_weights,
        details={
            "composite_score": round(composite, 4),
            "component_scores": {k: round(v, 4) for k, v in scores.items()},
            "normalised_weights": {k: round(v, 4) for k, v in norm_weights.items()},
        },
    )

    logger.info(
        f"[bold]{symbol}[/] composite signal: [bold]{direction}[/] "
        f"{leverage}x (conf={confidence:.2f}, score={composite:.4f})"
    )

    return signal
