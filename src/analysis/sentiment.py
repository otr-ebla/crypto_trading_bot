"""
Sentiment analysis module.

Scores news headlines and social posts using VADER, then aggregates into
a per-symbol sentiment signal.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from src.data.news import NewsFeed
from src.data.social import SocialFeed

logger = logging.getLogger(__name__)

_vader = SentimentIntensityAnalyzer()

# Crypto-specific lexicon boosts
_CRYPTO_LEXICON = {
    "bullish": 2.5,
    "bearish": -2.5,
    "moon": 2.0,
    "mooning": 2.5,
    "dump": -2.0,
    "dumping": -2.5,
    "pump": 1.8,
    "pumping": 2.0,
    "rekt": -3.0,
    "rug": -3.5,
    "rugpull": -3.5,
    "hodl": 1.5,
    "fomo": 1.0,
    "fud": -1.5,
    "ath": 2.0,
    "breakout": 1.8,
    "crash": -2.5,
    "scam": -3.0,
    "whale": 0.5,
    "accumulation": 1.2,
    "distribution": -1.0,
    "halving": 1.5,
    "adoption": 2.0,
    "regulation": -0.8,
    "ban": -2.5,
    "approval": 2.0,
    "etf": 1.5,
    "hack": -3.0,
    "exploit": -2.5,
    "rally": 2.0,
    "correction": -1.0,
    "dip": -0.5,
    "buy the dip": 1.5,
}

# Update VADER's lexicon with crypto terms
_vader.lexicon.update(_CRYPTO_LEXICON)


@dataclass
class SentimentSignal:
    """Aggregated sentiment for a single symbol."""

    direction: str  # "BUY" | "SELL" | "HOLD"
    confidence: float  # 0.0 – 1.0
    score: float  # raw compound score (-1 to +1)
    sample_size: int
    source: str  # "news" | "social" | "combined"
    top_headlines: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


# ── Text cleaning ────────────────────────────────────────────────────

_URL_RE = re.compile(r"https?://\S+")
_MENTION_RE = re.compile(r"@\w+")
_HASHTAG_RE = re.compile(r"#")
_SPECIAL_RE = re.compile(r"[^\w\s.,!?'-]")


def clean_text(text: str) -> str:
    """Remove URLs, mentions, hashtags markers, and special chars."""
    text = _URL_RE.sub("", text)
    text = _MENTION_RE.sub("", text)
    text = _HASHTAG_RE.sub("", text)
    text = _SPECIAL_RE.sub(" ", text)
    return " ".join(text.split())  # collapse whitespace


# ── Scoring ──────────────────────────────────────────────────────────

def score_text(text: str) -> float:
    """Return VADER compound score for a piece of text (-1 to +1)."""
    cleaned = clean_text(text)
    if not cleaned.strip():
        return 0.0
    return _vader.polarity_scores(cleaned)["compound"]


def score_texts(texts: List[str]) -> List[float]:
    """Score a batch of texts."""
    return [score_text(t) for t in texts]


# ── Aggregation ──────────────────────────────────────────────────────

def analyse_news(feed: NewsFeed) -> SentimentSignal:
    """Analyse a NewsFeed and return a SentimentSignal."""
    if not feed.articles:
        return SentimentSignal(
            direction="HOLD", confidence=0.0, score=0.0, sample_size=0, source="news"
        )

    texts = [
        f"{a.title}. {a.description or ''}"
        for a in feed.articles
    ]
    scores = score_texts(texts)

    avg_score = sum(scores) / len(scores) if scores else 0.0

    # Weight recent articles higher
    weighted_scores = []
    for i, s in enumerate(scores):
        recency_weight = 1.0 + (len(scores) - i) / len(scores) * 0.5
        weighted_scores.append(s * recency_weight)
    weighted_avg = sum(weighted_scores) / sum(
        1.0 + (len(scores) - i) / len(scores) * 0.5 for i in range(len(scores))
    )

    direction, confidence = _score_to_signal(weighted_avg, len(scores))

    top = sorted(
        zip(feed.articles, scores), key=lambda x: abs(x[1]), reverse=True
    )[:5]

    return SentimentSignal(
        direction=direction,
        confidence=confidence,
        score=round(weighted_avg, 4),
        sample_size=len(scores),
        source="news",
        top_headlines=[f"[{s:.2f}] {a.title}" for a, s in top],
        details={
            "raw_avg": round(avg_score, 4),
            "weighted_avg": round(weighted_avg, 4),
            "positive": sum(1 for s in scores if s > 0.05),
            "negative": sum(1 for s in scores if s < -0.05),
            "neutral": sum(1 for s in scores if -0.05 <= s <= 0.05),
        },
    )


def analyse_social(feed: SocialFeed) -> SentimentSignal:
    """Analyse a SocialFeed and return a SentimentSignal."""
    if not feed.posts:
        return SentimentSignal(
            direction="HOLD", confidence=0.0, score=0.0, sample_size=0, source="social"
        )

    # Weight by upvotes
    texts = [p.text for p in feed.posts]
    scores = score_texts(texts)
    upvotes = [max(p.score, 1) for p in feed.posts]

    total_weight = sum(upvotes)
    weighted_avg = (
        sum(s * w for s, w in zip(scores, upvotes)) / total_weight
        if total_weight > 0
        else 0.0
    )

    direction, confidence = _score_to_signal(weighted_avg, len(scores))

    return SentimentSignal(
        direction=direction,
        confidence=confidence,
        score=round(weighted_avg, 4),
        sample_size=len(scores),
        source="social",
        details={
            "weighted_avg": round(weighted_avg, 4),
            "total_engagement": total_weight,
        },
    )


def combine_sentiment(
    news_signal: SentimentSignal,
    social_signal: SentimentSignal,
    news_weight: float = 0.6,
    social_weight: float = 0.4,
) -> SentimentSignal:
    """Combine news and social sentiment into a single signal."""
    # Only use sources that have data
    if news_signal.sample_size == 0 and social_signal.sample_size == 0:
        return SentimentSignal(
            direction="HOLD", confidence=0.0, score=0.0,
            sample_size=0, source="combined"
        )

    if news_signal.sample_size == 0:
        return SentimentSignal(
            direction=social_signal.direction,
            confidence=social_signal.confidence,
            score=social_signal.score,
            sample_size=social_signal.sample_size,
            source="combined",
            top_headlines=social_signal.top_headlines,
            details={"social_only": True, **social_signal.details},
        )

    if social_signal.sample_size == 0:
        return SentimentSignal(
            direction=news_signal.direction,
            confidence=news_signal.confidence,
            score=news_signal.score,
            sample_size=news_signal.sample_size,
            source="combined",
            top_headlines=news_signal.top_headlines,
            details={"news_only": True, **news_signal.details},
        )

    combined_score = (
        news_signal.score * news_weight + social_signal.score * social_weight
    )
    total_samples = news_signal.sample_size + social_signal.sample_size
    direction, confidence = _score_to_signal(combined_score, total_samples)

    return SentimentSignal(
        direction=direction,
        confidence=confidence,
        score=round(combined_score, 4),
        sample_size=total_samples,
        source="combined",
        top_headlines=news_signal.top_headlines,
        details={
            "news_score": news_signal.score,
            "social_score": social_signal.score,
            "news_weight": news_weight,
            "social_weight": social_weight,
        },
    )


def _score_to_signal(score: float, sample_size: int) -> tuple[str, float]:
    """Convert a compound score to direction + confidence."""
    # Sample-size confidence dampening
    sample_factor = min(sample_size / 10, 1.0)  # full confidence at 10+ samples

    if score > 0.15:
        direction = "BUY"
    elif score < -0.15:
        direction = "SELL"
    else:
        direction = "HOLD"

    confidence = min(abs(score), 1.0) * sample_factor
    return direction, round(confidence, 4)
