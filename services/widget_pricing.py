"""위젯 가격 — 한국주식 + 미국주식 세션 자동 분기.

KR 세션 (KST 평일):
    08:00 - 08:50  NXT 프리마켓     → NXT 실시간 (overPrice)
    08:50 - 09:00  KRX 동시호가     → 직전 KRX 종가 (closePrice)
    09:00 - 15:20  정규장          → KRX 실시간 (polling)
    15:20 - 15:30  KRX 마감 동시호가 → KRX 가격 (closePrice)
    15:30 - 20:00  NXT 애프터마켓    → NXT 실시간 (overPrice)
    20:00 - 다음날  휴장            → 가장 최근 NXT/KRX 종가

KR % 기준 (토스 방식, KRX 종가 기준):
    - 정규장 진행 중: ref = lastClosePrice (어제 KRX 정규장 종가)
    - 그 외 시간: ref = closePrice (가장 최근 KRX 정규장 종가)

US 세션 (KST 기준, DST 자동 반영 — Yahoo currentTradingPeriod 사용):
    DST(3~11월):  17:00 프리 → 22:30 정규 → 05:00 애프터 → 09:00 마감
    표준시간:      18:00 프리 → 23:30 정규 → 06:00 애프터 → 10:00 마감

US % 기준 (KR 와 동일 패턴):
    - 정규장 진행 중: ref = previousClose (어제 정규장 종가) → "오늘 정규장 누적 변동"
    - 프리장:        ref = regularMarketPrice (= 어제 종가) → "프리장 변동"
    - 애프터장:       ref = regularMarketPrice (= 오늘 정규장 종가) → "애프터 변동"
    - 장 마감:       ref = previousClose → "오늘 정규장 결과"
"""

import time
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
    "us_pre_market", "us_regular", "us_after_market", "us_closed",
]

SESSION_LABEL = {
    "pre_market": "프리장",
    "pre_market_break": "프리장 마감",
    "regular": "정규장",
    "regular_close_auction": "정규장 마감",
    "after_market": "애프터장",
    "closed": "장 마감",
    "holiday": "휴장",
    "us_pre_market": "프리장",
    "us_regular": "정규장",
    "us_after_market": "애프터장",
    "us_closed": "장 마감",
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


# ── US (Yahoo Finance) ───────────────────────────────


def _us_session_from_periods(periods: dict, now_ts: float | None = None) -> SessionType:
    """Yahoo currentTradingPeriod 기반 현재 세션 (UTC 기준)."""
    n = now_ts if now_ts is not None else time.time()
    for key, sess in (
        ("pre", "us_pre_market"),
        ("regular", "us_regular"),
        ("post", "us_after_market"),
    ):
        p = periods.get(key) or {}
        start = p.get("start", 0)
        end = p.get("end", 0)
        if start and end and start <= n < end:
            return sess
    return "us_closed"


def fetch_us_widget_price(ticker: str) -> WidgetPrice:
    """Yahoo chart API — 프리/정규/애프터/마감 자동 분기."""
    try:
        r = httpx.get(
            f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            params={"interval": "1m", "includePrePost": "true", "range": "1d"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        r.raise_for_status()
        result = r.json().get("chart", {}).get("result") or []
        if not result:
            raise ValueError("empty chart result")
        meta = result[0].get("meta") or {}
    except Exception as e:
        logger.error(f"yahoo [{ticker}]: {e}")
        return WidgetPrice(
            price=0.0, prev_close=0.0, change_pct=0.0, name="",
            session="us_closed", session_label=SESSION_LABEL["us_closed"],
        )

    def _f(v) -> float:
        try:
            return float(v) if v is not None else 0.0
        except (TypeError, ValueError):
            return 0.0

    regular = _f(meta.get("regularMarketPrice"))
    pre = _f(meta.get("preMarketPrice"))
    post = _f(meta.get("postMarketPrice"))
    prev_close = _f(meta.get("previousClose")) or _f(meta.get("chartPreviousClose"))
    name = meta.get("shortName") or meta.get("longName") or ""

    sess = _us_session_from_periods(meta.get("currentTradingPeriod") or {})

    if sess == "us_pre_market":
        price = pre if pre > 0 else regular
        ref = regular if regular > 0 else prev_close
    elif sess == "us_regular":
        price = regular
        ref = prev_close
    elif sess == "us_after_market":
        price = post if post > 0 else regular
        ref = regular if regular > 0 else prev_close
    else:  # us_closed
        price = regular
        ref = prev_close

    change_pct = 0.0
    if price > 0 and ref > 0:
        change_pct = (price - ref) / ref * 100

    return WidgetPrice(
        price=price, prev_close=ref, change_pct=change_pct, name=name,
        session=sess, session_label=SESSION_LABEL[sess],
    )
