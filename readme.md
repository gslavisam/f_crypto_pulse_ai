# Crypto Pulse AI

Multi-asset quantitative analytics platform — decoupled desktop client (Flet) + REST backend (FastAPI).
Supports **4 instrument classes**: crypto, stocks, indices, commodities.
Combines live market data, technical indicators, AI-driven analysis, forecast tracking, and stored reports.

---

## Table of Contents

- [Features](#features)
- [Architecture](#architecture)
- [Data Sources](#data-sources)
- [AI Pipeline](#ai-pipeline)
- [Caching Strategy](#caching-strategy)
- [Database Schema](#database-schema)
- [API Reference](#api-reference)
- [Installation & Run](#installation--run)
- [Environment Variables](#environment-variables)
- [Disclaimer](#disclaimer)

---

## Features

| Tab | Description |
| :--- | :--- |
| **Home** | Live dashboard — global market stats, top instruments ranked by type, dropdown to switch between crypto / stock / index / commodity |
| **Details** | Per-asset deep-dive — price, 30d trend, RSI14, SMA20, EMA12/26, MACD, Bollinger Bands, L2 order book, Polymarket odds, Google News sentiment, premium RSS, full AI analysis (stance, catalysts, recommendation, 7D forecast) |
| **Tracker** | Forecast watchlist — add from Details, monitor actual 7d price history vs AI forecast, entry price, target, bull/bear, confidence, days remaining |
| **Chat** | Conversational AI with optional Tracker portfolio context |
| **Reports** | Run full Financial Agent pipeline on any instrument, store to DB, view with full Markdown rendering |

### Key Capabilities

- **Asset-type-aware** across all layers — data fetching, news queries, cache keys, LLM prompts
- **LLM forecast sanity check** — clamps expected/bull/bear to 50–200% of current price, prevents hallucinated historical prices
- **Structured JSON output** enforced on every LLM call, with smart model fallback chain (OpenRouter free tier)
- **Full Markdown rendering** in Reports viewer (GitHub Flavored + Atom One Dark code theme)
- **7-day actual price tracking** in Tracker with real API data per asset fetched in parallel (asyncio.gather)
- **AI cache key stability** — price rounded to 2 significant figures prevents cache miss on minor fluctuations

---

## Architecture

```
+------------------------------------------+
|            Flet Desktop UI               |
|  main.py — 4 tabs, async handlers        |
|  Home / Tracker / Chat / Reports         |
+------------------+-----------------------+
                   | HTTP (aiohttp)
                   v
+------------------------------------------+
|          FastAPI Backend                 |
|  api.py — REST routes + Pydantic models  |
|  Port 8000                               |
+------+------------------+----------------+
       |                  |
       v                  v
+-----------+    +------------------------+
| SQLite DB |    |   external_apis.py     |
| SQLAlchemy|    |                        |
| models.py |    |  CryptoDataProvider    |
| db_service|    |  MultiAssetDataProvider|
|           |    |  AIServiceProvider     |
| 6 tables  |    |  FinancialAgentService |
+-----------+    |  PolymarketProvider    |
                 |  PremiumNewsProvider   |
                 +----------+-------------+
                            |
           +----------------+---------------+
           v                v               v
      CoinGecko         yfinance       OpenRouter
      Binance L2       Google News      / Groq
      Polymarket       CoinDesk RSS    / LM Studio
      Alt.me F&G       TheBlock RSS
```

### File Map

```
f_crypto_pulse_ai/
├── main.py            # Flet UI — all 4 tabs, async event handlers
├── api.py             # FastAPI routes + Pydantic request models
├── external_apis.py   # All data providers + AI service + indicator math
├── db_service.py      # DB service layer (CRUD + TTL logic per table)
├── models.py          # SQLAlchemy ORM models
├── cache_manager.py   # In-memory SmartCache (TTL dict)
├── requirements.txt
└── .env               # API keys + active provider selection
```

---

## Data Sources

| Source | What it provides | Used in |
| :--- | :--- | :--- |
| **CoinGecko** | Crypto prices, market cap, 30d/7d OHLCV, global stats, trending | Home, Details, Reports |
| **yfinance** | OHLCV for stocks, indices, commodities (3mo history) | Details, Reports |
| **Binance REST** | L2 order book (top 5 bids/asks) | Details |
| **Polymarket CLOB** | Prediction market odds for crypto | Details |
| **Google News RSS** | News headlines + sentiment, query adapted per asset type | Details, Reports |
| **CoinDesk RSS** | Premium crypto news | Details |
| **TheBlock RSS** | Premium institutional crypto news | Details |
| **CoinTelegraph RSS** | Premium community crypto news | Details |
| **Alternative.me** | Fear & Greed Index | Home |

### News Query Adaptation

Google News query suffix is adapted per asset type to avoid cross-asset noise:

| Asset type | Query suffix added |
| :--- | :--- |
| `crypto` | `cryptocurrency price` |
| `stock` | `stock earnings market` |
| `index` | `stock market index` |
| `commodity` | `commodity futures price` |

---

## AI Pipeline

### Details Page (`/api/ai/analysis`)

1. Fetch 30d price history → compute pure-Python indicators (`calc_indicators`)
2. Fetch L2 order book (Binance REST)
3. Fetch Polymarket odds (SmartCache 3h)
4. Fetch premium RSS news — CoinDesk, TheBlock, CoinTelegraph
5. Fetch Google News + sentiment (SmartCache 6h + DB cache 6h)
6. Build structured LLM prompt anchored to current price — forecast examples use real price math
7. Call LLM → parse JSON → sanity-clamp forecast to 50–200% of current price
8. Cache result in SmartCache (7 min, key = asset + price rounded to 2 sig. figs.)
9. Write news sentiment back to `NewsSentimentCache` DB

**Indicators (pure Python, no pandas):**

| Indicator | Window |
| :--- | :--- |
| RSI | 14 periods |
| SMA | 20 periods |
| EMA | 12 and 26 periods |
| MACD | EMA12 − EMA26 |
| Bollinger Bands | SMA20 ± 2×StdDev |

### Reports Tab (`/api/analysis/run`)

Richer pipeline via `FinancialAgentService` + pandas/yfinance:

| Indicator | Details |
| :--- | :--- |
| SMA_20 | 20-period simple MA |
| EMA_20 | 20-period exponential MA |
| RSI_14 | 14-period RSI |
| MACD | 12/26 EMA diff |
| Bollinger Bands | upper/lower |
| Stochastic %K | 14-period |
| Williams %R | 14-period |

### LLM Providers

Configured via `ACTIVE_AI_PROVIDER` env var:

| Provider | Notes |
| :--- | :--- |
| `openrouter` | Default. Tries `openrouter/free` → `llama-3-8b-instruct:free` → `gemma-2-9b-it:free` |
| `groq` | Single model from `GROQ_MODEL` env var |
| `lmstudio` | Local server — fully offline inference |

---

## Caching Strategy

Two-tier cache system — RAM first, SQLite second.

### Tier 1 — SmartCache (in-memory, `cache_manager.py`)

| Key pattern | TTL | Data |
| :--- | :--- | :--- |
| `global_stats_{type}` | 60s | Global market stats |
| `top_assets_{type}_{limit}` | 60s | Top instruments list |
| `news_{type}_{asset}` | 6h | Google News sentiment |
| `poly_{asset}` | 3h | Polymarket odds |
| `ai_{md5(name+price)}` | 7 min | Full AI analysis JSON |
| `run_{TICKER}_{type}` | 15 min | Financial Agent report |

> Cache key for AI analysis uses price rounded to 2 significant figures.
> Example: $201.73 and $201.75 → same bucket → same cache hit.

### Tier 2 — SQLite DB Cache (`db_service.py`)

| Table | TTL | Data |
| :--- | :--- | :--- |
| `price_history_cache` | 12h | OHLCV history arrays |
| `news_sentiment_cache` | 6h | Sentiment score + headlines |
| `asset_indicator_cache` | 4h | Full indicator set + price series |
| `dashboard_cache` | 5 min | Dashboard stats snapshot |

---

## Database Schema

```
forecast_snapshots         Tracker watchlist entries
  id, asset_id, asset_name, ticker, asset_type
  current_price, expected_price_7d, bull_case, bear_case
  model_confidence, tracked_at

market_analysis_reports    Stored Financial Agent reports
  id, instrument_name, asset_type, ticker
  current_price, report_text (Markdown), ai_provider, created_at

asset_indicator_cache      Technical indicators per asset (TTL 4h)
  cache_key, asset_id, asset_type
  current_price, change_percent
  price_series (JSON), indicators (JSON)
  cached_at, expires_at

price_history_cache        OHLCV history arrays (TTL 12h)
  cache_key, history_data (JSON), cached_at, expires_at

news_sentiment_cache       Google News results (TTL 6h)
  asset_id, sentiment_score, sentiment_category
  news_headlines (JSON), cached_at, expires_at

dashboard_cache            Dashboard snapshot (TTL 5 min)
  cache_key, data (JSON), cached_at, expires_at

daily_price_records        Reserved for future daily tracking
  forecast_snapshot_id, asset_id, day_offset
  closing_price, daily_average, recorded_date
```

---

## API Reference

### Market

| Method | Route | Description |
| :--- | :--- | :--- |
| GET | `/api/market/global?asset_type=` | Global stats — market cap, volume, Fear & Greed |
| GET | `/api/market/top?limit=&asset_type=` | Top N instruments by type |
| GET | `/api/asset/{id}` | Single asset details |
| GET | `/api/asset/{id}/history?days=&asset_type=` | Price history (DB-cached 12h) |

### AI

| Method | Route | Description |
| :--- | :--- | :--- |
| POST | `/api/ai/analysis` | Full AI analysis for Details page (SmartCache 7 min) |
| POST | `/api/chat` | Chat assistant |

`POST /api/ai/analysis` body:
```json
{
  "asset_name": "NVIDIA Corporation",
  "ticker": "NVDA",
  "asset_type": "stock",
  "current_price": 201.73,
  "change_24h": -0.28,
  "history": [{"date": "01 Apr", "price": 195.10}]
}
```

Response:
```json
{
  "news_sentiment": {"score": "NEUTRAL", "headline": "..."},
  "stance": "BULLISH",
  "summary": "...",
  "catalysts": ["..."],
  "recommendation": "BUY",
  "risk_level": "MEDIUM",
  "forecast": {"expected": 208.50, "bull": 221.90, "bear": 185.60, "confidence": 72}
}
```

### Reports

| Method | Route | Description |
| :--- | :--- | :--- |
| POST | `/api/analysis/run` | Run full pipeline + optionally save |
| GET | `/api/analysis` | List all saved reports |
| GET | `/api/analysis/{id}` | Full report text (Markdown) |
| DELETE | `/api/analysis/{id}` | Delete report |

`POST /api/analysis/run` body:
```json
{
  "instrument_name": "gold",
  "asset_type": "commodity",
  "ticker": "GC=F",
  "save": true
}
```

### Tracker

| Method | Route | Description |
| :--- | :--- | :--- |
| GET | `/api/tracker` | List all tracked assets |
| POST | `/api/tracker` | Add asset snapshot |
| DELETE | `/api/tracker/{id}` | Remove tracked asset |

---

## Installation & Run

```powershell
cd d:\py_radno\f_crypto_pulse_ai
d:\py_radno\.venv\Scripts\pip.exe install -r requirements.txt
```

**Terminal 1 — Backend:**
```powershell
cd d:\py_radno\f_crypto_pulse_ai
d:\py_radno\.venv\Scripts\uvicorn.exe api:app --reload --port 8000
```

**Terminal 2 — Frontend:**
```powershell
cd d:\py_radno\f_crypto_pulse_ai
d:\py_radno\.venv\Scripts\python.exe main.py
```

Interactive API docs: http://127.0.0.1:8000/docs

---

## Environment Variables

Minimum `.env` (OpenRouter free tier, no credit card):

```env
ACTIVE_AI_PROVIDER=openrouter
OPENROUTER_API_KEY=sk-or-...
```

Local LM Studio (fully offline):

```env
ACTIVE_AI_PROVIDER=lmstudio
LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
LMSTUDIO_MODEL=local-model
LMSTUDIO_API_KEY=lm-studio
```

All supported variables:

```env
ACTIVE_AI_PROVIDER=openrouter   # openrouter | groq | lmstudio

OPENROUTER_API_KEY=...
GROQ_API_KEY=...
GROQ_MODEL=llama3-8b-8192
OPENAI_API_KEY=...
ZAI_API_KEY=...

LMSTUDIO_BASE_URL=http://127.0.0.1:1234/v1
LMSTUDIO_MODEL=local-model
LMSTUDIO_API_KEY=lm-studio
```

---

## Disclaimer

Educational and research project only. Not financial advice. All analysis is generated by AI models and may be inaccurate. Do not use for real investment decisions.
