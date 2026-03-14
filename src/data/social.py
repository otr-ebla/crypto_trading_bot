"""
Social media data fetcher.

Pulls recent posts/comments from Reddit (via asyncpraw) about tracked coins.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional

from src.config import settings

logger = logging.getLogger(__name__)

# Subreddits to scan
CRYPTO_SUBREDDITS = [
    "cryptocurrency",
    "bitcoin",
    "ethereum",
    "CryptoMarkets",
    "SatoshiStreetBets",
    "altcoin",
]

# Symbol → subreddit overrides
SYMBOL_SUBREDDITS: Dict[str, List[str]] = {
    "BTC": ["bitcoin", "cryptocurrency"],
    "ETH": ["ethereum", "cryptocurrency"],
    "SOL": ["solana", "cryptocurrency"],
    "DOGE": ["dogecoin", "cryptocurrency"],
}


@dataclass
class SocialPost:
    text: str
    source: str  # "reddit"
    subreddit: str
    score: int  # upvotes
    created_at: datetime
    url: str = ""


@dataclass
class SocialFeed:
    symbol: str
    posts: List[SocialPost] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


def _base_symbol(symbol: str) -> str:
    return symbol.split("/")[0].upper()


async def fetch_reddit_posts(
    symbol: str,
    limit: int = 25,
) -> SocialFeed:
    """Fetch recent Reddit posts mentioning a coin."""
    if not settings.sentiment.reddit_enabled:
        logger.debug("Reddit integration disabled")
        return SocialFeed(symbol=symbol)

    if not settings.sentiment.reddit_client_id:
        logger.warning("REDDIT_CLIENT_ID not set — skipping Reddit fetch")
        return SocialFeed(symbol=symbol)

    try:
        import asyncpraw  # lazy import
    except ImportError:
        logger.error("asyncpraw not installed — run: pip install asyncpraw")
        return SocialFeed(symbol=symbol)

    base = _base_symbol(symbol)
    subreddits = SYMBOL_SUBREDDITS.get(base, ["cryptocurrency"])

    posts: List[SocialPost] = []

    try:
        reddit = asyncpraw.Reddit(
            client_id=settings.sentiment.reddit_client_id,
            client_secret=settings.sentiment.reddit_client_secret,
            user_agent=settings.sentiment.reddit_user_agent,
        )

        for sub_name in subreddits:
            try:
                subreddit = await reddit.subreddit(sub_name)
                async for submission in subreddit.hot(limit=limit):
                    # Filter: title must mention the coin
                    title_lower = submission.title.lower()
                    if base.lower() in title_lower or any(
                        kw.lower() in title_lower
                        for kw in [base, base.lower()]
                    ):
                        posts.append(
                            SocialPost(
                                text=f"{submission.title} {submission.selftext[:300]}",
                                source="reddit",
                                subreddit=sub_name,
                                score=submission.score,
                                created_at=datetime.fromtimestamp(
                                    submission.created_utc, tz=timezone.utc
                                ),
                                url=f"https://reddit.com{submission.permalink}",
                            )
                        )
            except Exception as e:
                logger.warning(f"Error fetching r/{sub_name}: {e}")

        await reddit.close()
        logger.info(f"Fetched {len(posts)} Reddit posts for {symbol}")

    except Exception as e:
        logger.error(f"Reddit fetch error for {symbol}: {e}")

    return SocialFeed(symbol=symbol, posts=posts)


async def fetch_social_batch(
    symbols: List[str], limit: int = 25
) -> Dict[str, SocialFeed]:
    """Fetch social data for multiple symbols."""
    tasks = [fetch_reddit_posts(sym, limit) for sym in symbols]
    results = await asyncio.gather(*tasks, return_exceptions=True)

    feeds: Dict[str, SocialFeed] = {}
    for sym, result in zip(symbols, results):
        if isinstance(result, Exception):
            logger.error(f"Social batch error for {sym}: {result}")
            feeds[sym] = SocialFeed(symbol=sym)
        else:
            feeds[sym] = result
    return feeds


def fetch_social_sync(symbol: str, limit: int = 25) -> SocialFeed:
    """Synchronous convenience wrapper."""
    return asyncio.run(fetch_reddit_posts(symbol, limit))
