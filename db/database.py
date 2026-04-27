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
        # 가상매매 — 기존 trades 테이블 스키마 확장
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS ticker_name VARCHAR(100) NOT NULL DEFAULT ''",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS tax DOUBLE PRECISION NOT NULL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS fx_rate DOUBLE PRECISION NOT NULL DEFAULT 1",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS realized_pnl DOUBLE PRECISION NOT NULL DEFAULT 0",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS broker VARCHAR(20) NOT NULL DEFAULT ''",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS memo VARCHAR(200) NOT NULL DEFAULT ''",
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS executed_at TIMESTAMP",
        "UPDATE trades SET executed_at = created_at WHERE executed_at IS NULL",
        # quantity → qty (qty 컬럼이 없으면 추가 + quantity 데이터 복사)
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS qty DOUBLE PRECISION",
        "UPDATE trades SET qty = quantity WHERE qty IS NULL AND quantity IS NOT NULL",
        "ALTER TABLE trades ALTER COLUMN qty SET NOT NULL",
        # 레거시 quantity / created_at 컬럼 제거 (기존 ORM 에서 사용 안 함, executed_at 으로 대체)
        "ALTER TABLE trades DROP COLUMN IF EXISTS quantity",
        "ALTER TABLE trades DROP COLUMN IF EXISTS created_at",
        # side VARCHAR(4) → VARCHAR(10)
        "ALTER TABLE trades ALTER COLUMN side TYPE VARCHAR(10)",
        "ALTER TABLE trades ALTER COLUMN ticker TYPE VARCHAR(30)",
        "ALTER TABLE trades ALTER COLUMN executed_at SET NOT NULL",
        # RealQuickWatch — currency 컬럼
        "ALTER TABLE real_quick_watch ADD COLUMN IF NOT EXISTS currency VARCHAR(10) NOT NULL DEFAULT 'KRW'",
        "ALTER TABLE real_quick_watch DROP CONSTRAINT IF EXISTS uq_real_quick_watch",
        "ALTER TABLE real_quick_watch ADD CONSTRAINT uq_real_quick_watch UNIQUE (owner, market, ticker, currency)",
        # 가상매매 user별 격리 — owner 컬럼 추가
        "ALTER TABLE trades ADD COLUMN IF NOT EXISTS owner VARCHAR(30) NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_trades_owner ON trades (owner)",
        "ALTER TABLE paper_holdings ADD COLUMN IF NOT EXISTS owner VARCHAR(30) NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_paper_holdings_owner ON paper_holdings (owner)",
        "ALTER TABLE paper_holdings DROP CONSTRAINT IF EXISTS uq_paper_holding",
        "ALTER TABLE paper_holdings ADD CONSTRAINT uq_paper_holding UNIQUE (owner, ticker, market)",
        "ALTER TABLE portfolio_settings ADD COLUMN IF NOT EXISTS owner VARCHAR(30) NOT NULL DEFAULT ''",
        "CREATE INDEX IF NOT EXISTS ix_portfolio_settings_owner ON portfolio_settings (owner)",
        "ALTER TABLE portfolio_settings DROP CONSTRAINT IF EXISTS uq_portfolio_setting_owner",
        "ALTER TABLE portfolio_settings ADD CONSTRAINT uq_portfolio_setting_owner UNIQUE (owner)",
    ]
    # 각 statement 를 독립 transaction 으로 — 한 개 실패해도 다음 statement 진행
    for sql in statements:
        try:
            with engine.begin() as conn:
                conn.execute(text(sql))
        except Exception:
            pass


def init_db():
    """테이블 생성. 앱 시작시 1회 호출."""
    from db.models import (
        Price, News, Watchlist, SignalData, Signal, Trade, PortfolioSetting,
        PaperHolding,
        ResearchTicker, ResearchHistory,
        RealAccount, RealHolding, RealTrade, RealQuickWatch,
    )
    Base.metadata.create_all(bind=engine)
    _migrate()
