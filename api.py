from fastapi import FastAPI, HTTPException, Depends
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from typing import List, Dict, Any, Optional
from sqlalchemy.orm import Session

# Importujemo tvoje postojeće servise i keš menadžer
from external_apis import CryptoDataProvider, MultiAssetDataProvider, AIServiceProvider, FinancialAgentService
from models import SessionLocal, Base, engine
from db_service import ForecastTrackerService, MarketAnalysisService, PriceHistoryCacheService, NewsAndSentimentService
from cache_manager import market_cache
import db_service

# Kreira bazu i tabele ako ne postoje
Base.metadata.create_all(bind=engine)

# SQLite migracija: dodaj asset_type kolonu ako ne postoji
import sqlite3 as _sqlite3
try:
    _conn = _sqlite3.connect("crypto_pulse_ai.db")
    _conn.execute("ALTER TABLE forecast_snapshots ADD COLUMN asset_type TEXT DEFAULT 'crypto'")
    _conn.commit()
    _conn.close()
    print("[MIGRATION] Dodata kolona asset_type u forecast_snapshots")
except Exception:
    pass  # Kolona već postoji

app = FastAPI(title="Crypto-Pulse-AI Backend", version="1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- DEPENDENCY ZA BAZU ---
def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

# --- MODELI ZA PRIMANJE PODATAKA (POST REQUESTS) ---
class AIAnalysisRequest(BaseModel):
    asset_name: str
    ticker: str
    asset_type: str = "crypto"  # crypto / stock / index / commodity
    current_price: float
    change_24h: float
    history: List[Dict[str, Any]]

class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]
    use_portfolio_context: Optional[bool] = False

class TrackAssetRequest(BaseModel):
    asset_id: str
    asset_name: str
    ticker: str
    asset_type: str = "crypto"
    current_price: float
    expected_price_7d: float
    bull_case: float
    bear_case: float
    model_confidence: float

class RunAnalysisRequest(BaseModel):
    instrument_name: str
    asset_type: str = "crypto"   # crypto / stock / index / commodity
    ticker: str
    save: bool = True             # sačuvati u bazu?

# --- API RUTE ZA TRŽIŠTE I AI ---

@app.get("/")
async def root():
    return {"status": "Sistemi operativni", "service": "Crypto-Pulse API"}

@app.get("/api/market/global")
async def get_global_market(asset_type: str = "crypto"):
    normalized_asset_type = (asset_type or "crypto").lower()

    # 1. Probaj RAM (TTL 60 sekundi za globalne podatke)
    cache_key = f"global_stats_{normalized_asset_type}"
    cached = market_cache.get(cache_key)
    if cached: 
        return cached
    
    # 2. Ako nema, zovi postojeću logiku
    data = await MultiAssetDataProvider.get_global_market_data(normalized_asset_type)
    if not data:
        raise HTTPException(status_code=500, detail="Greška pri dohvatanju globalnih podataka")
    
    # 3. Sačuvaj u RAM za sledeći put
    market_cache.set(cache_key, data, ttl=60)
    return data

@app.get("/api/market/top")
async def get_top_assets(limit: int = 10, asset_type: str = "crypto"):
    normalized_asset_type = (asset_type or "crypto").lower()

    # 1. Definišemo ključ za RAM keš
    cache_key = f"top_assets_{normalized_asset_type}_{limit}"

    # 2. Proveravamo da li su podaci već u memoriji
    cached = market_cache.get(cache_key)
    if cached:
        return cached

    # 3. Ako nisu, tek onda zovemo odgovarajući provider
    data = await MultiAssetDataProvider.get_top_assets(normalized_asset_type, limit)

    # 4. Sačuvamo listu u RAM na 60 sekundi da izbegnemo blokadu
    if data:
        market_cache.set(cache_key, data, ttl=60)

    return data

@app.get("/api/asset/{asset_id}")
async def get_asset_details(asset_id: str, asset_type: str = "crypto"):
    data = await MultiAssetDataProvider.get_asset_details(asset_id, asset_type)
    if not data:
        raise HTTPException(status_code=404, detail="Instrument nije pronađen")
    return data

@app.get("/api/asset/{asset_id}/history")
async def get_asset_history(asset_id: str, days: int = 7, asset_type: str = "crypto", db: Session = Depends(get_db)):
    cached = PriceHistoryCacheService.get_history(db, f"{asset_id}_{asset_type}", str(days))
    if cached:
        print(f"[CACHE HIT] History DB: {asset_id} {asset_type} {days}d")
        return cached
    data = await MultiAssetDataProvider.get_price_history(asset_id, asset_type, days)
    if data:
        PriceHistoryCacheService.save_history(db, f"{asset_id}_{asset_type}", str(days), data)
    return data

@app.post("/api/ai/analysis")
async def get_ai_analysis(request: AIAnalysisRequest, db: Session = Depends(get_db)):
    ai_key = market_cache.generate_ai_key(request.asset_name, request.current_price)
    
    cached_ai = market_cache.get(f"ai_{ai_key}")
    if cached_ai:
        return cached_ai

    # Provjeri DB cache za news sentiment prije LLM poziva
    asset_id_key = request.asset_name.lower()
    db_news = NewsAndSentimentService.get_cached_sentiment(db, asset_id_key)
    if db_news:
        print(f"[CACHE HIT] NewsSentimentCache DB: {request.asset_name}")

    analysis = await AIServiceProvider.get_market_analysis(
        request.asset_name, 
        request.ticker,
        request.asset_type,
        request.current_price, 
        request.change_24h, 
        request.history
    )
    
    if not analysis or "error" in analysis:
        raise HTTPException(status_code=500, detail="AI analiza trenutno nije dostupna")

    # Sačuvaj news sentiment u DB ako ga nema
    if not db_news and analysis.get("news_sentiment"):
        ns = analysis["news_sentiment"]
        score = 1.0 if ns.get("score") == "BULLISH" else (0.0 if ns.get("score") == "BEARISH" else 0.5)
        NewsAndSentimentService.cache_sentiment(
            db, asset_id_key, score, ns.get("score", "NEUTRAL"),
            [{"text": ns.get("headline", "")}]
        )
    
    market_cache.set(f"ai_{ai_key}", analysis, ttl=420)  # 7 minuta
    return analysis

@app.post("/api/chat")
async def get_chat_response(request: ChatRequest, db: Session = Depends(get_db)):
    messages = request.messages
    
    # --- LOGIKA ZA PORTFOLIO KONTEKST ---
    if request.use_portfolio_context:
        try:
            # SADA PRAVILNO POZIVAMO SERVIS I PROSLEĐUJEMO SESIJU BAZE
            user_assets = ForecastTrackerService.get_tracked_assets(db)
            
            if user_assets and len(user_assets) > 0:
                # Pravimo čitljiv spisak za AI
                assets_info = "\n".join([
                    f"- {a['asset_name']} ({a['ticker']}): Ulazna cena ${a['current_price']:.2f}" 
                    for a in user_assets
                ])
                
                context_instruction = (
                    "VAŽNO: Korisnik u svom portfoliju prati sledeće valute:\n"
                    f"{assets_info}\n"
                    "Kada odgovaraš na pitanja, uzmi u obzir ove valute i budi specifičan u vezi sa njima."
                )
                
                # Ubacujemo kontekst kao sistemsku instrukciju na početak
                messages.insert(0, {"role": "system", "content": context_instruction})
        except Exception as db_err:
            print(f"Greška pri čitanju portfolija za Chat: {db_err}")
            # Ne radimo ništa, puštamo chat da radi bez portfolija umesto da pukne 500 greška

    # --- SLANJE KA AI PROVAJDERU ---
    try:
        odgovor = await AIServiceProvider.get_chat_response(messages)
        return {"response": odgovor}
    except Exception as e:
        print(f"Greška u AI Chat servisu: {e}")
        raise HTTPException(status_code=500, detail="AI asistent trenutno nije dostupan.")

# --- API RUTE ZA SISTEM PRAĆENJA (TRACKER) ---

@app.get("/api/tracker")
async def get_tracked_assets(db: Session = Depends(get_db)):
    """Vraća listu svih praćenih valuta iz SQLite baze."""
    return ForecastTrackerService.get_tracked_assets(db)

@app.post("/api/tracker")
async def add_tracked_asset(request: TrackAssetRequest, db: Session = Depends(get_db)):
    """Dodaje novu valutu i AI prognozu u sistem praćenja."""
    result = ForecastTrackerService.add_tracked_asset(
        db=db,
        asset_id=request.asset_id,
        asset_name=request.asset_name,
        ticker=request.ticker,
        asset_type=request.asset_type,
        current_price=request.current_price,
        expected_price_7d=request.expected_price_7d,
        bull_case=request.bull_case,
        bear_case=request.bear_case,
        model_confidence=request.model_confidence
    )
    if "error" in result:
        raise HTTPException(status_code=400, detail=result["error"])
    return result

@app.delete("/api/tracker/{snapshot_id}")
async def remove_tracked_asset(snapshot_id: int, db: Session = Depends(get_db)):
    """Briše valutu iz sistema praćenja."""
    success = ForecastTrackerService.remove_tracked_asset(db, snapshot_id)
    if not success:
        raise HTTPException(status_code=404, detail="Zapis nije pronađen u bazi.")
    return {"success": True}


# --- API RUTE ZA FINANCIAL AGENT ANALIZE ---

@app.post("/api/analysis/run")
async def run_financial_analysis(request: RunAnalysisRequest, db: Session = Depends(get_db)):
    """Pokreće puni financial agent pipeline i opciono čuva izveštaj u bazu."""
    run_key = f"run_{request.ticker.upper()}_{request.asset_type}"
    cached_run = market_cache.get(run_key)
    if cached_run:
        print(f"[CACHE HIT] Analysis run: {request.ticker}")
        return cached_run

    result = await FinancialAgentService.run_analysis(
        instrument_name=request.instrument_name,
        asset_type=request.asset_type,
        ticker=request.ticker,
    )
    if "error" in result:
        raise HTTPException(status_code=500, detail=result["error"])

    if request.save:
        MarketAnalysisService.save_report(
            db=db,
            instrument_name=result["instrument_name"],
            asset_type=result["asset_type"],
            ticker=result["ticker"],
            report_text=result["report"],
            current_price=result["current_price"],
            ai_provider=result["ai_provider"],
        )

    market_cache.set(run_key, result, ttl=900)  # 15 minuta
    return result


@app.get("/api/analysis")
async def get_all_analyses(db: Session = Depends(get_db)):
    """Vraća listu svih sačuvanih izveštaja (bez punog teksta, samo preview)."""
    return MarketAnalysisService.get_all_reports(db)


@app.get("/api/analysis/{report_id}")
async def get_analysis(report_id: int, db: Session = Depends(get_db)):
    """Vraća kompletan izveštaj po ID-u."""
    report = MarketAnalysisService.get_report(db, report_id)
    if not report:
        raise HTTPException(status_code=404, detail="Izveštaj nije pronađen.")
    return report


@app.delete("/api/analysis/{report_id}")
async def delete_analysis(report_id: int, db: Session = Depends(get_db)):
    """Briše izveštaj iz baze."""
    success = MarketAnalysisService.delete_report(db, report_id)
    if not success:
        raise HTTPException(status_code=404, detail="Izveštaj nije pronađen.")
    return {"success": True}
