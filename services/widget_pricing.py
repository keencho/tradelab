"""한국주식 위젯 가격 — 세션 자동 분기 + 전날 NXT 종가 기준 %.

세션 (KST 평일):
    08:00 - 08:50  NXT 프리마켓     → NXT 실시간 (overMarketPriceInfo.overPrice)
    08:50 - 09:00  KRX 동시호가     → 직전 NXT 종가 (전날 NXT)
    09:00 - 15:20  정규장          → KRX 실시간 (polling)
    15:20 - 15:30  KRX 마감 동시호가 → KRX 마감 가격 (closePrice)
    15:30 - 20:00  NXT 애프터마켓    → NXT 실시간 (overMarketPriceInfo.overPrice)
    20:00 - 다음날  휴장            → 오늘 NXT 종가 (= 방금 마감가)

휴장 후~다음 거래일 시작 전: "오늘 NXT 종가" 가 "전날 NXT 종가" 가 됨.

% 기준:
    토스 표시 방식 = (현재 표시가 - 전날 NXT 종가) / 전날 NXT 종가 * 100
    "전날 NXT 종가" = 마지막으로 끝난 NXT 애프터마켓의 종가
    캐시 없으면 KRX 어제 종가 (lastClosePrice) 로 폴백.
"""

import json
import os
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Literal

import httpx

from config import KST, get_logger
from data.signal_collectors import _parse_naver_number

logger = get_logger("widget_pricing")

SessionType = Literal[
    "pre_market",       # NXT 프리 08:00-08:50
    "pre_market_break", # 08:50-09:00 (NXT 정지)
    "regular",          # 정규장 09:00-15:20
    "regular_close_auction",  # 15:20-15:30 KRX 마감 동시호가
    "after_market",     # NXT 애프터 15:30-20:00
    "closed",           # 그 외 시간
    "holiday",          # 주말
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
    """현재 KST 기준 세션 판별."""
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


# ── 전날 NXT 종가 캐시 (JSON 파일) ──────────────────────

CACHE_FILE = Path(__file__).parent.parent / "data" / "cache" / "nxt_close.json"
CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)

_nxt_close_cache: dict[str, dict[str, float]] | None = None  # {ticker: {YYYY-MM-DD: price}}


def _load_cache() -> dict:
    global _nxt_close_cache
    if _nxt_close_cache is not None:
        return _nxt_close_cache
    if CACHE_FILE.exists():
        try:
            with open(CACHE_FILE, "r", encoding="utf-8") as f:
                _nxt_close_cache = json.load(f)
        except Exception as e:
            logger.error(f"nxt_close cache 로드 실패: {e}")
            _nxt_close_cache = {}
    else:
        _nxt_close_cache = {}
    return _nxt_close_cache


def _save_cache() -> None:
    if _nxt_close_cache is None:
        return
    try:
        tmp = CACHE_FILE.with_suffix(".tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_nxt_close_cache, f, ensure_ascii=False)
        os.replace(tmp, CACHE_FILE)
    except Exception as e:
        logger.error(f"nxt_close cache 저장 실패: {e}")


def save_nxt_close(ticker: str, the_date: date, price: float) -> None:
    """특정 날짜의 NXT 종가 캐싱 (cron 에서 호출)."""
    if price <= 0:
        return
    cache = _load_cache()
    bucket = cache.setdefault(ticker, {})
    bucket[the_date.strftime("%Y-%m-%d")] = price
    # 90일 넘은 것 정리
    cutoff = (the_date - timedelta(days=90)).strftime("%Y-%m-%d")
    bucket_new = {k: v for k, v in bucket.items() if k >= cutoff}
    cache[ticker] = bucket_new
    _save_cache()


def get_prev_nxt_close(ticker: str, now: datetime | None = None) -> float | None:
    """'전날 NXT 종가' = 직전 거래일의 NXT 애프터마켓 마감가.

    오늘 애프터장이 아직 진행 전이면 → 직전 거래일.
    오늘 애프터장이 끝났으면 → 오늘 (이미 마감됐으니).
    """
    cache = _load_cache()
    bucket = cache.get(ticker, {})
    if not bucket:
        return None
    n = now or datetime.now(KST)
    today_str = n.strftime("%Y-%m-%d")
    sess = kr_session(n)

    # 오늘 애프터장이 이미 끝났으면 (20:00 이후) 오늘 NXT 종가 사용
    if sess == "closed" and n.hour >= 20 and today_str in bucket:
        return bucket[today_str]

    # 그 외엔 today 보다 작은 가장 최근 날짜 사용
    candidates = sorted([d for d in bucket if d < today_str], reverse=True)
    if candidates:
        return bucket[candidates[0]]
    return None


# ── Naver Fetch ───────────────────────────────────────


_HEADERS = {"User-Agent": "Mozilla/5.0"}


def _fetch_basic(ticker: str) -> dict:
    r = httpx.get(
        f"https://m.stock.naver.com/api/stock/{ticker}/basic",
        headers=_HEADERS, timeout=10,
    )
    r.raise_for_status()
    return r.json()


def _fetch_integration_lastclose(ticker: str) -> tuple[float, str]:
    """KRX 어제 종가 (lastClosePrice) + 이름."""
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
    """정규장 KRX 실시간 (7초 갱신)."""
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


# ── Public API ────────────────────────────────────────


@dataclass
class WidgetPrice:
    price: float
    prev_close: float       # % 기준 가격 (전날 NXT 종가 우선, 없으면 KRX 어제 종가)
    change_pct: float
    name: str
    session: SessionType
    session_label: str
    prev_close_kind: str    # "nxt" / "krx_fallback"


def fetch_kr_widget_price(ticker: str) -> WidgetPrice:
    """위젯용 한국주식 가격 — 세션 자동 분기."""
    sess = kr_session()
    label = SESSION_LABEL[sess]
    basic = {}
    try:
        basic = _fetch_basic(ticker)
    except Exception as e:
        logger.error(f"basic [{ticker}]: {e}")

    name = basic.get("stockName", "")
    over = basic.get("overMarketPriceInfo") or {}
    close_price_krx = _parse_naver_number(basic.get("closePrice", "0"))
    over_price = _parse_naver_number(over.get("overPrice", "0"))

    # 세션별 표시 가격 결정
    price = 0.0
    if sess == "regular":
        # 정규장 실시간 (polling 우선, 폴백 basic)
        polled = _fetch_polling_krx(ticker)
        price = polled if polled > 0 else close_price_krx
    elif sess == "pre_market" or sess == "after_market":
        # NXT 라이브
        if over_price > 0 and over.get("overMarketStatus") == "OPEN":
            price = over_price
        else:
            price = close_price_krx  # 폴백
    elif sess in ("regular_close_auction", "pre_market_break"):
        # KRX 마감/대기 가격 (현 KRX closePrice 가 곧 마감가)
        price = close_price_krx
    else:  # closed / holiday
        # 가장 최근에 알려진 가격 — Naver 가 주는 closePrice (보통 KRX 마지막 종가)
        # 단 NXT over_price 가 더 최근이면 그걸 우선 (서버 시간 vs basic 응답 시간 격차)
        if over_price > 0 and over.get("overMarketStatus") != "OPEN":
            price = over_price
        else:
            price = close_price_krx

    # % 기준: 전날 NXT 종가
    prev_close = get_prev_nxt_close(ticker)
    prev_close_kind = "nxt"
    if not prev_close or prev_close <= 0:
        prev_close, ic_name = _fetch_integration_lastclose(ticker)
        prev_close_kind = "krx_fallback"
        name = name or ic_name

    change_pct = 0.0
    if price > 0 and prev_close > 0:
        change_pct = (price - prev_close) / prev_close * 100

    return WidgetPrice(
        price=price,
        prev_close=prev_close,
        change_pct=change_pct,
        name=name,
        session=sess,
        session_label=label,
        prev_close_kind=prev_close_kind,
    )


# ── NXT close 캐시 갱신 (cron 용) ─────────────────────


def collect_nxt_close_for(ticker: str, the_date: date | None = None) -> float | None:
    """특정 ticker 의 오늘(또는 지정일) NXT 애프터마켓 종가 수집.

    20:05+ 에 호출해야 정확. /basic 의 overPrice 가 곧 종가.
    """
    if the_date is None:
        the_date = datetime.now(KST).date()
    try:
        basic = _fetch_basic(ticker)
    except Exception as e:
        logger.error(f"NXT 종가 수집 실패 [{ticker}]: {e}")
        return None

    over = basic.get("overMarketPriceInfo") or {}
    over_price = _parse_naver_number(over.get("overPrice", "0"))
    if over_price <= 0:
        return None

    # 안전장치 — 현재 시각이 20:00 이전이면 아직 진행 중일 수 있음, 그래도 저장 (덮어쓰기)
    save_nxt_close(ticker, the_date, over_price)
    return over_price
