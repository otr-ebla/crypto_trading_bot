"""
Technical analysis module.

Computes common indicators (RSI, MACD, Bollinger Bands, EMA crossovers, ATR)
on OHLCV data and emits a directional signal with confidence.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

import numpy as np
import pandas as pd
import ta

logger = logging.getLogger(__name__)


@dataclass
class TechnicalSignal:
    """Output of the technical analysis pipeline."""

    direction: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 – 1.0
    indicators: Dict[str, Any] = field(default_factory=dict)


def compute_indicators(df: pd.DataFrame) -> pd.DataFrame:
    """
    Add all technical indicator columns to an OHLCV DataFrame.

    Expects columns: open, high, low, close, volume.
    """
    df = df.copy()

    # ── Trend ──
    df["ema_12"] = ta.trend.EMAIndicator(df["close"], window=12).ema_indicator()
    df["ema_26"] = ta.trend.EMAIndicator(df["close"], window=26).ema_indicator()
    df["ema_50"] = ta.trend.EMAIndicator(df["close"], window=50).ema_indicator()

    macd = ta.trend.MACD(df["close"])
    df["macd"] = macd.macd()
    df["macd_signal"] = macd.macd_signal()
    df["macd_diff"] = macd.macd_diff()

    # ── Momentum ──
    df["rsi"] = ta.momentum.RSIIndicator(df["close"], window=14).rsi()
    df["stoch_k"] = ta.momentum.StochasticOscillator(
        df["high"], df["low"], df["close"]
    ).stoch()
    df["stoch_d"] = ta.momentum.StochasticOscillator(
        df["high"], df["low"], df["close"]
    ).stoch_signal()

    # ── Volatility ──
    bb = ta.volatility.BollingerBands(df["close"])
    df["bb_upper"] = bb.bollinger_hband()
    df["bb_middle"] = bb.bollinger_mavg()
    df["bb_lower"] = bb.bollinger_lband()
    df["bb_width"] = bb.bollinger_wband()

    df["atr"] = ta.volatility.AverageTrueRange(
        df["high"], df["low"], df["close"]
    ).average_true_range()

    # ── Volume ──
    df["volume_sma20"] = df["volume"].rolling(window=20).mean()
    df["volume_ratio"] = df["volume"] / df["volume_sma20"]

    return df


def _score_rsi(rsi: float) -> float:
    """Score RSI: <30 → bullish (+1), >70 → bearish (-1), else neutral."""
    if rsi < 25:
        return 1.0
    if rsi < 30:
        return 0.6
    if rsi > 75:
        return -1.0
    if rsi > 70:
        return -0.6
    return 0.0


def _score_macd(macd_diff: float, prev_macd_diff: float) -> float:
    """Score MACD histogram crossover."""
    if macd_diff > 0 and prev_macd_diff <= 0:
        return 0.8  # bullish crossover
    if macd_diff < 0 and prev_macd_diff >= 0:
        return -0.8  # bearish crossover
    if macd_diff > 0:
        return 0.3
    if macd_diff < 0:
        return -0.3
    return 0.0


def _score_ema(close: float, ema_12: float, ema_26: float) -> float:
    """Score EMA alignment."""
    if ema_12 > ema_26 and close > ema_12:
        return 0.7  # strong uptrend
    if ema_12 < ema_26 and close < ema_12:
        return -0.7  # strong downtrend
    if ema_12 > ema_26:
        return 0.3
    if ema_12 < ema_26:
        return -0.3
    return 0.0


def _score_bollinger(close: float, bb_upper: float, bb_lower: float) -> float:
    """Score Bollinger Band position."""
    bb_range = bb_upper - bb_lower
    if bb_range == 0:
        return 0.0
    position = (close - bb_lower) / bb_range  # 0 = at lower, 1 = at upper

    if position < 0.1:
        return 0.6  # near lower band → potential bounce
    if position > 0.9:
        return -0.6  # near upper band → potential reversal
    return 0.0


def _score_volume(volume_ratio: float) -> float:
    """Score volume breakout/dryup."""
    if volume_ratio > 2.0:
        return 0.4  # volume spike (amplifies other signals)
    if volume_ratio < 0.5:
        return -0.1  # low volume (reduces conviction)
    return 0.0


def analyse(df: pd.DataFrame) -> TechnicalSignal:
    """
    Run the full technical analysis pipeline on an OHLCV DataFrame.

    Returns a TechnicalSignal with direction and confidence.
    """
    if len(df) < 50:
        logger.warning("Not enough data for full technical analysis")
        return TechnicalSignal(direction="HOLD", confidence=0.0)

    df = compute_indicators(df)
    latest = df.iloc[-1]
    prev = df.iloc[-2]

    # Score each component
    rsi_score = _score_rsi(latest["rsi"])
    macd_score = _score_macd(latest["macd_diff"], prev["macd_diff"])
    ema_score = _score_ema(latest["close"], latest["ema_12"], latest["ema_26"])
    bb_score = _score_bollinger(latest["close"], latest["bb_upper"], latest["bb_lower"])
    vol_score = _score_volume(latest.get("volume_ratio", 1.0))

    # Weighted composite
    weights = {
        "rsi": 0.20,
        "macd": 0.25,
        "ema": 0.25,
        "bollinger": 0.15,
        "volume": 0.15,
    }
    scores = {
        "rsi": rsi_score,
        "macd": macd_score,
        "ema": ema_score,
        "bollinger": bb_score,
        "volume": vol_score,
    }

    composite = sum(scores[k] * weights[k] for k in weights)

    # Determine direction
    if composite > 0.25:
        direction = "BUY"
    elif composite < -0.25:
        direction = "SELL"
    else:
        direction = "HOLD"

    confidence = min(abs(composite), 1.0)

    indicators = {
        "rsi": round(float(latest["rsi"]), 2),
        "macd_diff": round(float(latest["macd_diff"]), 6),
        "ema_12": round(float(latest["ema_12"]), 2),
        "ema_26": round(float(latest["ema_26"]), 2),
        "bb_upper": round(float(latest["bb_upper"]), 2),
        "bb_lower": round(float(latest["bb_lower"]), 2),
        "atr": round(float(latest["atr"]), 4),
        "volume_ratio": round(float(latest.get("volume_ratio", 1.0)), 2),
        "scores": {k: round(v, 3) for k, v in scores.items()},
        "composite": round(composite, 4),
    }

    logger.info(
        f"Technical signal: {direction} (conf={confidence:.2f}, "
        f"RSI={indicators['rsi']}, MACD_diff={indicators['macd_diff']})"
    )

    return TechnicalSignal(
        direction=direction, confidence=confidence, indicators=indicators
    )
