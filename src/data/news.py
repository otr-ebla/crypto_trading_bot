"""
News aggregation module.

Fetches crypto-related headlines from NewsAPI and scores them for sentiment.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import aiohttp

from src.config import settings

logger = logging.getLogger(__name__)

_NEWSAPI_URL = "https://newsapi.org/v2/everything"

# Coin name → search keywords
COIN_KEYWORDS: Dict[str, List[str]] = {
    "BTC": ["bitcoin", "BTC"],
    "ETH": ["ethereum", "ETH", "ether"],
    "SOL": ["solana", "SOL"],
    "XRP": ["ripple", "XRP"],
    "ADA": ["cardano", "ADA"],
    "DOGE": ["dogecoin", "DOGE"],
    "AVAX": ["avalanche", "AVAX"],
    "DOT": ["polkadot", "DOT"],
    "LINK": ["chainlink", "LINK"],
    "MATIC": ["polygon", "MATIC"],
}


@dataclass
class NewsArticle:
    title: str
    source: str
    description: str
    url: str
    published_at: str
    content: Optional[str] = None


@dataclass
class NewsFeed:
    symbol: str
    articles: List[NewsArticle] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _base_symbol(symbol: str) -> str:
    """Extract base currency from a trading pair like 'BTC/USDT' → 'BTC'."""
    return symbol.split("/")[0].upper()


async def fetch_news_for_symbol(
    symbol: str,
    max_articles: int = 20,
    session: Optional[aiohttp.ClientSession] = None,
) -> NewsFeed:
    """Fetch recent news articles for a crypto symbol."""
    base = _base_symbol(symbol)
    keywords = COIN_KEYWORDS.get(base, [base])
    query = " OR ".join(keywords) + " cryptocurrency"

    if not settings.sentiment.news_api_key:
        logger.warning("NEWS_API_KEY not set — returning empty feed")
        return NewsFeed(symbol=symbol)

    params = {
        "q": query,
        "apiKey": settings.sentiment.news_api_key,
        "language": "en",
        "sortBy": "publishedAt",
        "pageSize": max_articles,
    }

    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession()

    try:
        async with session.get(_NEWSAPI_URL, params=params) as resp:
            if resp.status != 200:
                logger.error(f"NewsAPI returned {resp.status}")
                return NewsFeed(symbol=symbol)
            data = await resp.json()

        articles = [
            NewsArticle(
                title=a.get("title", ""),
                source=a.get("source", {}).get("name", "unknown"),
                description=a.get("description", ""),
                url=a.get("url", ""),
                published_at=a.get("publishedAt", ""),
                content=a.get("content"),
            )
            for a in data.get("articles", [])
            if a.get("title")
        ]

        logger.info(f"Fetched {len(articles)} news articles for {symbol}")
        return NewsFeed(symbol=symbol, articles=articles)
    except Exception as e:
        logger.error(f"News fetch error for {symbol}: {e}")
        return NewsFeed(symbol=symbol)
    finally:
        if own_session:
            await session.close()


async def fetch_news_batch(
    symbols: List[str], max_articles: int = 15
) -> Dict[str, NewsFeed]:
    """Fetch news for multiple symbols concurrently."""
    async with aiohttp.ClientSession() as session:
        tasks = [
            fetch_news_for_symbol(sym, max_articles, session) for sym in symbols
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    feeds: Dict[str, NewsFeed] = {}
    for sym, result in zip(symbols, results):
        if isinstance(result, Exception):
            logger.error(f"News batch error for {sym}: {result}")
            feeds[sym] = NewsFeed(symbol=sym)
        else:
            feeds[sym] = result
    return feeds


def fetch_news_sync(symbol: str, max_articles: int = 20) -> NewsFeed:
    """Synchronous convenience wrapper."""
    return asyncio.run(fetch_news_for_symbol(symbol, max_articles))
