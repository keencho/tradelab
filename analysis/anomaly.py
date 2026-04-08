"""이상 탐지 -- z-score 기반 + 실시간 가격 감지 + LLM 해석."""

import statistics
from datetime import datetime, timedelta

from config import (
    KST, PRICE_ALERT_VS_CLOSE, PRICE_ALERT_MOMENTUM,
    SIGNAL_TYPE_NAMES, get_logger,
)
from db.database import SessionLocal
from db.models import SignalData, Watchlist
from analysis.llm import call_llm

logger = get_logger("anomaly")


# data_type별 이상 탐지 설정
# (threshold, direction_logic, description)
# direction_logic: "high_bullish" = 높으면 bullish, "high_bearish" = 높으면 bearish
ANOMALY_CONFIG = {
    "foreign_net_buy":       (2.0, "high_bullish",  "외국인이 평소보다 훨씬 많이 사고 있음"),
    "institutional_net_buy": (2.0, "high_bullish",  "기관(증권사/펀드)이 평소보다 훨씬 많이 사고 있음"),
    "short_ratio":           (2.0, "high_bearish",  "하락에 베팅하는 비율이 평소보다 높음"),
    "program_buy":           (2.0, "high_bullish",  "기관 자동매매가 대량으로 매수 중"),
    "funding_rate":          (2.0, "high_bearish",  "선물 시장 과열 — 급락 가능성 주의"),
    "open_interest":         (2.0, "high_bullish",  "선물 베팅이 급변 — 큰 움직임 예고"),
    "fear_greed":            (2.0, "high_bullish",  "시장 분위기가 극단적 — 반전 가능성"),
    "whale_transfer":        (2.0, "high_bullish",  "큰손이 대량 코인을 이동 중"),
    "reddit_buzz":           (2.5, "high_bullish",  "해외 커뮤니티에서 관심 폭발"),
    "naver_buzz":            (2.5, "high_bullish",  "네이버 종토방 게시글 폭증"),
    "insider_buy":           (1.5, "high_bullish",  "회사 내부자가 자사 주식 매수"),
    "insider_sell":          (1.5, "high_bearish",  "회사 내부자가 자사 주식 매도"),
    "insider_trade":         (1.5, "high_bearish",  "내부자 거래 공시 급증"),
    "volume_spike":          (2.5, "high_bullish",  "거래대금이 평소보다 폭증"),
    "us_vix":                (2.0, "high_bearish",  "시장 공포지수 급등 — 변동성 주의"),
    "us_yield_spread":       (2.0, "high_bearish",  "금리 역전 — 경기침체 경고"),
}


def compute_z_score(values: list[float], latest: float) -> float:
    """z-score 계산. 데이터 부족 시 0.0 반환."""
    if len(values) < 5:
        return 0.0
    mean = statistics.mean(values)
    stdev = statistics.stdev(values)
    if stdev == 0:
        return 0.0
    return (latest - mean) / stdev


def detect_anomalies(lookback_days: int = 30) -> list[dict]:
    """
    signal_data 테이블에서 최근 데이터 z-score 분석.
    이상치 발견 시 Signal 생성용 dict 리스트 반환.
    """
    session = SessionLocal()
    anomalies = []

    try:
        cutoff = datetime.now(KST).replace(tzinfo=None) - timedelta(days=lookback_days)

        combos = (
            session.query(SignalData.data_type, SignalData.ticker)
            .filter(SignalData.collected_at >= cutoff)
            .distinct()
            .all()
        )

        for data_type, ticker in combos:
            config = ANOMALY_CONFIG.get(data_type)
            if not config:
                continue
            threshold, direction_logic, desc = config

            rows = (
                session.query(SignalData.value)
                .filter(
                    SignalData.data_type == data_type,
                    SignalData.ticker == ticker,
                    SignalData.collected_at >= cutoff,
                )
                .order_by(SignalData.collected_at.asc())
                .all()
            )

            values = [r[0] for r in rows]
            if len(values) < 5:
                continue

            latest = values[-1]
            z = compute_z_score(values, latest)

            if abs(z) >= threshold:
                if direction_logic == "high_bullish":
                    direction = "bullish" if z > 0 else "bearish"
                else:
                    direction = "bearish" if z > 0 else "bullish"

                confidence = min(0.95, 0.4 + abs(z) * 0.15)

                market_row = session.query(SignalData.market).filter(
                    SignalData.data_type == data_type,
                    SignalData.ticker == ticker,
                ).first()
                market = market_row[0] if market_row else ""

                # 종목명 조회
                wl_row = session.query(Watchlist.name).filter(
                    Watchlist.ticker == ticker, Watchlist.market == market
                ).first()
                ticker_name = wl_row[0] if wl_row and wl_row[0] else ""

                # 매크로는 extra.desc에서 가져오기
                if not ticker_name and market == "macro":
                    extra_row = session.query(SignalData.extra).filter(
                        SignalData.data_type == data_type,
                        SignalData.ticker == ticker,
                    ).order_by(SignalData.collected_at.desc()).first()
                    if extra_row and extra_row[0]:
                        ticker_name = extra_row[0].get("desc", "")

                anomalies.append({
                    "ticker": ticker,
                    "ticker_name": ticker_name,
                    "signal_type": data_type,
                    "direction": direction,
                    "confidence": round(confidence, 2),
                    "description": f"{desc} (평소의 {abs(z):.1f}배)",
                    "source": "anomaly_detector",
                    "market": market,
                    "z_score": round(z, 2),
                    "raw_value": latest,
                })

    finally:
        session.close()

    logger.info(f"이상 탐지 완료: {len(anomalies)}건 감지")
    return anomalies


def detect_price_anomalies() -> list[dict]:
    """
    realtime_price 데이터로 가격 급등락 감지.
    1) 전일 종가 대비 ±PRICE_ALERT_VS_CLOSE% → price_vs_close
    2) 직전 수집가 대비 ±PRICE_ALERT_MOMENTUM% → price_momentum
    """
    session = SessionLocal()
    anomalies = []

    try:
        # 최신 realtime_price 데이터 (종목별 마지막 1건)
        from sqlalchemy import func

        latest_subq = (
            session.query(
                SignalData.ticker,
                func.max(SignalData.collected_at).label("max_at"),
            )
            .filter(SignalData.data_type == "realtime_price")
            .group_by(SignalData.ticker)
            .subquery()
        )

        latest_rows = (
            session.query(SignalData)
            .join(
                latest_subq,
                (SignalData.ticker == latest_subq.c.ticker)
                & (SignalData.collected_at == latest_subq.c.max_at),
            )
            .filter(SignalData.data_type == "realtime_price")
            .all()
        )

        for row in latest_rows:
            extra = row.extra or {}
            price = row.value
            prev_close = extra.get("prev_close", 0)
            ticker = row.ticker
            market = row.market

            if not price or not prev_close:
                continue

            # 종목명 조회
            wl_row = session.query(Watchlist.name).filter(
                Watchlist.ticker == ticker, Watchlist.market == market
            ).first()
            ticker_name = wl_row[0] if wl_row and wl_row[0] else ""

            # 마켓별 가격 포맷
            if market == "crypto":
                fmt = lambda v: f"${v:,.2f}"
            elif market == "us_stock":
                fmt = lambda v: f"${v:,.2f}"
            else:
                fmt = lambda v: f"{v:,.0f}원"

            # ── 1) 전일 종가 대비 ──
            vs_close_pct = (price - prev_close) / prev_close * 100

            if abs(vs_close_pct) >= PRICE_ALERT_VS_CLOSE:
                direction = "bullish" if vs_close_pct > 0 else "bearish"
                confidence = min(0.95, 0.5 + abs(vs_close_pct) * 0.03)
                sign = "+" if vs_close_pct > 0 else ""

                anomalies.append({
                    "ticker": ticker,
                    "ticker_name": ticker_name,
                    "signal_type": "price_vs_close",
                    "direction": direction,
                    "confidence": round(confidence, 2),
                    "description": f"전일 대비 {sign}{vs_close_pct:.1f}% ({fmt(prev_close)} → {fmt(price)})",
                    "source": "price_detector",
                    "market": market,
                    "z_score": round(vs_close_pct / 2, 2),
                    "raw_value": price,
                })

            # ── 2) 직전 수집가 대비 ──
            prev_row = (
                session.query(SignalData.value)
                .filter(
                    SignalData.data_type == "realtime_price",
                    SignalData.ticker == ticker,
                    SignalData.collected_at < row.collected_at,
                )
                .order_by(SignalData.collected_at.desc())
                .first()
            )

            if prev_row and prev_row[0]:
                prev_price = prev_row[0]
                momentum_pct = (price - prev_price) / prev_price * 100

                if abs(momentum_pct) >= PRICE_ALERT_MOMENTUM:
                    direction = "bullish" if momentum_pct > 0 else "bearish"
                    confidence = min(0.95, 0.5 + abs(momentum_pct) * 0.05)
                    sign = "+" if momentum_pct > 0 else ""

                    anomalies.append({
                        "ticker": ticker,
                        "ticker_name": ticker_name,
                        "signal_type": "price_momentum",
                        "direction": direction,
                        "confidence": round(confidence, 2),
                        "description": f"장중 {sign}{momentum_pct:.1f}% ({fmt(prev_price)} → {fmt(price)}) [5분간]",
                        "source": "price_detector",
                        "market": market,
                        "z_score": round(momentum_pct / 2, 2),
                        "raw_value": price,
                    })

    finally:
        session.close()

    logger.info(f"가격 감지 완료: {len(anomalies)}건")
    return anomalies


def get_ai_analysis(anomaly: dict) -> str:
    """LLM으로 이상치 AI 해석 요청 (초보 투자자용)."""
    signal_type = SIGNAL_TYPE_NAMES.get(anomaly["signal_type"], anomaly["signal_type"])
    direction_kr = "상승 신호" if anomaly["direction"] == "bullish" else "하락 신호"

    prompt = (
        "주식/코인 초보 투자자에게 아래 시그널을 쉽게 설명해주세요.\n"
        "전문 용어 없이, 친구한테 말하듯이.\n"
        "형식: 첫 줄에 상황 요약 1문장, 빈 줄, 행동 가이드 1~2문장.\n"
        "마크다운(**, ## 등) 쓰지 마세요. 번호 매기지 마세요.\n\n"
        f"종목: {anomaly.get('ticker_name') or anomaly['ticker']}\n"
        f"무슨 일: {signal_type} — {direction_kr}\n"
        f"상세: {anomaly['description']}\n"
    )
    result = call_llm(prompt)
    return result or ""
