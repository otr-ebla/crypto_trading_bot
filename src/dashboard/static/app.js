/* ══════════════════════════════════════════════════════════════
   CryptoBot Dashboard — Frontend Application
   ══════════════════════════════════════════════════════════════ */

// ── State ──────────────────────────────────────────────────────
const state = {
  config: { symbols: [], timeframe: "1h", mode: "paper", exchange: "binance" },
  stats: {},
  trades: [],
  weights: [],
  signals: [],
  bot_status: { running: false },
  activity: [],
  charts: {},        // chart instances keyed by container id
  chartSeries: {},   // series instances
  refreshTimer: null,
};

const API = "";  // same origin

// ── Init ───────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", async () => {
  // Capture token from URL if present
  const urlParams = new URLSearchParams(window.location.search);
  const token = urlParams.get('token');
  if (token) {
    sessionStorage.setItem('bot_token', token);
    // Clean up URL without refreshing
    window.history.replaceState({}, document.title, window.location.pathname);
  }

  setupNavigation();
  setupBotControls();
  await loadConfig();
  populateSymbolSelectors();
  await refreshAll();
  // Auto-refresh every 5s for live feel
  state.refreshTimer = setInterval(refreshAll, 5000);
});

// ── Navigation ─────────────────────────────────────────────────
function setupNavigation() {
  document.querySelectorAll(".nav-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      const page = btn.dataset.page;
      showPage(page);
    });
  });
}

function showPage(name) {
  document.querySelectorAll(".page").forEach(p => p.classList.remove("active"));
  document.querySelectorAll(".nav-btn").forEach(b => b.classList.remove("active"));

  const page = document.getElementById(`page-${name}`);
  const btn = document.querySelector(`[data-page="${name}"]`);
  if (page) page.classList.add("active");
  if (btn) btn.classList.add("active");

  const titles = {
    dashboard: "Dashboard",
    charts: "Trading Charts",
    trades: "Trade History",
    sentiment: "Sentiment Analysis",
    ai: "AI Learning",
  };
  document.getElementById("page-title").textContent = titles[name] || name;

  // Initialise charts when their page becomes visible
  if (name === "dashboard") initDashChart();
  if (name === "charts") initMainChart();
  if (name === "ai") loadAIData();
}

// ── Bot Controls ───────────────────────────────────────────────
function setupBotControls() {
  const btn = document.getElementById("btn-bot-toggle");
  if (!btn) return;
  
  btn.addEventListener("click", async () => {
    const isRunning = state.bot_status.running;
    const strategy = document.getElementById("bot-strategy")?.value || "momentum_sentiment";
    const endpoint = isRunning ? "/api/bot/stop" : "/api/bot/start";
    const token = sessionStorage.getItem('bot_token');
    
    btn.disabled = true;
    try {
      const res = await fetch(API + endpoint, { 
        method: "POST", 
        body: JSON.stringify({ strategy, token }),
        headers: { "Content-Type": "application/json" }
      });
      if (res.status === 401 || res.status === 403) {
        alert("Unauthorized: Invalid or missing token.");
      }
      await refreshAll();
    } catch(e) {
      console.error("Bot toggle error:", e);
    } finally {
      btn.disabled = false;
    }
  });
}

function updateBotUI() {
  const btn = document.getElementById("btn-bot-toggle");
  const badge = document.getElementById("bot-status-badge");
  const strat = document.getElementById("bot-strategy");
  
  if (!btn) return;
  
  if (state.bot_status.running) {
    btn.textContent = "⏹ Stop Bot";
    btn.className = "btn-primary stop";
    if (badge) {
      badge.textContent = "RUNNING";
      badge.className = "badge badge-buy";
    }
    if (strat) strat.disabled = true;
  } else {
    btn.textContent = "▶ Start Bot";
    btn.className = "btn-primary start";
    if (badge) {
      badge.textContent = "STOPPED";
      badge.className = "badge badge-open";
    }
    if (strat) strat.disabled = false;
  }
}

function renderActivityFeed() {
  const el = document.getElementById("activity-feed");
  if (!el) return;
  
  if (!state.activity.length) {
    el.innerHTML = '<div style="color:var(--text-3);padding:1rem;text-align:center">No activity yet. Start the bot!</div>';
    return;
  }
  
  el.innerHTML = state.activity.map(a => {
    const d = new Date(a.time);
    const time = `${d.getHours().toString().padStart(2,'0')}:${d.getMinutes().toString().padStart(2,'0')}:${d.getSeconds().toString().padStart(2,'0')}`;
    return `
      <div class="log-entry">
        <span class="log-time">[${time}]</span>
        <span class="log-msg ${a.level || ''}">${a.message}</span>
      </div>
    `;
  }).reverse().join(""); // newest first
}

// ── API Helpers ────────────────────────────────────────────────
async function api(endpoint, params = {}) {
  const url = new URL(endpoint, window.location.origin);
  Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
  try {
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    return await res.json();
  } catch (e) {
    console.error(`API error [${endpoint}]:`, e);
    return null;
  }
}

async function loadConfig() {
  const cfg = await api("/api/config");
  if (cfg) {
    state.config = cfg;
    document.getElementById("exchange-name").textContent = cfg.exchange;
    const badge = document.getElementById("mode-badge");
    badge.textContent = cfg.mode.toUpperCase();
    if (cfg.mode === "live") badge.classList.add("live");

    // Hide controls if auth is required but missing token
    const token = sessionStorage.getItem('bot_token');
    const controls = document.querySelector('.bot-controls');
    if (cfg.auth_required && !token && controls) {
      controls.style.display = 'none';
    }
  }
}

function populateSymbolSelectors() {
  const selectors = ["dash-chart-symbol", "chart-symbol", "sentiment-symbol"];
  selectors.forEach(id => {
    const el = document.getElementById(id);
    if (!el) return;
    el.innerHTML = "";
    state.config.symbols.forEach(sym => {
      const opt = document.createElement("option");
      opt.value = sym;
      opt.textContent = sym.replace("/USDT", "");
      el.appendChild(opt);
    });
  });
}

// ── Refresh All ────────────────────────────────────────────────
async function refreshAll() {
  const [stats, trades, tickers, botStatus, activity] = await Promise.all([
    api("/api/stats"),
    api("/api/trades", { limit: 50 }),
    api("/api/tickers"),
    api("/api/bot/status"),
    api("/api/bot/activity"),
  ]);

  if (botStatus) { state.bot_status = botStatus; updateBotUI(); }
  if (activity) { state.activity = activity; renderActivityFeed(); }
  if (stats) { state.stats = stats; renderStats(stats); }
  if (trades) { 
    state.trades = trades; 
    renderDashOpenPositions(trades); 
    renderDashRecentHistory(trades); 
    renderTradesTable(trades); 
  }
  if (tickers) renderTickers(tickers);

  document.getElementById("last-update").textContent =
    new Date().toLocaleTimeString();
}

// ══════════════════════════════════════════════════════════════
// STATS CARDS
// ══════════════════════════════════════════════════════════════
function renderStats(s) {
  const pnlClass = s.total_pnl >= 0 ? "positive" : "negative";
  const pnlSign = s.total_pnl >= 0 ? "+" : "";

  document.getElementById("stats-grid").innerHTML = `
    <div class="stat-card">
      <div class="label">Portfolio</div>
      <div class="value">$${fmt(s.paper_balance)}</div>
      <div class="sub">Paper balance</div>
    </div>
    <div class="stat-card">
      <div class="label">Total P&L</div>
      <div class="value ${pnlClass}">${pnlSign}$${fmt(s.total_pnl)}</div>
      <div class="sub">${s.total_trades} closed trades</div>
    </div>
    <div class="stat-card">
      <div class="label">Win Rate</div>
      <div class="value">${s.win_rate}%</div>
      <div class="sub">${s.winning_trades}W / ${s.losing_trades}L</div>
    </div>
    <div class="stat-card">
      <div class="label">Avg Win</div>
      <div class="value positive">+$${fmt(s.avg_win)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Avg Loss</div>
      <div class="value negative">$${fmt(s.avg_loss)}</div>
    </div>
    <div class="stat-card">
      <div class="label">Open Positions</div>
      <div class="value">${s.open_positions}</div>
    </div>
  `;
}

// ══════════════════════════════════════════════════════════════
// TICKERS
// ══════════════════════════════════════════════════════════════
function renderTickers(tickers) {
  const strip = document.getElementById("ticker-strip");
  strip.innerHTML = tickers.map(t => {
    const pct = t.change_pct ?? 0;
    const cls = pct >= 0 ? "positive" : "negative";
    const sign = pct >= 0 ? "+" : "";
    return `
      <div class="ticker-item">
        <span class="name">${(t.symbol || "").replace("/USDT", "")}</span>
        <span class="price">$${fmtPrice(t.last)}</span>
        <span class="change ${cls}">${sign}${pct?.toFixed(1) ?? 0}%</span>
      </div>
    `;
  }).join("");
}

// ══════════════════════════════════════════════════════════════
// DASHBOARD ACTIVE POSITIONS
// ══════════════════════════════════════════════════════════════
function renderDashOpenPositions(trades) {
  const openTrades = trades.filter(t => t.status === "open");
  const elCount = document.getElementById("open-positions-count");
  if (elCount) elCount.textContent = openTrades.length;
  
  const el = document.getElementById("dash-open-positions");
  if (!el) return;
  
  if (!openTrades.length) {
    el.innerHTML = `<div class="loading" style="padding:1rem;">No active positions</div>`;
    return;
  }
  
  el.innerHTML = openTrades.map(t => {
    const sideCls = t.side === "BUY" ? "badge-buy" : "badge-sell";
    const amount = t.amount?.toFixed(6) ?? "";
    return `
      <div class="trade-row">
        <span class="sym">${t.symbol.replace("/USDT","")}</span>
        <span class="side badge ${sideCls}">${t.side}</span>
        <span class="amount" style="font-size:0.8rem;color:var(--text-3);">${amount}</span>
        <span class="pnl" style="font-weight:600;">@ $${fmtPrice(t.entry_price)}</span>
      </div>
    `;
  }).join("");
}

// ══════════════════════════════════════════════════════════════
// DASHBOARD RECENT HISTORY
// ══════════════════════════════════════════════════════════════
function renderDashRecentHistory(trades) {
  const closedTrades = trades.filter(t => t.status === "closed");
  const elCount = document.getElementById("recent-history-count");
  if (elCount) elCount.textContent = closedTrades.length;
  
  const el = document.getElementById("dash-recent-history");
  if (!el) return;
  
  if (!closedTrades.length) {
    el.innerHTML = `<div class="loading" style="padding:1rem;">No trading history yet</div>`;
    return;
  }
  
  el.innerHTML = closedTrades.slice(0, 15).map(t => {
    const sideCls = t.side === "BUY" ? "badge-buy" : "badge-sell";
    const pnl = t.pnl >= 0 ? `<span class="positive">+$${fmt(t.pnl)}</span>` : `<span class="negative">-$${fmt(Math.abs(t.pnl))}</span>`;
    const time = t.exit_time ? new Date(t.exit_time).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "";
    return `
      <div class="trade-row">
        <span class="sym">${t.symbol.replace("/USDT","")}</span>
        <span class="side badge ${sideCls}">${t.side}</span>
        <span class="pnl">${pnl}</span>
        <span class="time">${time}</span>
      </div>
    `;
  }).join("");
}

// ══════════════════════════════════════════════════════════════
// TRADES TABLE (full page)
// ══════════════════════════════════════════════════════════════
function renderTradesTable(trades) {
  const tbody = document.getElementById("trades-tbody");
  if (!tbody) return;
  tbody.innerHTML = trades.map(t => {
    const sideBadge = t.side === "BUY" ? "badge-buy" : "badge-sell";
    const statusBadge = t.status === "open" ? "badge-open" : "badge-closed";
    const pnl = t.pnl != null ? `<span class="${t.pnl >= 0 ? 'positive' : 'negative'}">${t.pnl >= 0 ? '+' : ''}$${fmt(t.pnl)}</span>` : "—";
    const pnlPct = t.pnl_pct != null ? `${t.pnl_pct >= 0 ? '+' : ''}${t.pnl_pct.toFixed(1)}%` : "—";
    const time = t.entry_time ? new Date(t.entry_time).toLocaleString() : "—";
    return `<tr>
      <td>${t.id}</td>
      <td style="font-weight:600">${t.symbol}</td>
      <td><span class="badge ${sideBadge}">${t.side}</span></td>
      <td>${t.amount?.toFixed(6) ?? "—"}</td>
      <td>$${fmtPrice(t.entry_price)}</td>
      <td>${t.exit_price ? '$' + fmtPrice(t.exit_price) : '—'}</td>
      <td>${t.stop_loss ? '$' + fmtPrice(t.stop_loss) : '—'}</td>
      <td>${t.take_profit ? '$' + fmtPrice(t.take_profit) : '—'}</td>
      <td>${pnl}</td>
      <td class="${t.pnl_pct >= 0 ? 'positive' : 'negative'}">${pnlPct}</td>
      <td>${t.strategy}</td>
      <td><span class="badge ${statusBadge}">${t.status}</span></td>
      <td style="color:var(--text-3);font-size:.72rem">${time}</td>
    </tr>`;
  }).join("");
}

// ── Trade filter ──
document.getElementById("trade-filter")?.addEventListener("change", async (e) => {
  const status = e.target.value || undefined;
  const trades = await api("/api/trades", { limit: 100, ...(status && { status }) });
  if (trades) renderTradesTable(trades);
});

// ══════════════════════════════════════════════════════════════
// CHARTS (TradingView Lightweight Charts)
// ══════════════════════════════════════════════════════════════
function createChart(containerId, options = {}) {
  const container = document.getElementById(containerId);
  if (!container) return null;

  // Clear previous chart
  container.innerHTML = "";

  const chart = LightweightCharts.createChart(container, {
    layout: {
      background: { type: "solid", color: "#111827" },
      textColor: "#94a3b8",
      fontFamily: "'Inter', sans-serif",
      fontSize: 11,
    },
    grid: {
      vertLines: { color: "rgba(255,255,255,.04)" },
      horzLines: { color: "rgba(255,255,255,.04)" },
    },
    crosshair: {
      mode: LightweightCharts.CrosshairMode.Normal,
      vertLine: { color: "rgba(167,139,250,.3)", width: 1, style: 2 },
      horzLine: { color: "rgba(167,139,250,.3)", width: 1, style: 2 },
    },
    rightPriceScale: {
      borderColor: "rgba(255,255,255,.06)",
    },
    timeScale: {
      borderColor: "rgba(255,255,255,.06)",
      timeVisible: true,
      secondsVisible: false,
    },
    ...options,
  });

  const candleSeries = chart.addCandlestickSeries({
    upColor: "#22c55e",
    downColor: "#ef4444",
    borderDownColor: "#ef4444",
    borderUpColor: "#22c55e",
    wickDownColor: "#ef444480",
    wickUpColor: "#22c55e80",
  });

  const volumeSeries = chart.addHistogramSeries({
    color: "rgba(167,139,250,.2)",
    priceFormat: { type: "volume" },
    priceScaleId: "",
  });
  volumeSeries.priceScale().applyOptions({
    scaleMargins: { top: 0.85, bottom: 0 },
  });

  // Resize observer
  const ro = new ResizeObserver(() => {
    chart.applyOptions({
      width: container.clientWidth,
      height: container.clientHeight,
    });
  });
  ro.observe(container);

  state.charts[containerId] = chart;
  state.chartSeries[containerId] = { candles: candleSeries, volume: volumeSeries };

  return chart;
}

async function loadChartData(containerId, symbol, timeframe = "1h") {
  const data = await api("/api/chart", { symbol, timeframe, limit: 300 });
  if (!data || !data.length) return;

  let series = state.chartSeries[containerId];
  if (!series) {
    createChart(containerId);
    series = state.chartSeries[containerId];
  }

  series.candles.setData(data.map(d => ({
    time: d.time,
    open: d.open,
    high: d.high,
    low: d.low,
    close: d.close,
  })));

  series.volume.setData(data.map(d => ({
    time: d.time,
    value: d.volume,
    color: d.close >= d.open ? "rgba(34,197,94,.15)" : "rgba(239,68,68,.15)",
  })));

  // Fit content
  state.charts[containerId]?.timeScale().fitContent();

  // Add trade markers
  addTradeMarkers(containerId);
}

function addTradeMarkers(containerId) {
  const series = state.chartSeries[containerId]?.candles;
  if (!series || !state.trades.length) return;

  const markers = state.trades
    .filter(t => t.entry_time)
    .map(t => ({
      time: Math.floor(new Date(t.entry_time).getTime() / 1000),
      position: t.side === "BUY" ? "belowBar" : "aboveBar",
      color: t.side === "BUY" ? "#22c55e" : "#ef4444",
      shape: t.side === "BUY" ? "arrowUp" : "arrowDown",
      text: `${t.side} $${fmtPrice(t.entry_price)}`,
    }))
    .sort((a, b) => a.time - b.time);

  if (markers.length) {
    try { series.setMarkers(markers); } catch(e) {}
  }
}

// ── Dashboard chart ──
function initDashChart() {
  const sym = document.getElementById("dash-chart-symbol")?.value || state.config.symbols[0];
  const tf = document.getElementById("dash-chart-tf")?.value || "1h";
  if (!state.charts["dash-chart"]) createChart("dash-chart");
  loadChartData("dash-chart", sym, tf);
}

document.getElementById("dash-chart-symbol")?.addEventListener("change", initDashChart);
document.getElementById("dash-chart-tf")?.addEventListener("change", initDashChart);

// ── Main chart page ──
function initMainChart() {
  const sym = document.getElementById("chart-symbol")?.value || state.config.symbols[0];
  const tf = document.getElementById("chart-tf")?.value || "1h";
  if (!state.charts["main-chart"]) createChart("main-chart");
  loadChartData("main-chart", sym, tf);
}

document.getElementById("chart-symbol")?.addEventListener("change", initMainChart);
document.getElementById("chart-tf")?.addEventListener("change", initMainChart);
document.getElementById("chart-refresh")?.addEventListener("click", initMainChart);

// ══════════════════════════════════════════════════════════════
// SENTIMENT
// ══════════════════════════════════════════════════════════════
document.getElementById("fetch-sentiment")?.addEventListener("click", async () => {
  const btn = document.getElementById("fetch-sentiment");
  const sym = document.getElementById("sentiment-symbol")?.value || state.config.symbols[0];

  btn.disabled = true;
  btn.textContent = "⏳ Fetching…";

  const grid = document.getElementById("sentiment-grid");
  grid.innerHTML = `<div class="sentiment-placeholder"><div class="loading"><div class="spinner"></div> Analysing news & social media for ${sym}…</div></div>`;

  const data = await api("/api/sentiment", { symbol: sym });

  btn.disabled = false;
  btn.textContent = "🔍 Fetch Live Sentiment";

  if (!data) {
    grid.innerHTML = `<div class="sentiment-placeholder"><p>Failed to fetch sentiment. Check your NEWS_API_KEY in .env</p></div>`;
    return;
  }

  renderSentiment(data, sym, grid);
});

function renderSentiment(data, symbol, grid) {
  const gaugeClass = data.score > 0.1 ? "positive" : data.score < -0.1 ? "negative" : "neutral";
  const directionEmoji = data.direction === "BUY" ? "📈" : data.direction === "SELL" ? "📉" : "➡️";

  let headlinesHtml = "";
  if (data.top_headlines && data.top_headlines.length) {
    headlinesHtml = data.top_headlines.map(h => {
      const match = h.match(/^\[([-\d.]+)\]\s*(.+)$/);
      if (match) {
        const score = parseFloat(match[1]);
        const cls = score > 0 ? "pos" : "neg";
        return `<div class="headline-item"><span class="headline-score ${cls}">${score > 0 ? '+' : ''}${score.toFixed(2)}</span>${match[2]}</div>`;
      }
      return `<div class="headline-item">${h}</div>`;
    }).join("");
  } else {
    headlinesHtml = `<div class="headline-item" style="color:var(--text-3)">No headlines available. Configure NEWS_API_KEY in .env for live data.</div>`;
  }

  grid.innerHTML = `
    <div class="card">
      <div class="card-header"><h3>${symbol} — Sentiment Gauge</h3></div>
      <div class="sentiment-gauge">
        <div class="gauge-ring ${gaugeClass}">
          ${(data.score >= 0 ? '+' : '')}${(data.score * 100).toFixed(0)}
        </div>
        <div class="sentiment-direction ${gaugeClass}">
          ${directionEmoji} ${data.direction}
        </div>
        <p style="color:var(--text-3);font-size:.82rem">
          Confidence: ${(data.confidence * 100).toFixed(0)}% · Samples: ${data.sample_size}
        </p>
      </div>
    </div>
    <div class="card">
      <div class="card-header"><h3>Top Headlines</h3></div>
      <div class="headline-list">${headlinesHtml}</div>
    </div>
  `;
}

// ══════════════════════════════════════════════════════════════
// AI LEARNING
// ══════════════════════════════════════════════════════════════
async function loadAIData() {
  const [weights, signals] = await Promise.all([
    api("/api/weights"),
    api("/api/signals", { limit: 30 }),
  ]);

  if (weights) renderWeights(weights);
  if (signals) renderSignals(signals);
}

function renderWeights(weights) {
  const panel = document.getElementById("weights-panel");
  if (!weights.length) {
    panel.innerHTML = `<div class="loading">No signal weights yet — start trading to build data</div>`;
    return;
  }

  panel.innerHTML = weights.map(w => {
    const pct = Math.min(w.weight / 3 * 100, 100);
    return `
      <div class="weight-item">
        <div class="weight-label">
          <span class="name">${w.source}</span>
          <span class="val">${w.weight.toFixed(3)}</span>
        </div>
        <div class="weight-bar"><div class="weight-bar-fill" style="width:${pct}%"></div></div>
        <div class="weight-meta">
          Accuracy: ${w.accuracy}% · Signals: ${w.total_signals}
          ${w.correct_signals > 0 ? `(${w.correct_signals} correct)` : ''}
        </div>
      </div>
    `;
  }).join("");
}

function renderSignals(signals) {
  const tbody = document.getElementById("signals-tbody");
  if (!tbody) return;

  if (!signals.length) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align:center;color:var(--text-3);padding:2rem">No signals recorded yet</td></tr>`;
    return;
  }

  tbody.innerHTML = signals.map(s => {
    const dirBadge = s.direction === "BUY" ? "badge-buy" : s.direction === "SELL" ? "badge-sell" : "badge-hold";
    const correct = s.was_correct === true ? "✅" : s.was_correct === false ? "❌" : "—";
    const pnl = s.outcome_pnl != null ? `<span class="${s.outcome_pnl >= 0 ? 'positive' : 'negative'}">$${fmt(s.outcome_pnl)}</span>` : "—";
    const time = s.created_at ? new Date(s.created_at).toLocaleString([], { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
    return `<tr>
      <td style="font-weight:600">${s.symbol}</td>
      <td>${s.source}</td>
      <td><span class="badge ${dirBadge}">${s.direction}</span></td>
      <td>${(s.confidence * 100).toFixed(0)}%</td>
      <td>${correct}</td>
      <td>${pnl}</td>
      <td style="color:var(--text-3);font-size:.72rem">${time}</td>
    </tr>`;
  }).join("");
}

// ══════════════════════════════════════════════════════════════
// UTILITIES
// ══════════════════════════════════════════════════════════════
function fmt(n) {
  if (n == null) return "0.00";
  return Math.abs(n).toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

function fmtPrice(n) {
  if (n == null || n === 0) return "—";
  if (n >= 1000) return n.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  if (n >= 1) return n.toFixed(4);
  return n.toFixed(6);
}

// ── Init the dashboard chart on load ──
setTimeout(initDashChart, 500);
