"""
Premium Web Dashboard — Backend API Server.

Serves the frontend and provides REST API endpoints for:
- Portfolio stats & P&L
- OHLCV chart data (via CCXT)
- Trade history
- Signal weights
- Live sentiment analysis (fetches from news/social)
- Ticker prices
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import traceback
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse, parse_qs

from src.config import settings
from src.database import init_db, get_db
from src.models import Trade, SignalWeight, SentimentSnapshot, Signal
from src.logger import setup_logging

logger = logging.getLogger(__name__)

_STATIC_DIR = Path(__file__).parent / "static"


# ══════════════════════════════════════════════════════════════════════
# API Data Helpers
# ══════════════════════════════════════════════════════════════════════

def _get_portfolio_stats() -> Dict[str, Any]:
    with get_db() as db:
        closed = (
            db.query(Trade)
            .filter(Trade.status == "closed", Trade.pnl.isnot(None))
            .all()
        )
        open_trades = db.query(Trade).filter(Trade.status == "open").all()

        total_pnl = sum(t.pnl for t in closed)
        winning = [t for t in closed if t.pnl > 0]
        losing = [t for t in closed if t.pnl <= 0]

        best_trade = max(closed, key=lambda t: t.pnl) if closed else None
        worst_trade = min(closed, key=lambda t: t.pnl) if closed else None

        avg_win = sum(t.pnl for t in winning) / len(winning) if winning else 0
        avg_loss = sum(t.pnl for t in losing) / len(losing) if losing else 0

    return {
        "total_pnl": round(total_pnl, 2),
        "total_trades": len(closed),
        "winning_trades": len(winning),
        "losing_trades": len(losing),
        "win_rate": round(len(winning) / len(closed) * 100, 1) if closed else 0,
        "avg_win": round(avg_win, 2),
        "avg_loss": round(avg_loss, 2),
        "best_trade": round(best_trade.pnl, 2) if best_trade else 0,
        "worst_trade": round(worst_trade.pnl, 2) if worst_trade else 0,
        "open_positions": len(open_trades),
        "paper_balance": settings.risk.paper_starting_balance + total_pnl,
    }


def _get_trades(limit: int = 100, status: Optional[str] = None) -> List[Dict]:
    with get_db() as db:
        q = db.query(Trade).order_by(Trade.entry_time.desc())
        if status:
            q = q.filter(Trade.status == status)
        trades = q.limit(limit).all()
        return [
            {
                "id": t.id,
                "symbol": t.symbol,
                "side": t.side,
                "entry_price": t.entry_price,
                "exit_price": t.exit_price,
                "amount": t.amount,
                "pnl": t.pnl,
                "pnl_pct": t.pnl_pct,
                "status": t.status,
                "strategy": t.strategy,
                "mode": t.mode,
                "stop_loss": t.stop_loss,
                "take_profit": t.take_profit,
                "notes": t.notes,
                "entry_time": t.entry_time.isoformat() if t.entry_time else None,
                "exit_time": t.exit_time.isoformat() if t.exit_time else None,
            }
            for t in trades
        ]


def _get_signal_weights() -> List[Dict]:
    with get_db() as db:
        weights = db.query(SignalWeight).all()
        return [
            {
                "source": w.source,
                "weight": round(w.weight, 4),
                "total_signals": w.total_signals,
                "correct_signals": w.correct_signals,
                "accuracy": (
                    round(w.correct_signals / w.total_signals * 100, 1)
                    if w.total_signals > 0
                    else 0
                ),
                "updated_at": w.updated_at.isoformat() if w.updated_at else None,
            }
            for w in weights
        ]


def _get_chart_data(symbol: str, timeframe: str = "1h", limit: int = 200) -> List[Dict]:
    """Fetch OHLCV data from the exchange for charting."""
    try:
        from src.data.exchange import ExchangeClient
        exchange = ExchangeClient()
        df = exchange.fetch_ohlcv(symbol, timeframe, limit)
        data = []
        for ts, row in df.iterrows():
            data.append({
                "time": int(ts.timestamp()),
                "open": round(float(row["open"]), 2),
                "high": round(float(row["high"]), 2),
                "low": round(float(row["low"]), 2),
                "close": round(float(row["close"]), 2),
                "volume": round(float(row["volume"]), 4),
            })
        return data
    except Exception as e:
        logger.error(f"Chart data error: {e}")
        return []


def _get_ticker(symbol: str) -> Dict:
    """Get current price for a symbol."""
    try:
        from src.data.exchange import ExchangeClient
        exchange = ExchangeClient()
        return exchange.fetch_ticker(symbol)
    except Exception as e:
        logger.error(f"Ticker error: {e}")
        return {"symbol": symbol, "last": 0, "bid": 0, "ask": 0, "change_pct": 0}


def _get_tickers() -> List[Dict]:
    """Get tickers for all tracked symbols."""
    results = []
    try:
        from src.data.exchange import ExchangeClient
        exchange = ExchangeClient()
        for sym in settings.trading.symbols:
            try:
                t = exchange.fetch_ticker(sym)
                t["timestamp"] = t["timestamp"].isoformat() if hasattr(t.get("timestamp", ""), "isoformat") else str(t.get("timestamp", ""))
                results.append(t)
            except Exception as e:
                logger.warning(f"Ticker error for {sym}: {e}")
                results.append({"symbol": sym, "last": 0, "change_pct": 0})
    except Exception as e:
        logger.error(f"Tickers error: {e}")
    return results


def _fetch_live_sentiment(symbol: str) -> Dict:
    """Fetch live sentiment for a symbol from news."""
    try:
        from src.data.news import fetch_news_sync
        from src.analysis.sentiment import analyse_news

        feed = fetch_news_sync(symbol, max_articles=15)
        signal = analyse_news(feed)

        # Store snapshot
        from src.models import SentimentSnapshot
        with get_db() as db:
            snap = SentimentSnapshot(
                symbol=symbol,
                source="news",
                score=signal.score,
                sample_size=signal.sample_size,
                headlines=[h for h in signal.top_headlines[:5]],
            )
            db.add(snap)

        return {
            "symbol": symbol,
            "direction": signal.direction,
            "confidence": signal.confidence,
            "score": signal.score,
            "sample_size": signal.sample_size,
            "top_headlines": signal.top_headlines[:5],
            "details": signal.details,
        }
    except Exception as e:
        logger.error(f"Sentiment error: {e}")
        return {
            "symbol": symbol,
            "direction": "HOLD",
            "confidence": 0,
            "score": 0,
            "sample_size": 0,
            "top_headlines": [],
            "error": str(e),
        }


def _get_recent_signals(limit: int = 30) -> List[Dict]:
    with get_db() as db:
        signals = (
            db.query(Signal)
            .order_by(Signal.created_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": s.id,
                "symbol": s.symbol,
                "source": s.source,
                "direction": s.direction,
                "confidence": s.confidence,
                "was_correct": s.was_correct,
                "outcome_pnl": s.outcome_pnl,
                "created_at": s.created_at.isoformat() if s.created_at else None,
            }
            for s in signals
        ]


# ══════════════════════════════════════════════════════════════════════
# HTTP Server
# ══════════════════════════════════════════════════════════════════════

class DashboardHandler(BaseHTTPRequestHandler):
    """HTTP handler for the dashboard."""

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        params = parse_qs(parsed.query)

        # API routes
        if path == "/api/stats":
            self._json_response(_get_portfolio_stats())

        elif path == "/api/trades":
            limit = int(params.get("limit", [100])[0])
            status = params.get("status", [None])[0]
            self._json_response(_get_trades(limit, status))

        elif path == "/api/weights":
            self._json_response(_get_signal_weights())

        elif path == "/api/chart":
            symbol = params.get("symbol", [settings.trading.symbols[0]])[0]
            timeframe = params.get("timeframe", ["1h"])[0]
            limit = int(params.get("limit", [200])[0])
            self._json_response(_get_chart_data(symbol, timeframe, limit))

        elif path == "/api/tickers":
            self._json_response(_get_tickers())

        elif path == "/api/sentiment":
            symbol = params.get("symbol", [settings.trading.symbols[0]])[0]
            self._json_response(_fetch_live_sentiment(symbol))

        elif path == "/api/signals":
            limit = int(params.get("limit", [30])[0])
            self._json_response(_get_recent_signals(limit))

        elif path == "/api/config":
            self._json_response({
                "symbols": settings.trading.symbols,
                "timeframe": settings.trading.timeframe,
                "mode": settings.trading.mode,
                "exchange": settings.exchange.id,
            })

        elif path == "/api/bot/status":
            from src.__main__ import get_bot
            self._json_response(get_bot().get_status())

        elif path == "/api/bot/activity":
            from src.__main__ import get_bot
            self._json_response(get_bot().activity_log)

        # Static files
        else:
            self._serve_static(path)

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path

        if path == "/api/bot/start":
            content_length = int(self.headers.get('Content-Length', 0))
            post_data = self.rfile.read(content_length)
            data = json.loads(post_data) if post_data else {}
            strategy = data.get("strategy", "momentum_sentiment")
            
            from src.__main__ import get_bot
            msg = get_bot().start(strategy_name=strategy)
            self._json_response({"status": "ok", "message": msg})

        elif path == "/api/bot/stop":
            from src.__main__ import get_bot
            msg = get_bot().stop()
            self._json_response({"status": "ok", "message": msg})
            
        else:
            self.send_error(404, "Not found")

    def _json_response(self, data: Any, status: int = 200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data, default=str).encode())

    def _serve_static(self, path: str):
        if path == "/" or path == "":
            path = "/index.html"

        file_path = _STATIC_DIR / path.lstrip("/")

        if not file_path.exists() or not file_path.is_file():
            # Serve index.html for SPA routes
            file_path = _STATIC_DIR / "index.html"

        if not file_path.exists():
            self.send_error(404, "Not found")
            return

        content_types = {
            ".html": "text/html",
            ".css": "text/css",
            ".js": "application/javascript",
            ".json": "application/json",
            ".png": "image/png",
            ".svg": "image/svg+xml",
            ".ico": "image/x-icon",
            ".woff2": "font/woff2",
        }

        ext = file_path.suffix
        content_type = content_types.get(ext, "application/octet-stream")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-cache")
        self.end_headers()
        self.wfile.write(file_path.read_bytes())

    def log_message(self, format, *args):
        first_arg = str(args[0]) if args else ""
        if "/api/" not in first_arg:
            logger.debug(f"Dashboard: {format % args}")


def start_dashboard(host: str = None, port: int = None) -> None:
    """Start the dashboard HTTP server."""
    host = host or settings.dashboard_host
    port = port or settings.dashboard_port

    setup_logging(settings.log_level, settings.log_file)
    init_db()

    # Ensure learner weights exist
    from src.trading.learner import Learner
    Learner()

    server = HTTPServer((host, port), DashboardHandler)
    print(f"""
╔══════════════════════════════════════════════════════════╗
║  🤖 Crypto Trading Bot — Dashboard                      ║
║                                                          ║
║  🌐 Open in browser: http://{host}:{port}          ║
║                                                          ║
║  Press Ctrl+C to stop                                    ║
╚══════════════════════════════════════════════════════════╝
    """)
    logger.info(f"Dashboard running at http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n👋 Dashboard stopped.")
        server.shutdown()
