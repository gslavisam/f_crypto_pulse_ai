"""Database service layer for Crypto-Pulse-AI."""

from datetime import datetime, timedelta
from typing import List, Optional, Dict
from sqlalchemy.orm import Session
from models import (
    NewsSentimentCache,
    ForecastSnapshot,
    DailyPriceRecord,
    DashboardCache,
    PriceHistoryCache,
    MarketAnalysisReport,
    AssetIndicatorCache,
    SessionLocal
)


class DashboardCacheService:
    """Upravlja podacima za Kontrolnu tablu (Dashboard)."""
    
    @staticmethod
    def get_dashboard_data(db: Session) -> Optional[Dict]:
        cache = db.query(DashboardCache).filter(DashboardCache.cache_key == "main_dashboard").first()
        if cache and cache.expires_at > datetime.utcnow():
            return cache.data
        return None
        
    @staticmethod
    def save_dashboard_data(db: Session, data: Dict):
        cache = db.query(DashboardCache).filter(DashboardCache.cache_key == "main_dashboard").first()
        expires = datetime.utcnow() + timedelta(minutes=5) # Keširamo na tačno 5 minuta
        
        if cache:
            cache.data = data
            cache.cached_at = datetime.utcnow()
            cache.expires_at = expires
        else:
            new_cache = DashboardCache(cache_key="main_dashboard", data=data, expires_at=expires)
            db.add(new_cache)
        db.commit()


class PriceHistoryCacheService:
    """Upravlja istorijskim grafikonima."""
    
    @staticmethod
    def get_history(db: Session, asset_id: str, period: str) -> Optional[List[Dict]]:
        cache_key = f"{asset_id}_{period}"
        cache = db.query(PriceHistoryCache).filter(PriceHistoryCache.cache_key == cache_key).first()
        if cache and cache.expires_at > datetime.utcnow():
            return cache.history_data
        return None
        
    @staticmethod
    def save_history(db: Session, asset_id: str, period: str, data: List[Dict]):
        cache_key = f"{asset_id}_{period}"
        cache = db.query(PriceHistoryCache).filter(PriceHistoryCache.cache_key == cache_key).first()
        expires = datetime.utcnow() + timedelta(hours=12) # Grafikon važi 12 sati
        
        if cache:
            cache.history_data = data
            cache.cached_at = datetime.utcnow()
            cache.expires_at = expires
        else:
            new_cache = PriceHistoryCache(cache_key=cache_key, history_data=data, expires_at=expires)
            db.add(new_cache)
        db.commit()


class NewsAndSentimentService:
    @staticmethod
    def get_cached_sentiment(db: Session, asset_id: str) -> Optional[Dict]:
        cache = db.query(NewsSentimentCache).filter(NewsSentimentCache.asset_id == asset_id).first()
        if cache and cache.expires_at > datetime.utcnow():
            return {
                "sentiment_score": cache.sentiment_score,
                "sentiment_category": cache.sentiment_category,
                "news_headlines": cache.news_headlines,
                "cached_at": cache.cached_at.isoformat(),
                "from_cache": True
            }
        return None
    
    @staticmethod
    def cache_sentiment(db: Session, asset_id: str, sentiment_score: float, sentiment_category: str, news_headlines: List[Dict]) -> NewsSentimentCache:
        db.query(NewsSentimentCache).filter(NewsSentimentCache.asset_id == asset_id).delete()
        expires = datetime.utcnow() + timedelta(hours=6)
        new_cache = NewsSentimentCache(
            asset_id=asset_id, sentiment_score=sentiment_score,
            sentiment_category=sentiment_category, news_headlines=news_headlines, expires_at=expires
        )
        db.add(new_cache)
        db.commit()
        db.refresh(new_cache)
        return new_cache


class ForecastTrackerService:
    @staticmethod
    def add_tracked_asset(db: Session, asset_id: str, asset_name: str, ticker: str, asset_type: str = "crypto", current_price: float = 0.0, expected_price_7d: float = 0.0, bull_case: float = 0.0, bear_case: float = 0.0, model_confidence: float = 0.0):
        existing = db.query(ForecastSnapshot).filter(ForecastSnapshot.asset_id == asset_id).first()
        if existing: return {"error": "Već pratite ovu valutu."}
        
        snapshot = ForecastSnapshot(
            asset_id=asset_id, asset_name=asset_name, ticker=ticker, asset_type=asset_type,
            current_price=current_price, expected_price_7d=expected_price_7d, bull_case=bull_case,
            bear_case=bear_case, model_confidence=model_confidence
        )
        db.add(snapshot)
        db.commit()
        db.refresh(snapshot)
        return {"success": True, "snapshot_id": snapshot.id}
    
    @staticmethod
    def get_tracked_assets(db: Session):
        snapshots = db.query(ForecastSnapshot).order_by(ForecastSnapshot.tracked_at.desc()).all()
        return [{"id": s.id, "asset_id": s.asset_id, "asset_name": s.asset_name, "ticker": s.ticker, "asset_type": getattr(s, "asset_type", "crypto") or "crypto", "current_price": s.current_price, "expected_price_7d": s.expected_price_7d, "bull_case": s.bull_case, "bear_case": s.bear_case, "model_confidence": s.model_confidence, "tracked_at": s.tracked_at.strftime("%Y-%m-%d %H:%M")} for s in snapshots]
        
    @staticmethod
    def remove_tracked_asset(db: Session, snapshot_id: int):
        snapshot = db.query(ForecastSnapshot).filter(ForecastSnapshot.id == snapshot_id).first()
        if snapshot:
            db.delete(snapshot)
            db.commit()
            return True
        return False


class MarketAnalysisService:
    """CRUD za izveštaje finansijskog agenta."""

    @staticmethod
    def save_report(
        db: Session,
        instrument_name: str,
        asset_type: str,
        ticker: str,
        report_text: str,
        current_price: float = 0.0,
        ai_provider: str = "openrouter",
    ) -> dict:
        report = MarketAnalysisReport(
            instrument_name=instrument_name,
            asset_type=asset_type,
            ticker=ticker,
            current_price=current_price,
            report_text=report_text,
            ai_provider=ai_provider,
        )
        db.add(report)
        db.commit()
        db.refresh(report)
        return {"success": True, "report_id": report.id}

    @staticmethod
    def get_all_reports(db: Session) -> list:
        reports = db.query(MarketAnalysisReport).order_by(MarketAnalysisReport.created_at.desc()).all()
        return [
            {
                "id": r.id,
                "instrument_name": r.instrument_name,
                "asset_type": r.asset_type,
                "ticker": r.ticker,
                "current_price": r.current_price,
                "ai_provider": r.ai_provider,
                "preview": (r.report_text or "")[:200],
                "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
            }
            for r in reports
        ]

    @staticmethod
    def get_report(db: Session, report_id: int) -> dict | None:
        r = db.query(MarketAnalysisReport).filter(MarketAnalysisReport.id == report_id).first()
        if not r:
            return None
        return {
            "id": r.id,
            "instrument_name": r.instrument_name,
            "asset_type": r.asset_type,
            "ticker": r.ticker,
            "current_price": r.current_price,
            "report_text": r.report_text,
            "ai_provider": r.ai_provider,
            "created_at": r.created_at.strftime("%Y-%m-%d %H:%M"),
        }

    @staticmethod
    def delete_report(db: Session, report_id: int) -> bool:
        r = db.query(MarketAnalysisReport).filter(MarketAnalysisReport.id == report_id).first()
        if r:
            db.delete(r)
            db.commit()
            return True
        return False


class AssetIndicatorCacheService:
    """Kešira izračunate tehničke indikatore + price_series u bazi (TTL 4h).

    Eliminacijom ponovnog fetch-a 30d istorije ubrzava se svaki Details
    i Reports poziv nakon prvog.
    """
    TTL_HOURS = 4

    @staticmethod
    def get(db: Session, asset_id: str, asset_type: str) -> Optional[Dict]:
        cache_key = f"{asset_id}_{asset_type}"
        row = db.query(AssetIndicatorCache).filter(
            AssetIndicatorCache.cache_key == cache_key
        ).first()
        if row and row.expires_at > datetime.utcnow():
            return {
                "current_price": row.current_price,
                "change_percent": row.change_percent,
                "price_series": row.price_series,
                "indicators": row.indicators,
                "cached_at": row.cached_at.isoformat(),
                "from_cache": True,
            }
        return None

    @staticmethod
    def save(
        db: Session,
        asset_id: str,
        asset_type: str,
        current_price: float,
        change_percent: float,
        price_series: List[Dict],
        indicators: Dict,
    ) -> None:
        cache_key = f"{asset_id}_{asset_type}"
        expires = datetime.utcnow() + timedelta(hours=AssetIndicatorCacheService.TTL_HOURS)
        row = db.query(AssetIndicatorCache).filter(
            AssetIndicatorCache.cache_key == cache_key
        ).first()
        if row:
            row.current_price = current_price
            row.change_percent = change_percent
            row.price_series = price_series
            row.indicators = indicators
            row.cached_at = datetime.utcnow()
            row.expires_at = expires
        else:
            row = AssetIndicatorCache(
                cache_key=cache_key,
                asset_id=asset_id,
                asset_type=asset_type,
                current_price=current_price,
                change_percent=change_percent,
                price_series=price_series,
                indicators=indicators,
                expires_at=expires,
            )
            db.add(row)
        db.commit()
