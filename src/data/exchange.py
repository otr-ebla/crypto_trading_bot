"""
Exchange data wrapper using CCXT.

Provides unified access to market data and order execution across exchanges.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import ccxt
import pandas as pd

from src.config import settings

logger = logging.getLogger(__name__)


class ExchangeClient:
    """Wrapper around a CCXT exchange instance."""

    def __init__(self) -> None:
        exchange_class = getattr(ccxt, settings.exchange.id, None)
        if exchange_class is None:
            raise ValueError(f"Unknown exchange: {settings.exchange.id}")

        self._exchange: ccxt.Exchange = exchange_class(
            {
                "apiKey": settings.exchange.api_key or None,
                "secret": settings.exchange.api_secret or None,
                "enableRateLimit": True,
                "options": {"defaultType": "spot"},
            }
        )

        if settings.exchange.sandbox:
            try:
                self._exchange.set_sandbox_mode(True)
                logger.info("Exchange running in [bold yellow]sandbox[/] mode")
            except ccxt.NotSupported:
                logger.warning(f"Sandbox mode not supported by {settings.exchange.id}. Falling back to standard API URLs (safe for Paper Trading).")
            except Exception as e:
                logger.warning(f"Could not enable sandbox mode: {e}")

    # ── Market Data ──────────────────────────────────────────────────

    def fetch_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1h",
        limit: int = 200,
    ) -> pd.DataFrame:
        """Fetch OHLCV candle data and return as a DataFrame."""
        raw = self._exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
        df = pd.DataFrame(
            raw, columns=["timestamp", "open", "high", "low", "close", "volume"]
        )
        df["timestamp"] = pd.to_datetime(df["timestamp"], unit="ms", utc=True)
        df.set_index("timestamp", inplace=True)
        logger.debug(f"Fetched {len(df)} candles for {symbol} ({timeframe})")
        return df

    def fetch_ticker(self, symbol: str) -> Dict[str, Any]:
        """Get the latest ticker snapshot for a symbol."""
        ticker = self._exchange.fetch_ticker(symbol)
        return {
            "symbol": ticker["symbol"],
            "last": ticker["last"],
            "bid": ticker["bid"],
            "ask": ticker["ask"],
            "volume": ticker.get("baseVolume"),
            "change_pct": ticker.get("percentage"),
            "timestamp": datetime.now(timezone.utc),
        }

    def fetch_order_book(self, symbol: str, limit: int = 20) -> Dict[str, Any]:
        """Fetch order book depth."""
        book = self._exchange.fetch_order_book(symbol, limit=limit)
        return {
            "symbol": symbol,
            "bids": book["bids"][:limit],
            "asks": book["asks"][:limit],
            "timestamp": datetime.now(timezone.utc),
        }

    # ── Order Execution ──────────────────────────────────────────────

    def place_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        order_type: str = "market",
        price: Optional[float] = None,
    ) -> Dict[str, Any]:
        """Place an order on the exchange (live mode only)."""
        logger.info(
            f"Placing {order_type} {side} order: {amount} {symbol}"
            + (f" @{price}" if price else "")
        )
        if order_type == "limit" and price is not None:
            order = self._exchange.create_order(
                symbol, order_type, side, amount, price
            )
        else:
            order = self._exchange.create_order(symbol, "market", side, amount)
        logger.info(f"Order placed: id={order['id']} status={order['status']}")
        return order

    def fetch_balance(self) -> Dict[str, float]:
        """Fetch account balances (non-zero only)."""
        balance = self._exchange.fetch_balance()
        return {
            k: v
            for k, v in balance.get("total", {}).items()
            if v and float(v) > 0
        }

    def get_markets(self) -> List[str]:
        """Return available trading pair symbols."""
        self._exchange.load_markets()
        return list(self._exchange.symbols)
