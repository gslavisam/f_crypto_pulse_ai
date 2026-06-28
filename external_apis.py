"""External API integrations for crypto data and AI services."""

import os
import json
import asyncio
import math
import aiohttp
import xml.etree.ElementTree as ET
import urllib.parse
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI
from dotenv import load_dotenv
from datetime import datetime
import time
import yfinance as yf
from cache_manager import market_cache

load_dotenv()

# Lazy import da izbegnemo circular import (models → external_apis)
def _get_indicator_cache_service():
    from db_service import AssetIndicatorCacheService
    from models import SessionLocal
    return AssetIndicatorCacheService, SessionLocal
#openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))
# --- INICIJALIZACIJA KLIJENATA ---
openai_client = None
if os.getenv("OPENAI_API_KEY"):
    openai_client = AsyncOpenAI(api_key=os.getenv("OPENAI_API_KEY"))

groq_client = None
if os.getenv("GROQ_API_KEY"):
    # Groq koristi identičan standard kao OpenAI, samo menjamo adresu
    groq_client = AsyncOpenAI(
        api_key=os.getenv("GROQ_API_KEY"),
        base_url="https://api.groq.com/openai/v1"
    )

zai_client = None
if os.getenv("ZAI_API_KEY"):
    # Zhipu AI takođe koristi OpenAI standard
    zai_client = AsyncOpenAI(
        api_key=os.getenv("ZAI_API_KEY"),
        base_url="https://api.z.ai/api/paas/v4/" # Zvanični Z.AI base URL
    )

# NOVI OPENROUTER KLIJENT
openrouter_client = None
if os.getenv("OPENROUTER_API_KEY"):
    openrouter_client = AsyncOpenAI(
        api_key=os.getenv("OPENROUTER_API_KEY"),
        base_url="https://openrouter.ai/api/v1"
    )

# LM STUDIO KLIJENT (lokalni model - OpenAI-kompatibilan API)
LMSTUDIO_BASE_URL = os.getenv("LMSTUDIO_BASE_URL", "http://127.0.0.1:1234/v1")
LMSTUDIO_MODEL = os.getenv("LMSTUDIO_MODEL", "local-model")
lmstudio_client = None
# Inicijalizujemo uvek - server možda još nije pokrenut, ali klijent će baciti grešku tek pri pozivu
lmstudio_client = AsyncOpenAI(
    api_key=os.getenv("LMSTUDIO_API_KEY", "lm-studio"),
    base_url=LMSTUDIO_BASE_URL,
)

# Čitamo koji je provajder aktiviran u .env (default je groq)
ACTIVE_PROVIDER = os.getenv("ACTIVE_AI_PROVIDER", "openrouter").lower()

class CryptoDataProvider:
    """Pruža podatke o kriptovalutama sa pravog CoinGecko API-ja."""
    
    COINGECKO_BASE_URL = "https://api.coingecko.com/api/v3"
    
    @staticmethod
    async def fetch_json(url: str, params: dict = None) -> Optional[Dict]:
        async with aiohttp.ClientSession() as session:
            headers = {"User-Agent": "CryptoPulseAI/1.0", "Accept": "application/json"}
            try:
                async with session.get(url, params=params, headers=headers, timeout=10) as response:
                    if response.status == 200:
                        return await response.json()
                    return None
            except Exception:
                return None

    @staticmethod
    async def get_global_market_data() -> Dict:
        cg_data = await CryptoDataProvider.fetch_json(f"{CryptoDataProvider.COINGECKO_BASE_URL}/global")
        fg_data = await CryptoDataProvider.fetch_json("https://api.alternative.me/fng/")
        
        result = {"total_market_cap": 2520000000000, "total_volume_24h": 102000000000, "fear_greed_index": 50, "fear_greed_label": "Neutral"}
        
        if cg_data and "data" in cg_data:
            result["total_market_cap"] = cg_data["data"]["total_market_cap"].get("usd", result["total_market_cap"])
            result["total_volume_24h"] = cg_data["data"]["total_volume"].get("usd", result["total_volume_24h"])
            
        if fg_data and "data" in fg_data and len(fg_data["data"]) > 0:
            result["fear_greed_index"] = int(fg_data["data"][0].get("value", 50))
            result["fear_greed_label"] = fg_data["data"][0].get("value_classification", "Neutral")
            
        return result

    @staticmethod
    async def get_top_assets(limit: int = 15) -> List[Dict]:
        params = {"vs_currency": "usd", "order": "market_cap_desc", "per_page": limit, "page": 1, "sparkline": "false"}
        data = await CryptoDataProvider.fetch_json(f"{CryptoDataProvider.COINGECKO_BASE_URL}/coins/markets", params)
        if not data: return []
            
        formatted = []
        for coin in data:
            formatted.append({
                "id": coin.get("id", ""),
                "name": coin.get("name", ""),
                "ticker": coin.get("symbol", "").upper(),
                "current_price": coin.get("current_price", 0),
                "market_cap": coin.get("market_cap", 0),
                "volume_24h": coin.get("total_volume", 0),
                "change_24h": coin.get("price_change_percentage_24h", 0),
                "icon": "🪙" 
            })
        return formatted

    @staticmethod
    async def get_trending_assets() -> List[Dict]:
        data = await CryptoDataProvider.fetch_json(f"{CryptoDataProvider.COINGECKO_BASE_URL}/search/trending")
        if not data or "coins" not in data: return []
            
        trending = []
        for item in data["coins"][:5]:
            coin = item["item"]
            trending.append({
                "id": coin.get("id", ""),
                "name": coin.get("name", ""),
                "ticker": coin.get("symbol", "").upper(),
                "current_price": coin.get("data", {}).get("price", 0),
                "change_24h": coin.get("data", {}).get("price_change_percentage_24h", {}).get("usd", 0),
                "icon": "🔥"
            })
        return trending

    @staticmethod
    async def get_asset_details(asset_id: str) -> Optional[Dict]:
        params = {"localization": "false", "tickers": "false", "community_data": "false", "developer_data": "false"}
        data = await CryptoDataProvider.fetch_json(f"{CryptoDataProvider.COINGECKO_BASE_URL}/coins/{asset_id}", params)
        if not data: return {"name": asset_id.capitalize(), "current_price": 0, "change_24h": 0}
            
        return {
            "id": data.get("id", ""),
            "name": data.get("name", ""),
            "ticker": data.get("symbol", "").upper(),
            "current_price": data.get("market_data", {}).get("current_price", {}).get("usd", 0),
            "change_24h": data.get("market_data", {}).get("price_change_percentage_24h", 0)
        }

    @staticmethod
    async def get_price_history(asset_id: str, days: int = 7) -> List[Dict]:
        """Preuzima istorijske podatke za Reflex Recharts grafikon."""
        url = f"{CryptoDataProvider.COINGECKO_BASE_URL}/coins/{asset_id}/market_chart"
        params = {"vs_currency": "usd", "days": days}
        data = await CryptoDataProvider.fetch_json(url, params)
        
        if not data or "prices" not in data: return []
            
        history = []
        for item in data["prices"]:
            dt = datetime.fromtimestamp(item[0]/1000)
            date_str = dt.strftime("%d %b %H:%M") if days <= 7 else dt.strftime("%d %b")
            history.append({"date": date_str, "price": item[1]})
            
        # Uzimamo svaki N-ti element da ne bismo preopteretili grafikon (max 60 tačaka)
        step = max(1, len(history) // 60)
        return history[::step]


class MultiAssetDataProvider:
    """Unified data provider for crypto, stocks/indices and commodities."""

    STOCK_ICONS = {
        "stock": "📈",
        "index": "📊",
        "commodity": "🥇",
        "crypto": "🪙",
    }

    INSTRUMENT_ALIASES = {
        "btc": ("crypto", "bitcoin"),
        "bitcoin": ("crypto", "bitcoin"),
        "eth": ("crypto", "ethereum"),
        "ethereum": ("crypto", "ethereum"),
        "sol": ("crypto", "solana"),
        "solana": ("crypto", "solana"),
        "xrp": ("crypto", "ripple"),
        "ripple": ("crypto", "ripple"),
        "gold": ("commodity", "GC=F"),
        "zlato": ("commodity", "GC=F"),
        "silver": ("commodity", "SI=F"),
        "oil": ("commodity", "CL=F"),
        "nafta": ("commodity", "CL=F"),
        "sp500": ("index", "^GSPC"),
        "s&p500": ("index", "^GSPC"),
        "nasdaq": ("index", "^IXIC"),
        "dow": ("index", "^DJI"),
        "nvda": ("stock", "NVDA"),
        "nvidia": ("stock", "NVDA"),
        "msft": ("stock", "MSFT"),
        "aapl": ("stock", "AAPL"),
        "tsla": ("stock", "TSLA"),
        "amzn": ("stock", "AMZN"),
    }

    DEFAULT_WATCHLIST = {
        "crypto": ["bitcoin", "ethereum", "solana", "ripple", "cardano", "dogecoin"],
        "stock": ["NVDA", "MSFT", "AAPL", "AMZN", "GOOG", "META"],
        "index": ["^GSPC", "^IXIC", "^DJI", "^GDAXI", "^FTSE"],
        "commodity": ["GC=F", "SI=F", "CL=F", "NG=F", "HG=F"],
    }

    @staticmethod
    def _safe_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except Exception:
            return default

    @staticmethod
    async def get_global_market_data(asset_type: str = "crypto") -> Dict[str, Any]:
        normalized_type = (asset_type or "crypto").lower()

        if normalized_type == "crypto":
            crypto = await CryptoDataProvider.get_global_market_data()
            btc_dom = 50.5

            cg_data = await CryptoDataProvider.fetch_json(f"{CryptoDataProvider.COINGECKO_BASE_URL}/global")
            if cg_data and "data" in cg_data:
                btc_dom = MultiAssetDataProvider._safe_float(
                    cg_data.get("data", {}).get("market_cap_percentage", {}).get("btc", btc_dom),
                    50.5,
                )

            fg = int(crypto.get("fear_greed_index", 50) or 50)
            fg_label = crypto.get("fear_greed_label", "Neutral")
            fg_color = "GREEN_400" if fg > 50 else ("RED_400" if fg < 40 else "AMBER_400")

            return {
                "asset_type": "crypto",
                "cards": [
                    {
                        "title": "Market Cap",
                        "value": MultiAssetDataProvider._safe_float(crypto.get("total_market_cap", 0.0)),
                        "subtitle": "Global",
                        "icon": "PIE_CHART",
                        "color": "BLUE_400",
                        "format": "currency_trillion",
                    },
                    {
                        "title": "Volume 24h",
                        "value": MultiAssetDataProvider._safe_float(crypto.get("total_volume_24h", 0.0)),
                        "subtitle": "Total",
                        "icon": "BAR_CHART",
                        "color": "PURPLE_400",
                        "format": "currency_billion",
                    },
                    {
                        "title": "BTC Dom",
                        "value": btc_dom,
                        "subtitle": "Market Share",
                        "icon": "CURRENCY_BITCOIN",
                        "color": "ORANGE_400",
                        "format": "percent",
                    },
                    {
                        "title": "Fear & Greed",
                        "value": fg,
                        "subtitle": fg_label,
                        "icon": "LOCAL_FIRE_DEPARTMENT",
                        "color": fg_color,
                        "format": "integer",
                    },
                ],
            }

        assets = await MultiAssetDataProvider.get_top_assets(normalized_type, 10)
        if not assets:
            return {
                "asset_type": normalized_type,
                "cards": [
                    {
                        "title": "Market Cap",
                        "value": 0,
                        "subtitle": "No data",
                        "icon": "PIE_CHART",
                        "color": "BLUE_400",
                        "format": "currency_trillion",
                    },
                    {
                        "title": "Volume",
                        "value": 0,
                        "subtitle": "No data",
                        "icon": "BAR_CHART",
                        "color": "PURPLE_400",
                        "format": "currency_billion",
                    },
                    {
                        "title": "Leader Move",
                        "value": 0,
                        "subtitle": "No data",
                        "icon": "SHOW_CHART",
                        "color": "AMBER_400",
                        "format": "percent_signed",
                    },
                    {
                        "title": "Breadth",
                        "value": 0,
                        "subtitle": "No data",
                        "icon": "INSIGHTS",
                        "color": "GREY_400",
                        "format": "integer",
                    },
                ],
            }

        market_caps = [MultiAssetDataProvider._safe_float(a.get("market_cap", 0.0)) for a in assets]
        volumes = [MultiAssetDataProvider._safe_float(a.get("volume_24h", 0.0)) for a in assets]
        changes = [MultiAssetDataProvider._safe_float(a.get("change_24h", 0.0)) for a in assets]

        total_cap = sum(market_caps)
        total_vol = sum(volumes)
        avg_change = sum(changes) / len(changes) if changes else 0.0
        leader_idx = max(range(len(assets)), key=lambda i: abs(changes[i]))
        leader = assets[leader_idx]
        leader_change = changes[leader_idx]
        positives = sum(1 for c in changes if c >= 0)
        breadth_pct = int(round((positives / len(changes)) * 100)) if changes else 0

        concentration = 0.0
        if total_cap > 0:
            top_cap = max(market_caps)
            concentration = (top_cap / total_cap) * 100

        sentiment_index = 50 + max(-40, min(40, avg_change * 2.5))

        if normalized_type == "stock":
            return {
                "asset_type": "stock",
                "cards": [
                    {
                        "title": "Market Cap",
                        "value": total_cap,
                        "subtitle": "Selected Basket",
                        "icon": "PIE_CHART",
                        "color": "BLUE_400",
                        "format": "currency_trillion",
                    },
                    {
                        "title": "Volume",
                        "value": total_vol,
                        "subtitle": "Session Proxy",
                        "icon": "BAR_CHART",
                        "color": "PURPLE_400",
                        "format": "currency_billion",
                    },
                    {
                        "title": "Concentration",
                        "value": concentration,
                        "subtitle": "Top Weight",
                        "icon": "DONUT_SMALL",
                        "color": "ORANGE_400",
                        "format": "percent",
                    },
                    {
                        "title": "Risk Mood",
                        "value": sentiment_index,
                        "subtitle": "Breadth + Move",
                        "icon": "PSYCHOLOGY",
                        "color": "GREEN_400" if sentiment_index >= 55 else ("RED_400" if sentiment_index <= 45 else "AMBER_400"),
                        "format": "integer",
                    },
                ],
            }

        if normalized_type == "index":
            return {
                "asset_type": "index",
                "cards": [
                    {
                        "title": "Aggregate Cap",
                        "value": total_cap,
                        "subtitle": "Index Basket",
                        "icon": "PIE_CHART",
                        "color": "BLUE_400",
                        "format": "currency_trillion",
                    },
                    {
                        "title": "Volume Proxy",
                        "value": total_vol,
                        "subtitle": "Index Components",
                        "icon": "BAR_CHART",
                        "color": "PURPLE_400",
                        "format": "currency_billion",
                    },
                    {
                        "title": "Leader Move",
                        "value": leader_change,
                        "subtitle": leader.get("ticker", "Leader"),
                        "icon": "SHOW_CHART",
                        "color": "GREEN_400" if leader_change >= 0 else "RED_400",
                        "format": "percent_signed",
                    },
                    {
                        "title": "Breadth",
                        "value": breadth_pct,
                        "subtitle": "Advancers",
                        "icon": "INSIGHTS",
                        "color": "GREEN_400" if breadth_pct >= 55 else ("RED_400" if breadth_pct <= 45 else "AMBER_400"),
                        "format": "percent",
                    },
                ],
            }

        # commodity
        return {
            "asset_type": "commodity",
            "cards": [
                {
                    "title": "Basket Value",
                    "value": sum(MultiAssetDataProvider._safe_float(a.get("current_price", 0.0)) for a in assets),
                    "subtitle": "Tracked Futures",
                    "icon": "PIE_CHART",
                    "color": "BLUE_400",
                    "format": "currency",
                },
                {
                    "title": "Volume Proxy",
                    "value": total_vol,
                    "subtitle": "Session Total",
                    "icon": "BAR_CHART",
                    "color": "PURPLE_400",
                    "format": "currency_billion",
                },
                {
                    "title": "Top Mover",
                    "value": leader_change,
                    "subtitle": leader.get("ticker", "Leader"),
                    "icon": "SHOW_CHART",
                    "color": "GREEN_400" if leader_change >= 0 else "RED_400",
                    "format": "percent_signed",
                },
                {
                    "title": "Momentum",
                    "value": 50 + max(-40, min(40, avg_change * 3.0)),
                    "subtitle": "Basket Average",
                    "icon": "SPEED",
                    "color": "GREEN_400" if avg_change > 0 else ("RED_400" if avg_change < 0 else "AMBER_400"),
                    "format": "integer",
                },
            ],
        }

    @staticmethod
    def resolve_asset(asset_id: str, asset_type: str = "crypto") -> Dict[str, str]:
        key = (asset_id or "").strip().lower()
        if key in MultiAssetDataProvider.INSTRUMENT_ALIASES:
            resolved_type, resolved_id = MultiAssetDataProvider.INSTRUMENT_ALIASES[key]
            return {"asset_type": resolved_type, "asset_id": resolved_id}

        inferred_type = asset_type or "crypto"
        upper = asset_id.strip().upper()
        if upper.startswith("^"):
            inferred_type = "index"
        elif "=F" in upper:
            inferred_type = "commodity"
        elif inferred_type != "crypto":
            inferred_id = upper
            return {"asset_type": inferred_type, "asset_id": inferred_id}

        return {
            "asset_type": inferred_type,
            "asset_id": asset_id if inferred_type == "crypto" else upper,
        }

    @staticmethod
    async def get_top_assets(asset_type: str = "crypto", limit: int = 10) -> List[Dict[str, Any]]:
        normalized_type = (asset_type or "crypto").lower()
        if normalized_type == "crypto":
            assets = await CryptoDataProvider.get_top_assets(limit)
            for item in assets:
                item["asset_type"] = "crypto"
            return assets

        tickers = MultiAssetDataProvider.DEFAULT_WATCHLIST.get(normalized_type, [])[:limit]
        assets = await MultiAssetDataProvider._get_yfinance_assets(tickers, normalized_type)

        if normalized_type == "commodity":
            assets.sort(key=lambda x: abs(MultiAssetDataProvider._safe_float(x.get("change_24h", 0.0))), reverse=True)
            return assets

        if normalized_type in {"stock", "index"}:
            def relevance(item: Dict[str, Any]) -> float:
                cap = max(0.0, MultiAssetDataProvider._safe_float(item.get("market_cap", 0.0)))
                vol = max(0.0, MultiAssetDataProvider._safe_float(item.get("volume_24h", 0.0)))
                chg = abs(MultiAssetDataProvider._safe_float(item.get("change_24h", 0.0)))
                return (math.log10(cap + 1.0) * 0.55) + (math.log10(vol + 1.0) * 0.30) + (chg * 0.15)

            assets.sort(key=relevance, reverse=True)

        return assets

    @staticmethod
    async def get_asset_details(asset_id: str, asset_type: str = "crypto") -> Dict[str, Any]:
        resolved = MultiAssetDataProvider.resolve_asset(asset_id, asset_type)
        normalized_type = resolved["asset_type"]
        normalized_id = resolved["asset_id"]

        if normalized_type == "crypto":
            details = await CryptoDataProvider.get_asset_details(normalized_id)
            if details:
                details["asset_type"] = "crypto"
            return details

        return await MultiAssetDataProvider._get_yfinance_asset_details(normalized_id, normalized_type)

    @staticmethod
    async def get_price_history(asset_id: str, asset_type: str = "crypto", days: int = 7) -> List[Dict[str, Any]]:
        resolved = MultiAssetDataProvider.resolve_asset(asset_id, asset_type)
        normalized_type = resolved["asset_type"]
        normalized_id = resolved["asset_id"]

        if normalized_type == "crypto":
            return await CryptoDataProvider.get_price_history(normalized_id, days)

        return await MultiAssetDataProvider._get_yfinance_history(normalized_id, days)

    @staticmethod
    async def _get_yfinance_assets(tickers: List[str], asset_type: str) -> List[Dict[str, Any]]:
        if not tickers:
            return []

        tasks = [MultiAssetDataProvider._get_yfinance_asset_details(t, asset_type) for t in tickers]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        formatted = []
        for item in results:
            if isinstance(item, Exception):
                continue
            if isinstance(item, dict) and item.get("name"):
                formatted.append(item)
        return formatted

    @staticmethod
    async def _get_yfinance_asset_details(ticker: str, asset_type: str) -> Dict[str, Any]:
        loop = asyncio.get_event_loop()

        def fetch() -> Dict[str, Any]:
            instrument = yf.Ticker(ticker)
            hist = instrument.history(period="7d")
            if hist is None or hist.empty:
                return {
                    "id": ticker,
                    "name": ticker,
                    "ticker": ticker,
                    "current_price": 0.0,
                    "change_24h": 0.0,
                    "icon": MultiAssetDataProvider.STOCK_ICONS.get(asset_type, "📈"),
                    "asset_type": asset_type,
                }

            current_price = float(hist["Close"].iloc[-1])
            prev_close = float(hist["Close"].iloc[-2]) if len(hist) > 1 else current_price
            change = ((current_price - prev_close) / prev_close) * 100 if prev_close else 0.0

            info = instrument.info or {}
            name = info.get("longName") or info.get("shortName") or ticker
            volume = float(hist["Volume"].iloc[-1]) if "Volume" in hist.columns else 0.0

            return {
                "id": ticker,
                "name": name,
                "ticker": ticker,
                "current_price": current_price,
                "market_cap": float(info.get("marketCap") or 0),
                "volume_24h": volume,
                "change_24h": change,
                "icon": MultiAssetDataProvider.STOCK_ICONS.get(asset_type, "📈"),
                "asset_type": asset_type,
            }

        return await loop.run_in_executor(None, fetch)

    @staticmethod
    async def _get_yfinance_history(ticker: str, days: int = 7) -> List[Dict[str, Any]]:
        loop = asyncio.get_event_loop()

        def fetch() -> List[Dict[str, Any]]:
            if days >= 60:
                period = "3mo"
            elif days >= 30:
                period = "1mo"
            else:
                period = "7d"
            instrument = yf.Ticker(ticker)
            hist = instrument.history(period=period, interval="1d")
            if hist is None or hist.empty:
                return []

            history = []
            for index, row in hist.iterrows():
                history.append({
                    "date": index.strftime("%d %b"),
                    "price": float(row.get("Close", 0.0)),
                    "open": float(row.get("Open", 0.0)),
                    "high": float(row.get("High", 0.0)),
                    "low": float(row.get("Low", 0.0)),
                    "volume": float(row.get("Volume", 0.0)),
                })

            step = max(1, len(history) // 60)
            return history[::step]

        return await loop.run_in_executor(None, fetch)


# Dodaj ovo u external_apis.py

class PolymarketProvider:
    """Izvlači podatke o verovatnoći sa Polymarket predikcionih tržišta."""
    CLOB_API_URL = "https://clob.polymarket.com/markets"

    @staticmethod
    async def get_market_odds(asset_name: str) -> str:
        cache_key = f"poly_{asset_name.lower()}"
        cached = market_cache.get(cache_key)
        if cached:
            print(f"[CACHE HIT] Polymarket: {asset_name}")
            return cached

        result = "Nema dostupnih podataka sa Polymarket-a."
        try:
            async with aiohttp.ClientSession() as session:
                params = {"active": "true", "order": "desc"}
                async with session.get(PolymarketProvider.CLOB_API_URL, params=params) as resp:
                    if resp.status == 200:
                        markets = await resp.json()
                        relevant = [m for m in markets if asset_name.lower() in m.get('description', '').lower()]
                        if relevant:
                            market = relevant[0]
                            desc = market.get('description')
                            prob = market.get('outcome_prices', {}).get('Yes', 'N/A')
                            result = f"Polymarket prognoza za '{desc}': Verovatnoća {prob}"
        except Exception:
            pass

        market_cache.set(cache_key, result, ttl=10800)  # 3 sata
        return result

class PremiumNewsProvider:
    """Ciljani RSS feed-ovi sa najjačih kripto izvora (Perplexity stil)."""
    
    SOURCES = {
        "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
        "TheBlock": "https://www.theblock.co/rss.xml",
        "CoinTelegraph": "https://cointelegraph.com/rss"
    }

    @staticmethod
    async def get_premium_news(asset_name: str) -> List[Dict]:
        headlines = []
        async with aiohttp.ClientSession() as session:
            for source_name, url in PremiumNewsProvider.SOURCES.items():
                try:
                    async with session.get(url) as resp:
                        if resp.status == 200:
                            text = await resp.text()
                            root = ET.fromstring(text)
                            for item in root.findall(".//item")[:2]: # Uzimamo top 2 vesti po izvoru
                                title = item.find("title").text
                                if asset_name.lower() in title.lower():
                                    headlines.append({
                                        "source": source_name,
                                        "text": title,
                                        "sentiment": "neutral" # Može se dodati NLP analiza
                                    })
                except Exception:
                    continue
        return headlines

def calc_indicators(prices: List[float]) -> Dict:
    """Računa tehničke indikatore iz liste closing cena (pure Python, bez dependencija)."""
    result: Dict = {}
    n = len(prices)

    # RSI(14)
    if n >= 15:
        period = 14
        gains, losses = [], []
        for i in range(1, n):
            diff = prices[i] - prices[i - 1]
            gains.append(max(diff, 0.0))
            losses.append(max(-diff, 0.0))
        avg_gain = sum(gains[:period]) / period
        avg_loss = sum(losses[:period]) / period
        for i in range(period, len(gains)):
            avg_gain = (avg_gain * (period - 1) + gains[i]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result["rsi14"] = 100.0 if avg_loss == 0 else round(100 - (100 / (1 + avg_gain / avg_loss)), 2)

    # SMA(20)
    if n >= 20:
        result["sma20"] = round(sum(prices[-20:]) / 20, 4)

    # EMA helper
    def _ema(p: List[float], period: int):
        if len(p) < period:
            return None
        k = 2 / (period + 1)
        val = sum(p[:period]) / period
        for x in p[period:]:
            val = x * k + val * (1 - k)
        return round(val, 4)

    ema12 = _ema(prices, 12)
    ema26 = _ema(prices, 26)
    if ema12 is not None:
        result["ema12"] = ema12
    if ema26 is not None:
        result["ema26"] = ema26
    if ema12 is not None and ema26 is not None:
        result["macd"] = round(ema12 - ema26, 4)

    # Bollinger Bands(20)
    if n >= 20:
        sma = sum(prices[-20:]) / 20
        std = (sum((p - sma) ** 2 for p in prices[-20:]) / 20) ** 0.5
        result["bb_upper"] = round(sma + 2 * std, 4)
        result["bb_middle"] = round(sma, 4)
        result["bb_lower"] = round(sma - 2 * std, 4)

    return result


class AIServiceProvider:
    @staticmethod
    def _get_active_client_and_model():
                
        if ACTIVE_PROVIDER == "lmstudio" and lmstudio_client:
            return lmstudio_client, LMSTUDIO_MODEL
        elif ACTIVE_PROVIDER == "openrouter" and openrouter_client:
            # Koristimo zvaničan, potpuno besplatan Llama 3 model preko OpenRoutera!
            return openrouter_client, "google/gemma-4-31b-it:free"
        elif ACTIVE_PROVIDER == "groq" and groq_client:
            return groq_client, "groq/compound"
        elif openai_client:
            return openai_client, "gpt-3.5-turbo-0125"
        else:
            return None, None

    @staticmethod
    async def get_order_book_walls(ticker: str) -> str:
        """Pristupa Binance API-ju i pronalazi prave 'zidove' (nivoe sa najvećim obimom)."""
        symbol = f"{ticker.upper()}USDT"
        # Povećavamo limit na 100 da bismo "videli" dublje u order book
        url = f"https://api.binance.com/api/v3/depth?symbol={symbol}&limit=100"
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=3) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        bids = data.get("bids", [])
                        asks = data.get("asks", [])
                        
                        if not bids or not asks:
                            return f"Nema L2 podataka za {symbol}."
                        
                        # LOGIKA ZIDOVA: Tražimo onaj nivo cene gde je KOLIČINA (x[1]) najveća
                        best_bid = max(bids, key=lambda x: float(x[1]))
                        best_ask = max(asks, key=lambda x: float(x[1]))
                        
                        return (f"L2 DUBINA ({ticker.upper()}): "
                                f"Glavni Support zid je na ${float(best_bid[0]):.4f} (Vol: {float(best_bid[1]):.0f}). "
                                f"Glavni Resistance zid je na ${float(best_ask[0]):.4f} (Vol: {float(best_ask[1]):.0f}).")
        except Exception as e:
            print(f"Greška za L2 ({symbol}): {e}")
            pass
            
        return "L2 Order Book trenutno nije dostupan."

    @staticmethod
    async def get_market_analysis(asset_name: str, ticker: str, asset_type: str, cena: float, promena: float, history_data: List[Dict]) -> Dict:
        client, start_model = AIServiceProvider._get_active_client_and_model()
        
        if not client:
            return {"error": "API ključevi nisu podešeni u .env fajlu."}

        trenutni_datum = datetime.now().strftime("%d.%m.%Y.")
        all_prices = [round(h["price"], 4) for h in history_data] if history_data else []
        price_trend = all_prices[-10:]  # zadnjih 10 tačaka za prikaz u promptu

        # Tehničke indikatori iz 30-dnevne istorije
        indicators = calc_indicators(all_prices)
        ind_str = ", ".join(f"{k.upper()}={v}" for k, v in indicators.items()) if indicators else "Nema dovoljno podataka"

        # Volume iz posljednje tačke (ako postoji)
        last_volume = history_data[-1].get("volume", 0) if history_data else 0
        vol_str = f"Vol: {last_volume:,.0f}" if last_volume else "Vol: N/A"

        order_book_data = await AIServiceProvider.get_order_book_walls(ticker)
        news_sentiment_data = await AIServiceProvider.get_news_and_sentiment(asset_name, asset_type)
        polymarket_odds = await PolymarketProvider.get_market_odds(asset_name)
        premium_news = await PremiumNewsProvider.get_premium_news(asset_name)

        print(f"""\n{'='*50}
[LLM PROMPT] Prikupljeni podaci za {asset_name} ({ticker}):
  Cena         : ${cena}
  Tačaka hist. : {len(all_prices)} (koristimo za indikatore)
  Trend (10h)  : {price_trend}
  Indikatori   : {ind_str}
  {vol_str}
  Order Book   : {str(order_book_data)[:200]}
  Polymarket   : {str(polymarket_odds)[:200]}
  Premium vesti: {str(premium_news)[:300]}
  News sent.   : kategorija={news_sentiment_data.get('sentiment_category','?')}, score={news_sentiment_data.get('overall_sentiment','?'):.2f}, naslovi={[h['text'][:60] for h in news_sentiment_data.get('headlines', [])]}
{'='*50}""")

        # Pripremamo sažetak Google News za LLM
        news_cat = news_sentiment_data.get("sentiment_category", "NEUTRAL")
        news_headlines_str = "; ".join(
            h.get("text", "")[:80] for h in news_sentiment_data.get("headlines", [])[:4]
        )

        messages = [
            {
                "role": "system", 
                "content": f"""Kvantni analitičar. DANAŠNJI DATUM: {trenutni_datum}.
                Analiziraš ISKLJUČIVO sredstvo: {asset_name} ({ticker}). 
                Ignorisi sve ostale instrumente koji se pominju u vestima (npr. Bitcoin, S&P 500, zlato itd.) osim ako DIREKTNO utiču na {asset_name}.
                Sintetiši podatke i vrati ISKLJUČIVO čisti JSON bez markdown formata.
                
                VAŽNO: Trenutna cena je TAČNO ${cena}. Forecast mora biti realna projekcija ove cene za 7 dana.
                Dozvoljeni opseg: expected/bull/bear MORA biti između {cena*0.7:.2f} i {cena*1.5:.2f}. NE SMEŠ koristiti cene iz starog training data!
                Katalizatori moraju biti specifični za {asset_name}, ne za druga sredstva.
                
                Struktura (zameni placeholder vrednosti stvarnim brojevima blizu ${cena}):
                {{
                "news_sentiment": {{"score": "BULLISH/BEARISH/NEUTRAL", "headline": "Najvažnija vest o {asset_name} u jednoj rečenici"}},
                "stance": "BULLISH",
                "summary": "Kratka sinteza za {asset_name}.",
                "catalysts": ["katalizator specifičan za {asset_name}"],
                "recommendation": "BUY",
                "risk_level": "LOW",
                "forecast": {{"expected": {cena*1.03:.2f}, "bull": {cena*1.10:.2f}, "bear": {cena*0.92:.2f}, "confidence": 75}}
                }}"""
            },
            {
                "role": "user", 
                "content": f"Analiziraj: {asset_name} ({ticker})\nTrenutna cena: ${cena}, Promena24h: {promena:.2f}%\nTrend(10t): {price_trend}\nIndikatori: {ind_str}\n{vol_str}\nL2: {str(order_book_data)[:150]}\nNews sentiment za {asset_name}: {news_cat} | {news_headlines_str}\nOpklade: {str(polymarket_odds)[:150]}\nPremium vesti: {str(premium_news)[:200]}"
            }
        ]

        # --- PAMETNI FALLBACK MEHANIZAM ---
        # Ako koristimo OpenRouter, prolazimo kroz listu besplatnih modela.
        # Ako koristimo neki drugi API (Groq/ZAI), probaće samo taj jedan izabrani.
        if ACTIVE_PROVIDER == "openrouter":
            modeli_za_pokusaj = [
                "openrouter/free",                    # Prvi izbor: Bira najbolji slobodan
                "meta-llama/llama-3-8b-instruct:free", # Drugi izbor
                "google/gemma-2-9b-it:free"            # Treći izbor
            ]
        else:
            modeli_za_pokusaj = [start_model]

        poslednja_greska = None

        # Prolazimo kroz modele jedan po jedan
        for aktivni_model in modeli_za_pokusaj:
            try:
                print(f"Pokušavam AI analizu sa modelom: {aktivni_model}...")
                
                # Pripremamo standardne parametre za poziv
                api_kwargs = {
                    "model": aktivni_model,
                    "max_tokens": 8192, 
                    "messages": messages,
                    "temperature": 0.2
                }
                
                # Uključujemo logičko "razmišljanje" SAMO za OpenRouter
                if ACTIVE_PROVIDER == "openrouter":
                    api_kwargs["extra_body"] = {"reasoning": {"enabled": True}}
                
                # Izvršavamo asinhroni poziv
                    response = await client.chat.completions.create(**api_kwargs)
                raw_content = response.choices[0].message.content
                
                # Ispisujemo tok misli modela u terminal (ako postoji)
                reasoning = getattr(response.choices[0].message, 'reasoning_details', None)
                if reasoning:
                    print(f"🧠 Model razmišlja:\n{reasoning}\n{'-'*30}")

                if not raw_content: 
                    raise Exception("Model je vratio prazan odgovor.")
                    
                # Čišćenje potencijalnih markdown tagova
                cleaned_content = raw_content.replace("```json", "").replace("```", "").strip()
                
                try:
                    # Ako uspešno dešifruje JSON, VRAĆA REZULTAT I PREKIDA PETLJU
                    return json.loads(cleaned_content)
                except json.JSONDecodeError:
                    print(f"JSON Parse Greška. Sirov odgovor: {raw_content}")
                    raise Exception("Model nije vratio ispravan JSON format.")
                    
            except Exception as e:
                # Ako bilo šta pukne (Rate Limit, loš JSON, prazan odgovor), beležimo i idemo na sledeći model
                print(f"⚠️ Model {aktivni_model} nije uspeo. Razlog: {e}")
                poslednja_greska = str(e)
                continue 
                
        # Ako se petlja završi, a nijedan model nije prošao, vraćamo konačnu grešku
        return {"error": f"Svi AI modeli su trenutno preopterećeni. Poslednja greška: {poslednja_greska}"}

    @staticmethod
    async def get_chat_response(messages: List[Dict[str, str]]) -> str:
        client, model = AIServiceProvider._get_active_client_and_model()
        if not client: raise Exception("API ključevi nisu konfigurisani.")
        
        try:
            response = await client.chat.completions.create(
                model=model, 
                messages=messages,
                temperature=0.7
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Chat Greška: {e}")
            raise e

    @staticmethod
    async def get_chat_response(messages: List[Dict[str, str]]) -> str:
        """Centralizovana metoda za AI chat koja automatski bira aktivan model."""
        client, model = AIServiceProvider._get_active_client_and_model()
        if not client:
            raise Exception("API ključevi nisu konfigurisani.")
            
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages
            )
            return response.choices[0].message.content
        except Exception as e:
            print(f"Chat Greška ({model}): {e}")
            raise e

    @staticmethod
    def get_price_forecast(current_price: float) -> Dict:
        if current_price == 0:
            return {"expected_price": 0.0, "bull_case": 0.0, "bear_case": 0.0, "confidence": 0}
            
        expected = current_price * 1.05
        bull = current_price * 1.15
        bear = current_price * 0.92
        return {"expected_price": expected, "bull_case": bull, "bear_case": bear, "confidence": 82}

    @staticmethod
    async def get_news_and_sentiment(asset_name: str, asset_type: str = "crypto") -> Dict:
        """Preuzima vesti i sentement sa Google News RSS, prilagođeno tipu instrumenta."""
        cache_key = f"news_{asset_type}_{asset_name.lower()}"
        cached = market_cache.get(cache_key)
        if cached:
            print(f"[CACHE HIT] News sentiment: {asset_name} ({asset_type})")
            return cached

        # Upit prilagođen tipu instrumenta
        _query_suffix = {
            "crypto":    "cryptocurrency price",
            "stock":     "stock earnings market",
            "index":     "stock market index",
            "commodity": "commodity futures price",
        }.get(asset_type, "market")
        query = urllib.parse.quote(f"{asset_name} {_query_suffix}")
        url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"

        # Pozitivne/negativne reči po tipu
        positive_words = ['surge', 'jump', 'bull', 'high', 'gain', 'buy', 'up', 'soar',
                          'adopt', 'rally', 'rise', 'boost', 'record', 'growth', 'beats']
        negative_words = ['crash', 'drop', 'bear', 'low', 'lose', 'sell', 'down', 'hack',
                          'scam', 'sue', 'sec', 'fall', 'slump', 'miss', 'recession',
                          'inflation', 'fear', 'concern', 'warning', 'cut', 'layoff']

        headlines = []
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=5) as resp:
                    if resp.status == 200:
                        xml_data = await resp.text()
                        root = ET.fromstring(xml_data)
                        items = root.findall(".//item")

                        for item in items[:5]:
                            title = item.find("title").text if item.find("title") is not None else ""
                            if not title:
                                continue
                            title_lower = title.lower()
                            sentiment = "neutral"
                            if any(w in title_lower for w in positive_words):
                                sentiment = "positive"
                            elif any(w in title_lower for w in negative_words):
                                sentiment = "negative"
                            headlines.append({"text": title, "sentiment": sentiment})
        except Exception as ex:
            print(f"[NEWS] Greška pri fetchu za {asset_name}: {ex}")

        if not headlines:
            headlines = [{"text": f"Nema pronađenih vesti za {asset_name}.", "sentiment": "neutral"}]

        pos = sum(1 for h in headlines if h["sentiment"] == "positive")
        neg = sum(1 for h in headlines if h["sentiment"] == "negative")
        score = 0.5 + (pos * 0.1) - (neg * 0.1)
        score = max(0.1, min(0.9, score))
        cat = "POSITIVE" if score > 0.6 else "NEGATIVE" if score < 0.4 else "NEUTRAL"

        result = {"overall_sentiment": score, "sentiment_category": cat, "headlines": headlines}
        market_cache.set(cache_key, result, ttl=21600)  # 6 sati
        return result


class FinancialAgentService:
    """Pipeline: prikupljanje podataka + LLM izveštaj za bilo koji instrument."""

    @staticmethod
    def _compute_indicators(hist) -> Dict[str, Any]:
        """Kompjutuje 7 tehničkih indikatora iz yfinance DataFrame-a."""
        indicators: Dict[str, Any] = {}
        if hist is None or len(hist) < 5:
            return indicators

        import pandas as pd

        close = hist["Close"].squeeze()
        high  = hist["High"].squeeze()
        low   = hist["Low"].squeeze()
        n20   = min(20, len(close))
        n14   = min(14, len(close))

        def safe(val):
            try:
                v = float(val)
                return round(v, 4) if not (v != v) else None  # NaN check
            except Exception:
                return None

        indicators["SMA_20"]  = safe(close.rolling(window=n20).mean().iloc[-1])
        indicators["EMA_20"]  = safe(close.ewm(span=n20).mean().iloc[-1])

        delta = close.diff()
        gain  = delta.where(delta > 0, 0).rolling(window=n14).mean()
        loss  = (-delta.where(delta < 0, 0)).rolling(window=n14).mean()
        rs    = gain / loss.replace(0, float("nan"))
        rsi   = 100 - (100 / (1 + rs))
        indicators["RSI_14"] = safe(rsi.iloc[-1]) or 50.0

        ema12 = close.ewm(span=12).mean()
        ema26 = close.ewm(span=26).mean()
        macd  = ema12 - ema26
        sig   = macd.ewm(span=9).mean()
        indicators["MACD"]           = safe(macd.iloc[-1])
        indicators["MACD_signal"]    = safe(sig.iloc[-1])
        indicators["MACD_histogram"] = safe((macd - sig).iloc[-1])

        sma20 = close.rolling(window=n20).mean()
        std20 = close.rolling(window=n20).std()
        indicators["BB_upper"]  = safe((sma20 + 2 * std20).iloc[-1])
        indicators["BB_middle"] = safe(sma20.iloc[-1])
        indicators["BB_lower"]  = safe((sma20 - 2 * std20).iloc[-1])

        ll = low.rolling(window=n14).min()
        hh = high.rolling(window=n14).max()
        rng = (hh - ll).replace(0, float("nan"))
        stk = 100 * ((close - ll) / rng)
        std = stk.rolling(window=3).mean()
        indicators["Stochastic_K"] = safe(stk.iloc[-1]) or 50.0
        indicators["Stochastic_D"] = safe(std.iloc[-1]) or 50.0
        indicators["Williams_R"]   = safe(((hh - close) / rng * -100).iloc[-1]) or -50.0

        return indicators

    @staticmethod
    async def _collect_yfinance_data(ticker: str) -> Dict[str, Any]:
        AssetIndicatorCacheService, SessionLocal = _get_indicator_cache_service()
        # Koristimo ticker kao asset_id, tip je 'stock' (obuhvata i index/commodity)
        db = SessionLocal()
        try:
            cached = AssetIndicatorCacheService.get(db, ticker, "stock")
            if cached:
                print(f"[CACHE HIT] Indicators DB: {ticker} stock")
                return {
                    "current_price": cached["current_price"],
                    "change_percent": cached["change_percent"],
                    "price_series": cached["price_series"],
                    "name": ticker,
                    "indicators": cached["indicators"],
                }
        finally:
            db.close()

        # Cache MISS — fetchujemo sa yfinance
        loop = asyncio.get_event_loop()

        def fetch() -> Dict[str, Any]:
            t        = yf.Ticker(ticker)
            hist_3mo = t.history(period="3mo")
            hist_7d  = t.history(period="7d", interval="1d")
            if hist_3mo is None or hist_3mo.empty:
                return {"error": f"Nema podataka za {ticker}"}

            current   = float(hist_3mo["Close"].iloc[-1])
            prev      = float(hist_3mo["Close"].iloc[-2]) if len(hist_3mo) > 1 else current
            change    = ((current - prev) / prev * 100) if prev else 0.0
            info      = t.info or {}
            series    = [round(float(v), 4) for v in hist_7d["Close"].tolist()[-10:]] if not hist_7d.empty else []
            return {
                "current_price": current,
                "change_percent": change,
                "price_series": series,
                "_hist": hist_3mo,
                "name": info.get("longName") or info.get("shortName") or ticker,
            }

        raw = await loop.run_in_executor(None, fetch)
        if "error" in raw:
            return raw
        raw["indicators"] = FinancialAgentService._compute_indicators(raw.pop("_hist", None))

        # Sačuvaj u DB cache
        db2 = SessionLocal()
        try:
            AssetIndicatorCacheService.save(
                db2, ticker, "stock",
                raw["current_price"], raw["change_percent"],
                raw["price_series"], raw["indicators"]
            )
        finally:
            db2.close()

        return raw

    @staticmethod
    async def _collect_crypto_data(coin_id: str) -> Dict[str, Any]:
        AssetIndicatorCacheService, SessionLocal = _get_indicator_cache_service()
        db = SessionLocal()
        try:
            cached = AssetIndicatorCacheService.get(db, coin_id, "crypto")
            if cached:
                print(f"[CACHE HIT] Indicators DB: {coin_id} crypto")
                return {
                    "current_price": cached["current_price"],
                    "change_percent": cached["change_percent"],
                    "price_series": cached["price_series"],
                    "name": coin_id,
                    "indicators": cached["indicators"],
                }
        finally:
            db.close()

        # Cache MISS — fetchujemo i računamo
        details = await CryptoDataProvider.get_asset_details(coin_id) or {}
        history = await CryptoDataProvider.get_price_history(coin_id, days=30)
        all_prices = [round(h["price"], 4) for h in history] if history else []
        series = all_prices[-10:]

        raw_ind = calc_indicators(all_prices)
        indicators = {}
        if raw_ind.get("sma20")     is not None: indicators["SMA_20"]    = raw_ind["sma20"]
        if raw_ind.get("ema12")     is not None: indicators["EMA_20"]    = raw_ind["ema12"]
        if raw_ind.get("rsi14")     is not None: indicators["RSI_14"]    = raw_ind["rsi14"]
        if raw_ind.get("macd")      is not None: indicators["MACD"]      = raw_ind["macd"]
        if raw_ind.get("bb_upper")  is not None: indicators["BB_upper"]  = raw_ind["bb_upper"]
        if raw_ind.get("bb_middle") is not None: indicators["BB_middle"] = raw_ind["bb_middle"]
        if raw_ind.get("bb_lower")  is not None: indicators["BB_lower"]  = raw_ind["bb_lower"]

        current_price = details.get("current_price", 0.0)
        change_percent = details.get("change_24h", 0.0)

        db2 = SessionLocal()
        try:
            AssetIndicatorCacheService.save(
                db2, coin_id, "crypto", current_price, change_percent, series, indicators
            )
        finally:
            db2.close()

        return {
            "current_price": current_price,
            "change_percent": change_percent,
            "price_series": series,
            "name": details.get("name", coin_id),
            "indicators": indicators,
        }

    @staticmethod
    async def run_analysis(instrument_name: str, asset_type: str, ticker: str) -> Dict[str, Any]:
        normalized = (asset_type or "crypto").lower()

        if normalized == "crypto":
            market_data = await FinancialAgentService._collect_crypto_data(ticker)
        else:
            market_data = await FinancialAgentService._collect_yfinance_data(ticker)

        if "error" in market_data:
            return {"error": market_data["error"]}

        news_task, poly_task = (
            AIServiceProvider.get_news_and_sentiment(instrument_name, normalized),
            PolymarketProvider.get_market_odds(instrument_name),
        )
        news_result, poly_result = await asyncio.gather(news_task, poly_task, return_exceptions=True)
        if isinstance(news_result, Exception):
            news_result = {"headlines": [], "sentiment_category": "NEUTRAL"}
        if isinstance(poly_result, Exception):
            poly_result = "Nema podataka."

        report_text = await FinancialAgentService._generate_report(
            market_data, news_result, poly_result, instrument_name, normalized, ticker
        )

        return {
            "instrument_name": instrument_name,
            "asset_type": normalized,
            "ticker": ticker,
            "current_price": market_data.get("current_price", 0.0),
            "report": report_text,
            "ai_provider": ACTIVE_PROVIDER,
        }

    @staticmethod
    async def _generate_report(
        market_data: Dict, news: Dict, poly: str,
        instrument_name: str, asset_type: str, ticker: str
    ) -> str:
        client, model = AIServiceProvider._get_active_client_and_model()
        if not client:
            return "Greška: AI provajder nije konfigurisan. Proverite .env fajl."

        ind     = market_data.get("indicators", {})
        price   = market_data.get("current_price", 0.0)
        change  = market_data.get("change_percent", 0.0)
        series  = market_data.get("price_series", [])
        heads   = [h.get("text", "") for h in news.get("headlines", [])[:4]]
        today   = datetime.now().strftime("%d.%m.%Y.")

        type_label = {"stock": "akcija", "index": "indeks",
                      "commodity": "roba/futures", "crypto": "kriptovaluta"}.get(asset_type, "instrument")

        def fmt(v):
            return str(v) if v is not None else "N/A"

        system_msg = (
            "Ti si ekspertski finansijski analitičar sa 20 godina iskustva. "
            "Piši izveštaje isključivo na srpskom jeziku. "
            "Pružaj konkretne, podacima potkrepljene analize sa jasnim zaključcima."
        )
        user_msg = f"""Napiši detaljan finansijski izveštaj za sledeći instrument:

**Instrument:** {instrument_name} ({ticker}) — {type_label}
**Datum analize:** {today}
**Trenutna cena:** ${price:,.4f}
**Promena (24h/dan):** {change:+.2f}%
**Trend cene (posledn. 10 perioda):** {series}

**Tehnički indikatori:**
- SMA_20: {fmt(ind.get("SMA_20"))}
- EMA_20: {fmt(ind.get("EMA_20"))}
- RSI_14: {fmt(ind.get("RSI_14"))}
- MACD: {fmt(ind.get("MACD"))} | Signal: {fmt(ind.get("MACD_signal"))} | Histogram: {fmt(ind.get("MACD_histogram"))}
- Bollinger: Gornja {fmt(ind.get("BB_upper"))} | Srednja {fmt(ind.get("BB_middle"))} | Donja {fmt(ind.get("BB_lower"))}
- Stochastic K/D: {fmt(ind.get("Stochastic_K"))} / {fmt(ind.get("Stochastic_D"))}
- Williams %R: {fmt(ind.get("Williams_R"))}

**Sentiment vesti:** {news.get("sentiment_category", "NEUTRAL")}
**Relevantne vesti:** {heads}
**Polymarket:** {str(poly)[:200]}

Struktura izveštaja:
1. Tehnička analiza ({instrument_name})
2. Fundamentalni faktori i tržišni kontekst
3. Prognoza (kratkoročna i srednjoročna)
4. Preporuka i nivo rizika

Navedi konkretne cene, procente i nivoe podrške/otpora."""

        try:
            api_kwargs: Dict[str, Any] = {
                "model": model,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user",   "content": user_msg},
                ],
                "max_tokens": 2048,
                "temperature": 0.4,
            }
            if ACTIVE_PROVIDER == "openrouter":
                api_kwargs["extra_body"] = {"reasoning": {"enabled": False}}

            print(f"Pokrećem Financial Agent analizu [{instrument_name}] via {ACTIVE_PROVIDER} / {model}")
            response = await client.chat.completions.create(**api_kwargs)
            return response.choices[0].message.content or "Model je vratio prazan odgovor."
        except Exception as e:
            return f"Greška pri generisanju izveštaja: {e}"
