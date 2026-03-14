# 🤖 Crypto Trading Bot

AI-powered cryptocurrency trading bot that combines **technical analysis**, **news & social sentiment**, and a **learning feedback loop** to generate informed trading signals.

## ✨ Features

- **Multi-Indicator Technical Analysis** — RSI, MACD, Bollinger Bands, EMA crossovers, ATR, volume analysis
- **Sentiment Analysis** — VADER NLP with crypto-specific lexicon on news headlines and Reddit posts
- **Learning Feedback Loop** — Automatically adjusts signal weights based on trade outcomes
- **Risk Management** — Stop-loss, take-profit, position sizing, drawdown limits, cooldown periods
- **Paper Trading** — Safe simulation mode with virtual portfolio tracking
- **Live Trading** — Real order execution via CCXT (30+ exchanges supported)
- **Web Dashboard** — Real-time monitoring with dark-mode UI
- **Beautiful CLI** — Rich terminal output with tables, panels, and emoji

## 🚀 Quick Start

### 1. Install

```bash
cd TradingBot
pip install -e ".[dev]"
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run (Paper Trading)

```bash
python -m src.cli run --mode paper
```

### 4. Check Status

```bash
python -m src.cli status
```

### 5. Backtest

```bash
python -m src.cli backtest --days 30 --strategy momentum_sentiment
```

## 📊 Architecture

```
src/
├── config.py              # Centralised settings from .env
├── logger.py              # Rich logging + rotating file handler
├── models.py              # SQLAlchemy models (Trade, Signal, etc.)
├── database.py            # Session management
├── __main__.py            # Main trading loop
├── cli.py                 # Click CLI interface
├── data/
│   ├── exchange.py        # CCXT wrapper (OHLCV, orders)
│   ├── news.py            # NewsAPI async fetcher
│   └── social.py          # Reddit async fetcher
├── analysis/
│   ├── technical.py       # TA indicators + scoring
│   ├── sentiment.py       # VADER NLP + crypto lexicon
│   └── signal_aggregator.py  # Weighted signal combiner
├── trading/
│   ├── strategy.py        # Strategy ABC + implementations
│   ├── risk_manager.py    # Position sizing, SL/TP, drawdown
│   ├── executor.py        # Paper + live executors
│   └── learner.py         # Learning feedback loop
└── dashboard/
    └── app.py             # Web dashboard (single-page)
```

## 🧠 How the Learning Loop Works

1. **Signal Recording** — Every technical and sentiment signal is saved to the database
2. **Trade Tagging** — When a trade closes, the learner finds signals that preceded it
3. **Weight Update** — Profitable-signal sources get their weight increased; unprofitable ones decreased
4. **Periodic Retraining** — Every 20 cycles, weights are recalculated based on cumulative accuracy
5. **Persistence** — Weights are stored in SQLite, so the bot keeps learning across restarts

## ⚙️ Configuration

| Variable | Default | Description |
|---|---|---|
| `EXCHANGE_ID` | `binance` | Exchange to use (any CCXT-supported) |
| `TRADING_MODE` | `paper` | `paper` or `live` |
| `TRADING_SYMBOLS` | `BTC/USDT,ETH/USDT,SOL/USDT` | Comma-separated pairs |
| `RISK_MAX_POSITION_PCT` | `5.0` | Max portfolio % per trade |
| `RISK_STOP_LOSS_PCT` | `3.0` | Stop-loss percentage |
| `RISK_TAKE_PROFIT_PCT` | `6.0` | Take-profit percentage |
| `NEWS_API_KEY` | — | NewsAPI.org key |
| `PAPER_STARTING_BALANCE` | `10000` | Virtual starting balance |

See [.env.example](.env.example) for all options.

## ☁️ Cloud Deployment & Mobile Access

To run the bot 24/7 and check it from your smartphone while outside, you can deploy it to a Virtual Private Server (VPS) like DigitalOcean, Linode, AWS, or an app platform like Render.

### Using Docker (Recommended for VPS)

1. Clone the repository on your server.
2. Edit your `.env` file with your API keys.
3. Run the bot using Docker Compose:

```bash
docker-compose up -d
```

The web dashboard will be available on `http://<YOUR_SERVER_IP>:8080`. You can access this IP address directly from your smartphone browser.

### Quick Local Exposure (Ngrok)
If you just want to run it on your laptop but check it from your phone temporarily, you can use **ngrok**:

1. Start the bot normally: `python -m src.cli dashboard`
2. In a new terminal, run: `ngrok http 8080`
3. Ngrok will give you a public `https://...` link. Open that link on your phone!

## ⚠️ Disclaimer

This bot is for **educational and research purposes**. Cryptocurrency trading involves substantial risk of loss. Always start with paper trading and never invest more than you can afford to lose.
