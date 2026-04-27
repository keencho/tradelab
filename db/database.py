from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker, DeclarativeBase

from config import DATABASE_URL

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(bind=engine)


class Base(DeclarativeBase):
    pass


def get_db():
    """DB 세션 생성. with문으로 사용."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _migrate():
    """간단한 컬럼 추가 마이그레이션 (Postgres, idempotent)."""
    statements = [
        "ALTER TABLE real_holdings ADD COLUMN IF NOT EXISTS is_hidden BOOLEAN NOT NULL DEFAULT FALSE",
    ]
    with engine.connect() as conn:
        for sql in statements:
            try:
                conn.execute(text(sql))
            except Exception:
                pass
        conn.commit()


def init_db():
    """테이블 생성. 앱 시작시 1회 호출."""
    from db.models import (
        Price, News, Watchlist, SignalData, Signal, Trade, PortfolioSetting,
        ResearchTicker, ResearchHistory,
        RealAccount, RealHolding, RealTrade, RealQuickWatch,
    )
    Base.metadata.create_all(bind=engine)
    _migrate()
