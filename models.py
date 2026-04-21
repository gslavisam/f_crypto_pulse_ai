"""Database models for Crypto-Pulse-AI using SQLAlchemy."""

from datetime import datetime
from sqlalchemy import Column, Integer, String, Float, DateTime, JSON, create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker

# Create database
DATABASE_URL = "sqlite:///./crypto_pulse_ai.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class NewsSentimentCache(Base):
    __tablename__ = "news_sentiment_cache"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(String, unique=True, index=True)
    sentiment_score = Column(Float)
    sentiment_category = Column(String)
    news_headlines = Column(JSON)
    cached_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshots"
    id = Column(Integer, primary_key=True, index=True)
    asset_id = Column(String, index=True)
    asset_name = Column(String)
    ticker = Column(String)
    asset_type = Column(String, default="crypto")
    current_price = Column(Float)
    expected_price_7d = Column(Float)
    bull_case = Column(Float)
    bear_case = Column(Float)
    model_confidence = Column(Float)
    snapshot_date = Column(DateTime, default=datetime.utcnow)
    tracked_at = Column(DateTime, default=datetime.utcnow)


class DailyPriceRecord(Base):
    __tablename__ = "daily_price_records"
    id = Column(Integer, primary_key=True, index=True)
    forecast_snapshot_id = Column(Integer, index=True)
    asset_id = Column(String, index=True)
    day_offset = Column(Integer)
    closing_price = Column(Float)
    daily_average = Column(Float)
    recorded_date = Column(DateTime, default=datetime.utcnow)


# ================= NOVI MODELI ZA BRZO KEŠIRANJE =================

class DashboardCache(Base):
    """Sloj za super-brzo učitavanje kontrolne table (čuva se 5 minuta)."""
    __tablename__ = "dashboard_cache"
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, index=True)
    data = Column(JSON)
    cached_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


class PriceHistoryCache(Base):
    """Sloj za čuvanje grafikona kako bi se izbegla blokada (čuva se 12 sati)."""
    __tablename__ = "price_history_cache"
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, index=True)
    history_data = Column(JSON)
    cached_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


class MarketAnalysisReport(Base):
    """Čuva izveštaje finansijskog agenta generisane LLM analizom."""
    __tablename__ = "market_analysis_reports"
    id = Column(Integer, primary_key=True, index=True)
    instrument_name = Column(String, index=True)
    asset_type = Column(String)          # crypto / stock / index / commodity
    ticker = Column(String)
    current_price = Column(Float, default=0.0)
    report_text = Column(String)
    ai_provider = Column(String, default="openrouter")
    created_at = Column(DateTime, default=datetime.utcnow)


class AssetIndicatorCache(Base):
    """Čuva izračunate tehničke indikatore i price_series per asset (TTL 4h).
    
    Eliminišu potrebu za fetch-om 30d istorije na svakom pozivu.
    Dele ga i Details page i Reports tab.
    """
    __tablename__ = "asset_indicator_cache"
    id = Column(Integer, primary_key=True, index=True)
    cache_key = Column(String, unique=True, index=True)  # "{asset_id}_{asset_type}"
    asset_id = Column(String, index=True)
    asset_type = Column(String)
    current_price = Column(Float, default=0.0)
    change_percent = Column(Float, default=0.0)
    price_series = Column(JSON)    # poslednjih 10 tačaka za prompt
    indicators = Column(JSON)      # RSI14, SMA20, EMA12/26, MACD, BB, Stoch, Williams...
    cached_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime)


# Automatsko kreiranje novih tabela u postojećoj bazi
Base.metadata.create_all(bind=engine)