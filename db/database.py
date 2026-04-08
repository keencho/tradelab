from sqlalchemy import create_engine
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


def init_db():
    """테이블 생성. 앱 시작시 1회 호출."""
    from db.models import Price, News, SignalData, Signal, Trade, PortfolioSetting, ResearchTicker, ResearchHistory
    Base.metadata.create_all(bind=engine)
