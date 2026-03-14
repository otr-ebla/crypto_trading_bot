"""
Free news & social scraper — NO API keys required.

Sources:
  • Reddit public JSON API (r/cryptocurrency, r/bitcoin, r/ethereum, etc.)
  • DuckDuckGo Instant Answer API (news headlines)
  • CryptoPanic public RSS feed

All sources are free and require zero credentials.
"""

from __future__ import annotations

import logging
import re
import json
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, List, Optional
from html import unescape

import requests

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}
_TIMEOUT = 5


# ─── Data Classes ────────────────────────────────────────────────────

@dataclass
class ScrapedArticle:
    title: str
    source: str
    description: str = ""
    url: str = ""
    published_at: str = ""

@dataclass
class ScrapedPost:
    title: str
    subreddit: str
    score: int = 0
    num_comments: int = 0
    url: str = ""
    created_at: str = ""

@dataclass
class FreeScraperResult:
    symbol: str
    articles: List[ScrapedArticle] = field(default_factory=list)
    reddit_posts: List[ScrapedPost] = field(default_factory=list)
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


# ─── Coin keyword mapping ────────────────────────────────────────────

COIN_NAMES = {
    "BTC": "bitcoin", "ETH": "ethereum", "SOL": "solana",
    "XRP": "ripple", "ADA": "cardano", "DOGE": "dogecoin",
    "AVAX": "avalanche", "DOT": "polkadot", "LINK": "chainlink",
    "MATIC": "polygon",
}

COIN_KEYWORDS = {
    "BTC": ["bitcoin", "btc"],
    "ETH": ["ethereum", "eth", "ether"],
    "SOL": ["solana"],
    "XRP": ["ripple", "xrp"],
    "ADA": ["cardano"],
    "DOGE": ["dogecoin", "doge"],
    "AVAX": ["avalanche"],
    "DOT": ["polkadot"],
    "LINK": ["chainlink"],
    "MATIC": ["polygon", "matic"],
}

REDDIT_SUBS = {
    "BTC":  ["bitcoin", "cryptocurrency"],
    "ETH":  ["ethereum", "cryptocurrency"],
    "SOL":  ["solana", "cryptocurrency"],
    "XRP":  ["ripple", "cryptocurrency"],
    "DOGE": ["dogecoin", "cryptocurrency"],
    "ADA":  ["cardano", "cryptocurrency"],
}

_TAG_RE = re.compile(r"<[^>]+>")

def _strip_html(text: str) -> str:
    if not text:
        return ""
    return unescape(_TAG_RE.sub("", text)).strip()

def _base(symbol: str) -> str:
    return symbol.split("/")[0].upper()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SOURCE 1: Reddit Public JSON API (fast, free, no key)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def _fetch_reddit_sub(sub: str, keywords: List[str], limit: int = 25) -> List[ScrapedPost]:
    """Fetch hot posts from a single subreddit, filtered by keyword."""
    posts: List[ScrapedPost] = []
    try:
        url = f"https://www.reddit.com/r/{sub}/hot.json?limit={limit}"
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json()
        for child in data.get("data", {}).get("children", []):
            pd = child.get("data", {})
            title = pd.get("title", "")
            if not any(kw in title.lower() for kw in keywords):
                continue
            created_utc = pd.get("created_utc", 0)
            posts.append(ScrapedPost(
                title=title,
                subreddit=sub,
                score=pd.get("score", 0),
                num_comments=pd.get("num_comments", 0),
                url=f"https://reddit.com{pd.get('permalink', '')}",
                created_at=datetime.fromtimestamp(
                    created_utc, tz=timezone.utc
                ).isoformat() if created_utc else "",
            ))
    except Exception as e:
        logger.debug(f"Reddit r/{sub}: {e}")
    return posts


def fetch_reddit(symbol: str) -> List[ScrapedPost]:
    """Fetch Reddit posts for a symbol using parallel requests."""
    base = _base(symbol)
    subs = REDDIT_SUBS.get(base, ["cryptocurrency"])
    keywords = COIN_KEYWORDS.get(base, [base.lower()])

    all_posts: List[ScrapedPost] = []
    with ThreadPoolExecutor(max_workers=len(subs)) as executor:
        futures = {
            executor.submit(_fetch_reddit_sub, sub, keywords): sub
            for sub in subs
        }
        for future in as_completed(futures, timeout=10):
            try:
                all_posts.extend(future.result())
            except Exception:
                pass

    # Deduplicate by title
    seen = set()
    unique = []
    for p in all_posts:
        if p.title not in seen:
            seen.add(p.title)
            unique.append(p)

    return sorted(unique, key=lambda p: p.score, reverse=True)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SOURCE 2: DuckDuckGo Instant Answers API (fast, free, no key)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_ddg_news(symbol: str) -> List[ScrapedArticle]:
    """Fetch news using DuckDuckGo's instant answer API."""
    base = _base(symbol)
    coin_name = COIN_NAMES.get(base, base.lower())
    articles: List[ScrapedArticle] = []

    try:
        url = f"https://api.duckduckgo.com/?q={coin_name}+crypto+news&format=json&no_html=1"
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []
        data = resp.json()

        # Related topics often contain news-like headlines
        for topic in data.get("RelatedTopics", []):
            text = topic.get("Text", "")
            first_url = topic.get("FirstURL", "")
            if text:
                articles.append(ScrapedArticle(
                    title=text[:200],
                    source="DuckDuckGo",
                    description="",
                    url=first_url,
                ))
            # Nested topics
            for sub_topic in topic.get("Topics", []):
                sub_text = sub_topic.get("Text", "")
                if sub_text:
                    articles.append(ScrapedArticle(
                        title=sub_text[:200],
                        source="DuckDuckGo",
                        url=sub_topic.get("FirstURL", ""),
                    ))

        # Abstract text
        abstract = data.get("AbstractText", "")
        if abstract:
            articles.append(ScrapedArticle(
                title=abstract[:200],
                source=data.get("AbstractSource", "DuckDuckGo"),
                description=abstract[:500],
                url=data.get("AbstractURL", ""),
            ))
    except Exception as e:
        logger.debug(f"DuckDuckGo news: {e}")

    return articles


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# SOURCE 3: CryptoPanic Public RSS (free, no key needed for basic)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def fetch_cryptopanic(symbol: str) -> List[ScrapedArticle]:
    """Fetch news headlines from CryptoPanic's public RSS feed."""
    base = _base(symbol)
    articles: List[ScrapedArticle] = []
    keywords = COIN_KEYWORDS.get(base, [base.lower()])

    try:
        url = "https://cryptopanic.com/news/rss/"
        resp = requests.get(url, headers=_HEADERS, timeout=_TIMEOUT)
        if resp.status_code != 200:
            return []

        root = ET.fromstring(resp.text)
        for item in root.findall(".//item")[:30]:
            title_el = item.find("title")
            link_el = item.find("link")
            pub_el = item.find("pubDate")

            title = title_el.text if title_el is not None and title_el.text else ""
            if not title:
                continue

            # Filter by keyword
            if not any(kw in title.lower() for kw in keywords):
                continue

            articles.append(ScrapedArticle(
                title=_strip_html(title),
                source="CryptoPanic",
                url=link_el.text if link_el is not None else "",
                published_at=pub_el.text if pub_el is not None else "",
            ))
    except Exception as e:
        logger.debug(f"CryptoPanic: {e}")

    return articles


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# COMBINED (all sources in parallel)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

def scrape_all(symbol: str) -> FreeScraperResult:
    """Fetch news + Reddit for a symbol from ALL free sources in parallel."""
    articles: List[ScrapedArticle] = []
    reddit_posts: List[ScrapedPost] = []

    with ThreadPoolExecutor(max_workers=3) as executor:
        future_reddit = executor.submit(fetch_reddit, symbol)
        future_ddg = executor.submit(fetch_ddg_news, symbol)
        future_cp = executor.submit(fetch_cryptopanic, symbol)

        try:
            reddit_posts = future_reddit.result(timeout=12)
        except Exception as e:
            logger.debug(f"Reddit fetch failed: {e}")

        try:
            articles.extend(future_ddg.result(timeout=8))
        except Exception as e:
            logger.debug(f"DDG fetch failed: {e}")

        try:
            articles.extend(future_cp.result(timeout=8))
        except Exception as e:
            logger.debug(f"CryptoPanic fetch failed: {e}")

    logger.info(
        f"Free scraper: {len(articles)} news + {len(reddit_posts)} Reddit posts for {symbol}"
    )
    return FreeScraperResult(
        symbol=symbol,
        articles=articles,
        reddit_posts=reddit_posts,
    )
