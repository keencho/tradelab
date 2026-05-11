"""한국주식 위젯 가격 — 세션 자동 분기.

세션 (KST 평일):
    08:00 - 08:50  NXT 프리마켓     → NXT 실시간 (overPrice)
    08:50 - 09:00  KRX 동시호가     → 직전 KRX 종가 (closePrice)
    09:00 - 15:20  정규장          → KRX 실시간 (polling)
    15:20 - 15:30  KRX 마감 동시호가 → KRX 가격 (closePrice)
    15:30 - 20:00  NXT 애프터마켓    → NXT 실시간 (overPrice)
    20:00 - 다음날  휴장            → 가장 최근 NXT/KRX 종가

% 기준 (토스 방식, KRX 종가 기준):
    - 정규장 진행 중 (09:00-15:30): ref = lastClosePrice (어제 KRX 정규장 종가)
    - 그 외 시간: ref = closePrice (가장 최근 KRX 정규장 종가 = 오늘 또는 직전 거래일)

→ NXT 종가는 ref 가 아님. KRX 종가만으로 충분.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Literal

import httpx

from config import KST, get_logger
from data.signal_collectors import _parse_naver_number

logger = get_logger("widget_pricing")

SessionType = Literal[
    "pre_market", "pre_market_break", "regular", "regular_close_auction",
    "after_market", "closed", "holiday",
]

SESSION_LABEL = {
    "pre_market": "프리장",
    "pre_market_break": "프리장 마감",
    "regular": "정규장",
    "regular_close_auction": "정규장 마감",
    "after_market": "애프터장",
    "closed": "장 마감",
    "holiday": "휴장",
}


def kr_session(now: datetime | None = None) -> SessionType:
    n = now or datetime.now(KST)
    if n.weekday() >= 5:
        return "holiday"
    m = n.hour * 60 + n.minute
    if 8 * 60 <= m < 8 * 60 + 50:
        return "pre_market"
    if 8 * 60 + 50 <= m < 9 * 60:
        return "pre_market_break"
    if 9 * 60 <= m < 15 * 60 + 20:
        return "regular"
    if 15 * 60 + 20 <= m < 15 * 60 + 30:
        return "regular_close_auction"
    if 15 * 60 + 30 <= m < 20 * 60:
        return "after_market"
    return "closed"


# ── Naver fetch ────────────────────────────────────────


_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_basic(ticker: str) -> dict:
    r = httpx.get(
        f"https://m.stock.naver.com/api/stock/{ticker}/basic",
        headers=_HEADERS, timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _fetch_integration_lastclose(ticker: str) -> tuple[float, str]:
    """어제 KRX 정규장 종가 + 이름 — m.stock /integration lastClosePrice."""
    try:
        r = httpx.get(
            f"https://m.stock.naver.com/api/stock/{ticker}/integration",
            headers=_HEADERS, timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        prev_close = 0.0
        for x in data.get("totalInfos", []):
            if x.get("code") == "lastClosePrice":
                prev_close = _parse_naver_number(x.get("value", "0"))
                break
        return prev_close, data.get("stockName", "")
    except Exception as e:
        logger.error(f"integration [{ticker}]: {e}")
        return 0.0, ""


def _fetch_polling_krx(ticker: str) -> float:
    """정규장 KRX 실시간 — polling.finance.naver.com (7초 갱신)."""
    try:
        r = httpx.get(
            f"https://polling.finance.naver.com/api/realtime/domestic/stock/{ticker}",
            headers={**_HEADERS, "Referer": "https://finance.naver.com/"},
            timeout=10,
        )
        r.raise_for_status()
        d = r.json().get("datas") or []
        if d:
            return _parse_naver_number(d[0].get("closePrice", "0"))
    except Exception as e:
        logger.error(f"polling [{ticker}]: {e}")
    return 0.0


# ── Public ───────────────────────────────────────────


@dataclass
class WidgetPrice:
    price: float
    prev_close: float
    change_pct: float
    name: str
    session: SessionType
    session_label: str


def fetch_kr_widget_price(ticker: str) -> WidgetPrice:
    sess = kr_session()
    try:
        basic = _fetch_basic(ticker)
    except Exception as e:
        logger.error(f"basic [{ticker}]: {e}")
        basic = {}

    name = basic.get("stockName", "")
    over = basic.get("overMarketPriceInfo") or {}
    close_price_krx = _parse_naver_number(basic.get("closePrice", "0"))
    over_price = _parse_naver_number(over.get("overPrice", "0"))
    over_open = over.get("overMarketStatus") == "OPEN"

    # 표시 가격
    if sess == "regular":
        polled = _fetch_polling_krx(ticker)
        price = polled if polled > 0 else close_price_krx
    elif sess in ("pre_market", "after_market") and over_open:
        price = over_price
    elif sess in ("closed", "holiday"):
        # 휴장: NXT 마지막 가격이 더 최근이면 우선 (애프터 마감 후)
        price = over_price if over_price > 0 else close_price_krx
    else:  # regular_close_auction / pre_market_break / NXT 정지 중
        price = close_price_krx

    # % 기준 (토스 방식)
    # 정규장 진행 중 → 어제 KRX 종가 (lastClosePrice)
    # 그 외 → 가장 최근 KRX 종가 (closePrice)
    if sess in ("regular", "regular_close_auction"):
        ref, ic_name = _fetch_integration_lastclose(ticker)
        if ref <= 0:
            ref = close_price_krx
        name = name or ic_name
    else:
        ref = close_price_krx if close_price_krx > 0 else 0.0

    change_pct = 0.0
    if price > 0 and ref > 0:
        change_pct = (price - ref) / ref * 100

    return WidgetPrice(
        price=price, prev_close=ref, change_pct=change_pct, name=name,
        session=sess, session_label=SESSION_LABEL[sess],
    )
