from datetime import datetime
from functools import partial

from sqlalchemy import String, Float, Integer, DateTime, Text, Enum
from sqlalchemy.orm import Mapped, mapped_column

from config import KST
from db.database import Base


def _now_kst():
    """KST 현재시각을 naive datetime으로 반환 (DB 저장용)."""
    return datetime.now(KST).replace(tzinfo=None)


class Price(Base):
    """가격 히스토리 (주식 + 코인)"""
    __tablename__ = "prices"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    market: Mapped[str] = mapped_column(String(10))  # "stock" | "crypto"
    dt: Mapped[datetime] = mapped_column(DateTime, index=True)
    open: Mapped[float] = mapped_column(Float)
    high: Mapped[float] = mapped_column(Float)
    low: Mapped[float] = mapped_column(Float)
    close: Mapped[float] = mapped_column(Float)
    volume: Mapped[float] = mapped_column(Float)


class News(Base):
    """뉴스 + 센티멘트 분석 결과"""
    __tablename__ = "news"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(500))
    url: Mapped[str] = mapped_column(String(1000), unique=True, default="")
    source: Mapped[str] = mapped_column(String(100))
    content: Mapped[str] = mapped_column(Text, default="")
    sentiment_label: Mapped[str] = mapped_column(String(10), default="")  # positive / negative / neutral
    sentiment_score: Mapped[float] = mapped_column(Float, default=0.0)    # -1.0 ~ 1.0
    impact: Mapped[int] = mapped_column(Integer, default=0)               # 1 ~ 10
    related_tickers: Mapped[str] = mapped_column(String(200), default="") # 쉼표 구분
    summary: Mapped[str] = mapped_column(String(500), default="")
    published_at: Mapped[datetime] = mapped_column(DateTime)
    analyzed_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class Signal(Base):
    """선행 시그널 (이상 탐지 결과)"""
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    signal_type: Mapped[str] = mapped_column(String(50))   # "whale_alert" | "insider_trade" | "social_buzz" | ...
    direction: Mapped[str] = mapped_column(String(10))      # "bullish" | "bearish" | "neutral"
    confidence: Mapped[float] = mapped_column(Float)         # 0.0 ~ 1.0
    description: Mapped[str] = mapped_column(Text)
    ai_analysis: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class Trade(Base):
    """가상매매 거래 내역"""
    __tablename__ = "trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    market: Mapped[str] = mapped_column(String(10))          # "stock" | "crypto"
    side: Mapped[str] = mapped_column(String(4))              # "buy" | "sell"
    quantity: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class PortfolioSetting(Base):
    """포트폴리오 설정"""
    __tablename__ = "portfolio_settings"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    initial_capital: Mapped[float] = mapped_column(Float, default=100_000_000)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)
