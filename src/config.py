"""
Centralised configuration loaded from environment variables / .env file.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import List

from dotenv import load_dotenv

# Load .env from project root
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


def _bool(val: str | None, default: bool = False) -> bool:
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes")


def _float(val: str | None, default: float = 0.0) -> float:
    if val is None:
        return default
    return float(val)


def _int(val: str | None, default: int = 0) -> int:
    if val is None:
        return default
    return int(val)


@dataclass(frozen=True)
class ExchangeConfig:
    id: str = os.getenv("EXCHANGE_ID", "binance")
    api_key: str = os.getenv("EXCHANGE_API_KEY", "")
    api_secret: str = os.getenv("EXCHANGE_API_SECRET", "")
    sandbox: bool = _bool(os.getenv("EXCHANGE_SANDBOX"), default=True)


@dataclass(frozen=True)
class TradingConfig:
    mode: str = os.getenv("TRADING_MODE", "paper")  # "paper" | "live"
    symbols: List[str] = field(
        default_factory=lambda: os.getenv(
            "TRADING_SYMBOLS", "BTC/USDT,ETH/USDT,SOL/USDT"
        ).split(",")
    )
    timeframe: str = os.getenv("TRADING_TIMEFRAME", "1h")
    interval_seconds: int = _int(os.getenv("TRADING_INTERVAL_SECONDS"), 60)


@dataclass(frozen=True)
class RiskConfig:
    max_position_pct: float = _float(os.getenv("RISK_MAX_POSITION_PCT"), 5.0)
    stop_loss_pct: float = _float(os.getenv("RISK_STOP_LOSS_PCT"), 3.0)
    take_profit_pct: float = _float(os.getenv("RISK_TAKE_PROFIT_PCT"), 6.0)
    max_daily_drawdown_pct: float = _float(
        os.getenv("RISK_MAX_DAILY_DRAWDOWN_PCT"), 10.0
    )
    max_consecutive_losses: int = _int(os.getenv("RISK_MAX_CONSECUTIVE_LOSSES"), 3)
    cooldown_minutes: int = _int(os.getenv("RISK_COOLDOWN_MINUTES"), 30)
    paper_starting_balance: float = _float(
        os.getenv("PAPER_STARTING_BALANCE"), 10_000.0
    )


@dataclass(frozen=True)
class SentimentConfig:
    news_api_key: str = os.getenv("NEWS_API_KEY", "")
    news_enabled: bool = _bool(os.getenv("NEWS_ENABLED"), default=True)
    reddit_client_id: str = os.getenv("REDDIT_CLIENT_ID", "")
    reddit_client_secret: str = os.getenv("REDDIT_CLIENT_SECRET", "")
    reddit_user_agent: str = os.getenv("REDDIT_USER_AGENT", "TradingBot/0.1")
    reddit_enabled: bool = _bool(os.getenv("REDDIT_ENABLED"), default=False)


@dataclass(frozen=True)
class Settings:
    exchange: ExchangeConfig = field(default_factory=ExchangeConfig)
    trading: TradingConfig = field(default_factory=TradingConfig)
    risk: RiskConfig = field(default_factory=RiskConfig)
    sentiment: SentimentConfig = field(default_factory=SentimentConfig)
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///tradingbot.db")
    log_level: str = os.getenv("LOG_LEVEL", "INFO")
    log_file: str = os.getenv("LOG_FILE", "logs/tradingbot.log")
    dashboard_host: str = os.getenv("DASHBOARD_HOST", "127.0.0.1")
    dashboard_port: int = _int(os.getenv("DASHBOARD_PORT"), 8080)


# Global singleton
settings = Settings()
