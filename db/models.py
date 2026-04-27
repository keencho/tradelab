from datetime import datetime
from functools import partial

from sqlalchemy import String, Float, Integer, DateTime, Text, Enum, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
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


class Watchlist(Base):
    """워치리스트 — 시그널 감지 + 데이터 수집 대상 종목"""
    __tablename__ = "watchlist"
    __table_args__ = (
        UniqueConstraint("market", "ticker", name="uq_watchlist"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    market: Mapped[str] = mapped_column(String(10), index=True)
    ticker: Mapped[str] = mapped_column(String(30))
    name: Mapped[str] = mapped_column(String(100))
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class SignalData(Base):
    """시그널 원본 데이터 (주기적 수집, z-score 계산용)"""
    __tablename__ = "signal_data"
    __table_args__ = (
        UniqueConstraint("source", "data_type", "ticker", "collected_at", name="uq_signal_data"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source: Mapped[str] = mapped_column(String(30), index=True)
    data_type: Mapped[str] = mapped_column(String(50), index=True)
    ticker: Mapped[str] = mapped_column(String(30), default="")
    market: Mapped[str] = mapped_column(String(10), default="")
    value: Mapped[float] = mapped_column(Float)
    extra: Mapped[dict] = mapped_column(JSONB, default=dict)
    collected_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst, index=True)


class Signal(Base):
    """선행 시그널 (이상 탐지 결과)"""
    __tablename__ = "signals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(20), index=True)
    signal_type: Mapped[str] = mapped_column(String(50))
    direction: Mapped[str] = mapped_column(String(10))
    confidence: Mapped[float] = mapped_column(Float)
    description: Mapped[str] = mapped_column(Text)
    ai_analysis: Mapped[str] = mapped_column(Text, default="")
    source: Mapped[str] = mapped_column(String(30), default="")
    market: Mapped[str] = mapped_column(String(10), default="")
    z_score: Mapped[float] = mapped_column(Float, default=0.0)
    raw_value: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class ResearchTicker(Base):
    """리서치 종목 (main)"""
    __tablename__ = "research_tickers"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True)
    ticker_name: Mapped[str] = mapped_column(String(100), default="")
    market: Mapped[str] = mapped_column(String(10), default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)
    last_researched_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class ResearchHistory(Base):
    """리서치 이력 (sub)"""
    __tablename__ = "research_history"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    research_ticker_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True)
    market: Mapped[str] = mapped_column(String(10), default="")
    price: Mapped[float] = mapped_column(Float, default=0.0)
    prev_close: Mapped[float] = mapped_column(Float, default=0.0)
    change_pct: Mapped[float] = mapped_column(Float, default=0.0)
    news_data: Mapped[dict] = mapped_column(JSONB, default=list)
    signals_data: Mapped[dict] = mapped_column(JSONB, default=list)
    ai_report: Mapped[str] = mapped_column(Text, default="")
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


# ── 실투자 (sycho 전용) ─────────────────────────────────────

class RealAccount(Base):
    """실제 증권/거래소 계좌"""
    __tablename__ = "real_accounts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    owner: Mapped[str] = mapped_column(String(30), index=True, default="sycho")
    broker: Mapped[str] = mapped_column(String(20))             # toss/samsung/kis/upbit/...
    account_type: Mapped[str] = mapped_column(String(20))       # regular_kr/regular_oversea/isa/pension/irp/crypto
    nickname: Mapped[str] = mapped_column(String(50), default="")
    currency: Mapped[str] = mapped_column(String(10), default="KRW")  # KRW/USD/USDT
    is_active: Mapped[bool] = mapped_column(default=True)
    sort_order: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class RealHolding(Base):
    """잔고 캐시 — 거래 입력 시 서비스단에서 갱신"""
    __tablename__ = "real_holdings"
    __table_args__ = (
        UniqueConstraint("account_id", "ticker", name="uq_real_holding"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(30))
    ticker_name: Mapped[str] = mapped_column(String(100), default="")
    market: Mapped[str] = mapped_column(String(10))             # kr_stock/us_stock/crypto
    qty: Mapped[float] = mapped_column(Float, default=0.0)
    avg_cost: Mapped[float] = mapped_column(Float, default=0.0)  # 이동평균 (수수료 포함)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    is_hidden: Mapped[bool] = mapped_column(default=False)  # 총 자산/원금 집계에서 제외
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst)


class RealTrade(Base):
    """실거래 내역"""
    __tablename__ = "real_trades"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    account_id: Mapped[int] = mapped_column(Integer, index=True)
    ticker: Mapped[str] = mapped_column(String(30), index=True)
    ticker_name: Mapped[str] = mapped_column(String(100), default="")
    market: Mapped[str] = mapped_column(String(10))
    side: Mapped[str] = mapped_column(String(10))               # buy/sell/dividend
    qty: Mapped[float] = mapped_column(Float)
    price: Mapped[float] = mapped_column(Float)
    fee: Mapped[float] = mapped_column(Float, default=0.0)
    tax: Mapped[float] = mapped_column(Float, default=0.0)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)  # 매도건만 계산
    executed_at: Mapped[datetime] = mapped_column(DateTime, default=_now_kst, index=True)
    memo: Mapped[str] = mapped_column(String(200), default="")
