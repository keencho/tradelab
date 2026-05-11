import asyncio
import re
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from config import (
    KST, FINNHUB_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET,
    SIGNAL_TYPE_NAMES, BROKER_FEES, BROKER_NAMES,
    ACCOUNT_TYPE_NAMES, ACCOUNT_TYPE_MARKET, IS_LOCAL, get_logger,
)
from db.database import SessionLocal
from db.models import (
    Signal, ResearchTicker, ResearchHistory,
    RealAccount, RealHolding, RealTrade, RealQuickWatch,
    Trade, PaperHolding, PortfolioSetting,
)
from analysis.llm import call_llm
from routes.auth import reset_session, require_auth, logout, get_current_user, COOKIE_NAME
from services import real_trader, paper_trader

router = APIRouter()
logger = get_logger("api")


# ── 세션/인증 ────────────────────────────────────────────────

@router.post("/session/reset")
async def session_reset(request: Request):
    """세션 리셋 — 24시간 타이머 재시작."""
    if not require_auth(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    response = JSONResponse(content={"status": "ok"})
    reset_session(request, response)
    return response


@router.post("/logout")
async def api_logout(request: Request):
    """로그아웃 — 세션 삭제 + 브라우저 Basic Auth 캐시 초기화."""
    response = Response(
        status_code=401,
        headers={"WWW-Authenticate": "Basic realm='TradeLab'"},
    )
    response.delete_cookie(COOKIE_NAME)
    logout(request)
    return response


# ── 종목 검색 (자동완성) ──────────────────────────────────────

# ── 종목 검색 (자동완성) — 외부 실시간 검색 ─────────────────

@router.get("/ticker/search")
async def ticker_search(q: str = "", market: str = ""):
    """외부 API에서 실시간 종목 검색. market 있으면 해당 마켓만."""
    if not q or len(q) < 1:
        return []

    existing = set()
    results = []

    if not market or market == "kr_stock":
        results.extend(_search_kr(q, existing))
    if not market or market == "us_stock":
        results.extend(_search_us(q, existing))
    if not market or market == "crypto":
        results.extend(_search_crypto(q))

    return results[:15]


def _search_kr(q: str, existing: set) -> list[dict]:
    """네이버 자동완성 API로 한국주식 검색."""
    results = []
    try:
        resp = httpx.get(
            "https://ac.stock.naver.com/ac",
            params={"q": q, "target": "stock"},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=5,
        )
        resp.raise_for_status()
        data = resp.json()

        for item in data.get("items", []):
            ticker = item.get("code", "")
            name = item.get("name", "")
            type_code = item.get("typeCode", "")
            if ticker and ticker not in existing and type_code in ("KOSPI", "KOSDAQ"):
                existing.add(ticker)
                results.append({
                    "ticker": ticker,
                    "name": name,
                    "market": "kr_stock",
                })
            if len(results) >= 5:
                break
    except Exception as e:
        logger.error(f"네이버 종목검색: {e}")
    return results


def _search_us(q: str, existing: set) -> list[dict]:
    """Finnhub symbol lookup으로 미국주식 검색."""
    if not FINNHUB_API_KEY:
        return []
    results = []
    try:
        resp = httpx.get(
            "https://finnhub.io/api/v1/search",
            params={"q": q, "token": FINNHUB_API_KEY},
            timeout=5,
        )
        resp.raise_for_status()
        for item in resp.json().get("result", [])[:5]:
            ticker = item.get("symbol", "")
            if ticker and ticker not in existing and "." not in ticker:
                existing.add(ticker)
                results.append({
                    "ticker": ticker,
                    "name": item.get("description", ""),
                    "market": "us_stock",
                })
    except Exception:
        pass
    return results


def _search_crypto(q: str) -> list[dict]:
    """코인 검색 (주요 코인 하드코딩 + 쿼리 매칭)."""
    COINS = [
        ("BTC", "비트코인"), ("ETH", "이더리움"), ("SOL", "솔라나"),
        ("XRP", "리플"), ("BNB", "바이낸스코인"), ("DOGE", "도지코인"),
        ("ADA", "카르다노"), ("AVAX", "아발란체"), ("DOT", "폴카닷"),
        ("MATIC", "폴리곤"), ("LINK", "체인링크"), ("UNI", "유니스왑"),
        ("ATOM", "코스모스"), ("APT", "앱토스"), ("ARB", "아비트럼"),
        ("OP", "옵티미즘"), ("SUI", "수이"), ("SEI", "세이"),
        ("NEAR", "니어"), ("FIL", "파일코인"),
    ]
    q_lower = q.lower()
    results = []
    for ticker, name in COINS:
        if q_lower in ticker.lower() or q_lower in name:
            results.append({"ticker": ticker, "name": name, "market": "crypto"})
        if len(results) >= 5:
            break
    return results


# ── 워치리스트 CRUD ───────────────────────────────────────────

@router.get("/watchlist")
async def get_watchlist():
    """워치리스트 전체 조회."""
    from db.models import Watchlist
    session = SessionLocal()
    try:
        rows = session.query(Watchlist).filter(
            Watchlist.is_active == True
        ).order_by(Watchlist.market, Watchlist.sort_order).all()
        return [{
            "id": r.id,
            "market": r.market,
            "ticker": r.ticker,
            "name": r.name,
            "sort_order": r.sort_order,
        } for r in rows]
    finally:
        session.close()


@router.post("/watchlist")
async def add_watchlist(request: Request):
    """워치리스트 종목 추가."""
    from db.models import Watchlist
    from config import MAX_WATCHLIST

    body = await request.json()
    market = body.get("market", "").strip()
    ticker = body.get("ticker", "").strip()
    name = body.get("name", "").strip()

    if not market or not ticker:
        return JSONResponse(status_code=400, content={"error": "market, ticker required"})

    session = SessionLocal()
    try:
        # 중복 체크
        exists = session.query(Watchlist).filter(
            Watchlist.market == market, Watchlist.ticker == ticker
        ).first()
        if exists:
            return JSONResponse(status_code=409, content={"error": "이미 등록된 종목입니다"})

        # 마켓별 최대 수 체크
        count = session.query(Watchlist).filter(
            Watchlist.market == market, Watchlist.is_active == True
        ).count()
        max_count = MAX_WATCHLIST.get(market, 15)
        if count >= max_count:
            return JSONResponse(status_code=400, content={"error": f"최대 {max_count}종목까지 등록 가능합니다"})

        wl = Watchlist(
            market=market, ticker=ticker, name=name,
            is_active=True, sort_order=count,
        )
        session.add(wl)
        session.commit()
        return {"id": wl.id, "ticker": ticker, "name": name, "market": market}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.delete("/watchlist/{item_id}")
async def delete_watchlist(item_id: int):
    """워치리스트 종목 삭제."""
    from db.models import Watchlist
    session = SessionLocal()
    try:
        wl = session.query(Watchlist).filter(Watchlist.id == item_id).first()
        if not wl:
            return JSONResponse(status_code=404, content={"error": "not found"})
        session.delete(wl)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


# ── 리서치 실행 ───────────────────────────────────────────────

@router.post("/research")
async def run_research(request: Request):
    """종목 리서치 실행 — 가격 + 뉴스 + 시그널 + AI 리포트."""
    body = await request.json()
    ticker = body.get("ticker", "").strip()
    ticker_name = body.get("ticker_name", "").strip()
    market = body.get("market", "").strip()

    if not ticker:
        return JSONResponse(status_code=400, content={"error": "ticker required"})

    # 가격 fetch
    price_data = _fetch_price(ticker, market)

    # 네이버 API에서 종목명 보완
    if not ticker_name and price_data.get("name"):
        ticker_name = price_data["name"]

    # 뉴스 fetch
    news = _fetch_research_news(ticker, ticker_name, market)

    # DB 시그널 조회
    signals = _fetch_signals(ticker)

    # AI 종합 리포트
    ai_report = _generate_report(ticker, ticker_name, market, price_data, news, signals)

    # DB 저장
    session = SessionLocal()
    try:
        now = datetime.now(KST).replace(tzinfo=None)

        # main: ResearchTicker
        rt = session.query(ResearchTicker).filter(
            ResearchTicker.ticker == ticker,
            ResearchTicker.market == market,
        ).first()

        if not rt:
            rt = ResearchTicker(
                ticker=ticker, ticker_name=ticker_name,
                market=market, created_at=now, last_researched_at=now,
            )
            session.add(rt)
            session.flush()
        else:
            rt.last_researched_at = now
            if ticker_name:
                rt.ticker_name = ticker_name

        # sub: ResearchHistory
        history = ResearchHistory(
            research_ticker_id=rt.id,
            ticker=ticker, market=market,
            price=price_data.get("price", 0),
            prev_close=price_data.get("prev_close", 0),
            change_pct=price_data.get("change_pct", 0),
            news_data=news,
            signals_data=signals,
            ai_report=ai_report,
            created_at=now,
        )
        session.add(history)
        session.commit()

        return {
            "id": history.id,
            "ticker": ticker,
            "ticker_name": ticker_name or rt.ticker_name,
            "market": market,
            "price": price_data,
            "news": news,
            "signals": signals,
            "ai_report": ai_report,
            "created_at": now.isoformat(),
        }
    except Exception as e:
        session.rollback()
        logger.error(f"리서치 에러: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


# ── 차트 데이터 ───────────────────────────────────────────────

# KST 오프셋 (초)
KST_OFFSET = 9 * 3600

# interval → (Yahoo range, Yahoo interval, is_intraday)
CHART_INTERVALS = {
    # 분봉
    "1min":  {"range": "1d",  "interval": "1m",  "intraday": True},
    "5min":  {"range": "5d",  "interval": "5m",  "intraday": True},
    "15min": {"range": "5d",  "interval": "15m", "intraday": True},
    "30min": {"range": "1mo", "interval": "30m", "intraday": True},
    "60min": {"range": "1mo", "interval": "60m", "intraday": True},
    # 일봉 이상
    "D":     {"range": "3mo", "interval": "1d",  "intraday": False},
    "W":     {"range": "1y",  "interval": "1wk", "intraday": False},
    "M":     {"range": "5y",  "interval": "1mo", "intraday": False},
}

# period별 기본 range (일봉 이상에서 사용)
PERIOD_RANGE = {
    "1m": "1mo", "3m": "3mo", "6m": "6mo",
    "1y": "1y", "3y": "3y", "5y": "5y",
}


@router.get("/chart/{ticker}")
async def chart_data(ticker: str, market: str = "", interval: str = "D", period: str = "3m"):
    """캔들차트 데이터 반환.

    interval: 1min, 5min, 15min, 30min, 60min, D, W, M
    period: 1m, 3m, 6m, 1y, 3y, 5y (일봉 이상에서만 사용)
    """
    try:
        candles = _fetch_candles(ticker, market, interval, period)
        return candles
    except Exception as e:
        logger.error(f"차트 데이터 [{ticker}]: {e}")
        return []


def _fetch_candles(ticker: str, market: str, interval: str, period: str) -> list[dict]:
    """마켓별 + 봉 타입별 캔들 데이터 fetch."""
    cfg = CHART_INTERVALS.get(interval, CHART_INTERVALS["D"])
    yahoo_interval = cfg["interval"]
    is_intraday = cfg["intraday"]

    # 분봉이면 고정 range, 일봉 이상이면 period 기반 range
    yahoo_range = cfg["range"] if is_intraday else PERIOD_RANGE.get(period, "3mo")

    if market == "kr_stock":
        yahoo_ticker = f"{ticker}.KS"
        return _fetch_yahoo_candles(yahoo_ticker, yahoo_interval, yahoo_range, is_intraday)
    elif market == "us_stock":
        return _fetch_yahoo_candles(ticker, yahoo_interval, yahoo_range, is_intraday)
    elif market == "crypto":
        return _fetch_crypto_candles(ticker, interval, period)
    return []


def _fetch_yahoo_candles(yahoo_ticker: str, interval: str, range_str: str, is_intraday: bool) -> list[dict]:
    """Yahoo Finance v8 API로 캔들 데이터 fetch (한국주식 + 미국주식)."""
    resp = httpx.get(
        f"https://query1.finance.yahoo.com/v8/finance/chart/{yahoo_ticker}",
        params={"interval": interval, "range": range_str},
        headers={"User-Agent": "Mozilla/5.0"},
        timeout=15,
    )
    resp.raise_for_status()
    data = resp.json()

    chart_result = data.get("chart", {}).get("result", [])
    if not chart_result:
        return []

    result_data = chart_result[0]
    timestamps = result_data.get("timestamp", [])
    quote = result_data.get("indicators", {}).get("quote", [{}])[0]

    opens = quote.get("open", [])
    highs = quote.get("high", [])
    lows = quote.get("low", [])
    closes = quote.get("close", [])
    volumes = quote.get("volume", [])

    result = []
    for i in range(len(timestamps)):
        if opens[i] is None or closes[i] is None:
            continue

        ts = timestamps[i]
        if is_intraday:
            # 분봉: KST로 변환한 Unix timestamp
            time_val = ts + KST_OFFSET
        else:
            # 일봉: "YYYY-MM-DD" 문자열
            from datetime import datetime as _dt
            time_val = _dt.utcfromtimestamp(ts + KST_OFFSET).strftime("%Y-%m-%d")

        result.append({
            "time": time_val,
            "open": opens[i],
            "high": highs[i],
            "low": lows[i],
            "close": closes[i],
            "volume": volumes[i] or 0,
        })

    return result


def _fetch_crypto_candles(ticker: str, interval: str, period: str) -> list[dict]:
    """ccxt로 코인 캔들 데이터."""
    import ccxt

    # interval → ccxt timeframe
    tf_map = {
        "1min": "1m", "5min": "5m", "15min": "15m",
        "30min": "30m", "60min": "1h",
        "D": "1d", "W": "1w", "M": "1M",
    }
    timeframe = tf_map.get(interval, "1d")

    # limit 계산
    is_intraday = interval in ("1min", "5min", "15min", "30min", "60min")
    if is_intraday:
        limit = {"1min": 500, "5min": 500, "15min": 300, "30min": 300, "60min": 300}.get(interval, 300)
    else:
        limit = {"1m": 30, "3m": 90, "6m": 180, "1y": 365, "3y": 1000, "5y": 1000}.get(period, 90)

    exchange = ccxt.binance({"timeout": 15000, "options": {"defaultType": "future"}})
    symbol = f"{ticker}/USDT:USDT"
    ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)

    result = []
    for candle in ohlcv:
        ts = candle[0] / 1000  # ms → sec

        if is_intraday:
            time_val = int(ts) + KST_OFFSET
        else:
            from datetime import datetime as _dt
            time_val = _dt.utcfromtimestamp(ts + KST_OFFSET).strftime("%Y-%m-%d")

        result.append({
            "time": time_val,
            "open": candle[1],
            "high": candle[2],
            "low": candle[3],
            "close": candle[4],
            "volume": candle[5],
        })
    return result



# ── 내부 함수 ─────────────────────────────────────────────────

def _fetch_price(ticker: str, market: str) -> dict:
    """마켓별 실시간 가격 fetch.

    한국주식 — services.widget_pricing.fetch_kr_widget_price() 위임:
    - 세션별 자동 분기 (프리장 / 정규장 / 정규장 마감 / 애프터장 / 장 마감)
    - % 기준 = 전날 NXT 종가 (캐시) > 폴백 KRX 어제 종가
    - session_label 반환 (위젯 배지용)
    """
    try:
        if market == "kr_stock":
            from services.widget_pricing import fetch_kr_widget_price
            wp = fetch_kr_widget_price(ticker)
            return {
                "price": wp.price,
                "prev_close": wp.prev_close,
                "change_pct": wp.change_pct,
                "name": wp.name,
                "session": wp.session,
                "session_label": wp.session_label,
                "prev_close_kind": wp.prev_close_kind,
            }

        elif market == "us_stock" and FINNHUB_API_KEY:
            resp = httpx.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": ticker, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            return {
                "price": float(data.get("c", 0)),
                "prev_close": float(data.get("pc", 0)),
                "change_pct": float(data.get("dp", 0)),
            }

        elif market == "crypto":
            import ccxt
            exchange = ccxt.binance({"timeout": 15000, "options": {"defaultType": "future"}})
            symbol = f"{ticker}/USDT:USDT"
            t = exchange.fetch_ticker(symbol)
            return {
                "price": float(t.get("last", 0) or 0),
                "prev_close": float(t.get("previousClose", 0) or 0),
                "change_pct": float(t.get("percentage", 0) or 0),
            }

    except Exception as e:
        logger.error(f"가격 fetch [{ticker}]: {e}")

    return {"price": 0, "prev_close": 0, "change_pct": 0}


def _fetch_research_news(ticker: str, ticker_name: str, market: str) -> list[dict]:
    """리서치용 뉴스 fetch (제목 + URL)."""
    try:
        if market == "kr_stock":
            return _fetch_naver_finance_news(ticker)

        elif market == "us_stock" and FINNHUB_API_KEY:
            today = datetime.now(KST).strftime("%Y-%m-%d")
            week_ago = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
            resp = httpx.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": week_ago, "to": today, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json()[:10]
            return [{
                "title": item.get("headline", ""),
                "description": item.get("summary", "")[:200],
                "url": item.get("url", ""),
            } for item in items if item.get("headline")]

    except Exception as e:
        logger.error(f"리서치 뉴스 [{ticker}]: {e}")

    return []


def _fetch_naver_finance_news(ticker: str, max_items: int = 10) -> list[dict]:
    """네이버 금융 모바일 API — 해당 종목에 태깅된 기사."""
    try:
        resp = httpx.get(
            f"https://m.stock.naver.com/api/news/stock/{ticker}",
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for group in data:
            for item in group.get("items", []):
                if len(results) >= max_items:
                    break
                title = item.get("title", "").strip()
                if not title:
                    continue
                title = title.replace("&quot;", '"').replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
                results.append({
                    "title": title,
                    "description": item.get("body", "")[:150],
                    "url": item.get("mobileNewsUrl", ""),
                    "source": item.get("officeName", ""),
                })
            if len(results) >= max_items:
                break

        return results
    except Exception as e:
        logger.error(f"네이버 금융 뉴스 [{ticker}]: {e}")
        return []


def _fetch_signals(ticker: str) -> list[dict]:
    """DB에서 최근 시그널 조회."""
    session = SessionLocal()
    try:
        rows = (
            session.query(Signal)
            .filter(Signal.ticker == ticker)
            .order_by(Signal.created_at.desc())
            .limit(10)
            .all()
        )
        return [{
            "signal_type": SIGNAL_TYPE_NAMES.get(s.signal_type, s.signal_type),
            "direction": s.direction,
            "description": s.description,
            "date": s.created_at.strftime("%m/%d %H:%M"),
            "created_at": s.created_at.isoformat(),
        } for s in rows]
    finally:
        session.close()


def _generate_report(ticker: str, ticker_name: str, market: str,
                     price: dict, news: list, signals: list) -> str:
    """LLM 종합 리서치 리포트 생성."""
    name = ticker_name or ticker
    price_val = price.get("price", 0)
    change = price.get("change_pct", 0)
    sign = "+" if change > 0 else ""

    news_text = ""
    if news:
        news_lines = "\n".join(f"- {n['title']}" for n in news)
        news_text = f"\n최근 뉴스:\n{news_lines}\n"

    signal_text = ""
    if signals:
        sig_lines = "\n".join(
            f"- {s['signal_type']}: {s['description']}" for s in signals[:3]
        )
        signal_text = f"\n최근 시그널:\n{sig_lines}\n"

    prompt = (
        "주식/코인 초보 투자자에게 아래 종목을 종합 분석해주세요.\n"
        "전문 용어 없이 쉬운 말로, 존댓말로.\n"
        "형식:\n"
        "1줄: 현재 상황 요약\n"
        "빈 줄\n"
        "2~3줄: 뉴스/시그널 기반 원인 분석 (있으면)\n"
        "빈 줄\n"
        "1~2줄: 지금 어떻게 하면 좋을지 행동 가이드\n"
        "마크다운(**, ## 등) 쓰지 마세요. 번호 매기지 마세요.\n\n"
        f"종목: {name} ({ticker})\n"
        f"현재가: {price_val:,.2f} ({sign}{change:.1f}%)\n"
        f"{news_text}{signal_text}"
    )

    result = call_llm(prompt)
    return result or ""


# ── 실투자 (로그인 사용자별) ─────────────────────────────────

def _require_user(request: Request) -> tuple[str | None, Response | None]:
    """로그인된 username 반환. 미인증이면 (None, 401 Response).
    로컬 환경에서는 외부 시세 호출 회피를 위해 전체 차단."""
    if IS_LOCAL:
        return None, JSONResponse(status_code=403, content={"error": "내 자산 기능은 서버 환경에서만 사용 가능합니다"})
    user = get_current_user(request)
    if not user or user == "unknown":
        return None, JSONResponse(status_code=401, content={"error": "login required"})
    return user, None


def _own_account(session, account_id: int, user: str) -> RealAccount | None:
    return session.query(RealAccount).filter(
        RealAccount.id == account_id, RealAccount.owner == user
    ).first()


@router.get("/my/accounts")
async def my_accounts_list(request: Request):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        rows = (
            session.query(RealAccount)
            .filter(RealAccount.owner == user)
            .order_by(RealAccount.sort_order, RealAccount.id)
            .all()
        )
        return [{
            "id": r.id,
            "broker": r.broker,
            "broker_name": BROKER_NAMES.get(r.broker, r.broker),
            "account_type": r.account_type,
            "account_type_name": ACCOUNT_TYPE_NAMES.get(r.account_type, r.account_type),
            "nickname": r.nickname,
            "currency": r.currency,
            "is_active": r.is_active,
        } for r in rows]
    finally:
        session.close()


@router.get("/my/accounts/{account_id}/holdings")
async def my_account_holdings(request: Request, account_id: int):
    """특정 계좌의 현재 보유 종목 (qty > 0). 매도 select 용."""
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        acc = session.query(RealAccount).filter(
            RealAccount.id == account_id, RealAccount.owner == user
        ).first()
        if not acc:
            return JSONResponse(status_code=404, content={"error": "account not found"})

        rows = (
            session.query(RealHolding)
            .filter(RealHolding.account_id == account_id, RealHolding.qty > 0)
            .order_by(RealHolding.ticker_name, RealHolding.ticker)
            .all()
        )
        return [{
            "ticker": h.ticker,
            "ticker_name": h.ticker_name,
            "market": h.market,
            "qty": h.qty,
            "avg_cost": h.avg_cost,
        } for h in rows]
    finally:
        session.close()


@router.post("/my/accounts")
async def my_accounts_create(request: Request):
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    broker = (body.get("broker") or "").strip()
    account_type = (body.get("account_type") or "").strip()
    nickname = (body.get("nickname") or "").strip()
    currency = (body.get("currency") or "KRW").strip()

    if not broker or not account_type:
        return JSONResponse(status_code=400, content={"error": "broker, account_type required"})
    if (broker, account_type) not in BROKER_FEES:
        return JSONResponse(status_code=400, content={"error": "지원하지 않는 (브로커, 계좌유형) 조합"})

    session = SessionLocal()
    try:
        count = session.query(RealAccount).filter(RealAccount.owner == user).count()
        acc = RealAccount(
            owner=user, broker=broker, account_type=account_type,
            nickname=nickname or f"{BROKER_NAMES.get(broker, broker)} {ACCOUNT_TYPE_NAMES.get(account_type, '')}".strip(),
            currency=currency, is_active=True, sort_order=count,
        )
        session.add(acc)
        session.commit()
        return {"id": acc.id, "status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.post("/my/accounts/reorder")
async def my_accounts_reorder(request: Request):
    """계좌 순서 일괄 갱신.
    body: { order: [account_id, ...] }
    """
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    order = body.get("order") or []
    if not isinstance(order, list):
        return JSONResponse(status_code=400, content={"error": "order must be list"})

    session = SessionLocal()
    try:
        rows = {a.id: a for a in session.query(RealAccount).filter(RealAccount.owner == user).all()}
        for idx, aid in enumerate(order):
            try:
                acc = rows.get(int(aid))
            except (TypeError, ValueError):
                continue
            if acc:
                acc.sort_order = idx
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.put("/my/accounts/{account_id}")
async def my_accounts_update(request: Request, account_id: int):
    """계좌 별명만 수정 (broker/account_type/currency는 변경 시 회계가 깨질 위험 있어 제외)."""
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    nickname = (body.get("nickname") or "").strip()
    if not nickname:
        return JSONResponse(status_code=400, content={"error": "별명 필수"})

    session = SessionLocal()
    try:
        acc = _own_account(session, account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "not found"})
        acc.nickname = nickname
        session.commit()
        return {"status": "ok", "nickname": acc.nickname}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.delete("/my/accounts/{account_id}")
async def my_accounts_delete(request: Request, account_id: int):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        acc = _own_account(session, account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "not found"})
        session.query(RealHolding).filter(RealHolding.account_id == account_id).delete()
        session.query(RealTrade).filter(RealTrade.account_id == account_id).delete()
        session.delete(acc)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.get("/my/fee-preview")
async def my_fee_preview(request: Request, account_id: int, side: str, price: float, qty: float):
    """수수료/세금 미리보기."""
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        acc = _own_account(session, account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "account not found"})
        fee, tax = real_trader.calc_fee_tax(acc.broker, acc.account_type, side, price, qty)
        return {"fee": fee, "tax": tax, "net": price * qty - fee - tax if side == "sell" else price * qty + fee}
    finally:
        session.close()


def _parse_trade_body(body: dict):
    """공통: POST/PUT body 파싱."""
    try:
        account_id = int(body.get("account_id"))
        ticker = (body.get("ticker") or "").strip()
        ticker_name = (body.get("ticker_name") or "").strip()
        side = (body.get("side") or "").strip()
        qty = float(body.get("qty") or 0)
        price = float(body.get("price") or 0)
        memo = (body.get("memo") or "").strip()
    except (TypeError, ValueError):
        raise ValueError("invalid body")

    fee = body.get("fee")
    tax = body.get("tax")
    fee = float(fee) if fee not in (None, "") else None
    tax = float(tax) if tax not in (None, "") else None

    executed_at = None
    if body.get("executed_at"):
        try:
            executed_at = datetime.fromisoformat(body["executed_at"])
        except ValueError:
            pass

    if not ticker or side not in ("buy", "sell", "dividend") or qty <= 0 or price < 0:
        raise ValueError("ticker/side/qty/price 필수")

    return {
        "account_id": account_id, "ticker": ticker, "ticker_name": ticker_name,
        "side": side, "qty": qty, "price": price, "fee": fee, "tax": tax,
        "executed_at": executed_at, "memo": memo,
    }


@router.post("/my/trades")
async def my_trades_create(request: Request):
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    try:
        p = _parse_trade_body(body)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    session = SessionLocal()
    try:
        acc = _own_account(session, p["account_id"], user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "account not found"})

        trade = real_trader.add_trade(
            session, p["account_id"], p["ticker"], p["ticker_name"],
            p["side"], p["qty"], p["price"], fee=p["fee"], tax=p["tax"],
            executed_at=p["executed_at"], memo=p["memo"],
        )
        session.commit()
        return {"id": trade.id, "fee": trade.fee, "tax": trade.tax, "realized_pnl": trade.realized_pnl}
    except ValueError as e:
        session.rollback()
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        session.rollback()
        logger.error(f"my_trades_create: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.get("/my/trades/{trade_id}")
async def my_trades_get(request: Request, trade_id: int):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        t = session.query(RealTrade).filter(RealTrade.id == trade_id).first()
        if not t:
            return JSONResponse(status_code=404, content={"error": "not found"})
        acc = _own_account(session, t.account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "not found"})
        return {
            "id": t.id, "account_id": t.account_id, "ticker": t.ticker,
            "ticker_name": t.ticker_name, "market": t.market, "side": t.side,
            "qty": t.qty, "price": t.price, "fee": t.fee, "tax": t.tax,
            "realized_pnl": t.realized_pnl,
            "executed_at": t.executed_at.strftime("%Y-%m-%dT%H:%M") if t.executed_at else None,
            "memo": t.memo,
        }
    finally:
        session.close()


@router.put("/my/trades/{trade_id}")
async def my_trades_update(request: Request, trade_id: int):
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    try:
        p = _parse_trade_body(body)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    session = SessionLocal()
    try:
        t = session.query(RealTrade).filter(RealTrade.id == trade_id).first()
        if not t:
            return JSONResponse(status_code=404, content={"error": "not found"})
        # 기존 trade 의 owner 확인
        acc = _own_account(session, t.account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "not found"})
        # 새 account_id 도 owner 확인 (계좌 변경 가능)
        if p["account_id"] != t.account_id:
            new_acc = _own_account(session, p["account_id"], user)
            if not new_acc:
                return JSONResponse(status_code=400, content={"error": "대상 계좌 권한 없음"})

        real_trader.update_trade(session, trade_id, p)
        session.commit()
        return {"status": "ok"}
    except ValueError as e:
        session.rollback()
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        session.rollback()
        logger.error(f"my_trades_update: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.post("/my/holdings/{holding_id}/toggle-hidden")
async def my_holdings_toggle_hidden(request: Request, holding_id: int):
    """보유종목 숨김 토글 (집계에서 제외)."""
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        h = session.query(RealHolding).filter(RealHolding.id == holding_id).first()
        if not h:
            return JSONResponse(status_code=404, content={"error": "not found"})
        acc = _own_account(session, h.account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "not found"})

        h.is_hidden = not h.is_hidden
        session.commit()
        return {"status": "ok", "is_hidden": h.is_hidden}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.post("/my/holdings/bulk-hidden")
async def my_holdings_bulk_hidden(request: Request):
    """일괄 숨김 — body:
       {scope: 'account'|'all', account_id?: int, hidden: bool,
        qty_filter?: 'open'|'closed'|'all'  (기본 'all')}.
    소유 계좌의 RealHolding 만 변경."""
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    scope = (body.get("scope") or "").strip()
    hidden = bool(body.get("hidden"))
    qty_filter = (body.get("qty_filter") or "all").strip()

    session = SessionLocal()
    try:
        my_accounts = [
            a.id for a in
            session.query(RealAccount).filter(RealAccount.owner == user).all()
        ]
        if not my_accounts:
            return {"status": "ok", "updated": 0}

        q = session.query(RealHolding).filter(RealHolding.account_id.in_(my_accounts))

        if scope == "account":
            try:
                acc_id = int(body.get("account_id") or 0)
            except (TypeError, ValueError):
                return JSONResponse(status_code=400, content={"error": "invalid account_id"})
            if acc_id not in my_accounts:
                return JSONResponse(status_code=404, content={"error": "account not found"})
            q = q.filter(RealHolding.account_id == acc_id)
        elif scope != "all":
            return JSONResponse(status_code=400, content={"error": "scope must be 'account' or 'all'"})

        if qty_filter == "open":
            q = q.filter(RealHolding.qty > 0)
        elif qty_filter == "closed":
            q = q.filter(RealHolding.qty == 0)
        elif qty_filter != "all":
            return JSONResponse(status_code=400, content={"error": "qty_filter must be 'open'|'closed'|'all'"})

        updated = q.update({RealHolding.is_hidden: hidden}, synchronize_session=False)
        session.commit()
        return {"status": "ok", "updated": updated, "is_hidden": hidden}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.delete("/my/trades/{trade_id}")
async def my_trades_delete(request: Request, trade_id: int):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        t = session.query(RealTrade).filter(RealTrade.id == trade_id).first()
        if not t:
            return JSONResponse(status_code=404, content={"error": "not found"})
        acc = _own_account(session, t.account_id, user)
        if not acc:
            return JSONResponse(status_code=404, content={"error": "not found"})

        real_trader.delete_trade(session, trade_id)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


# ── 관심 종목 (현재가 + 등락률만) ────────────────────────────

MAX_QUICK_WATCH = 30


@router.get("/my/watch")
async def my_watch_list(request: Request):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        rows = (
            session.query(RealQuickWatch)
            .filter(RealQuickWatch.owner == user)
            .order_by(RealQuickWatch.sort_order, RealQuickWatch.id)
            .all()
        )
        return [{
            "id": r.id, "market": r.market, "ticker": r.ticker,
            "ticker_name": r.ticker_name, "currency": r.currency or "KRW",
        } for r in rows]
    finally:
        session.close()


@router.post("/my/watch")
async def my_watch_create(request: Request):
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    market = (body.get("market") or "").strip()
    ticker = (body.get("ticker") or "").strip()
    ticker_name = (body.get("ticker_name") or "").strip()
    currency = (body.get("currency") or "KRW").strip().upper()

    if not market or not ticker:
        return JSONResponse(status_code=400, content={"error": "market, ticker required"})
    if market not in ("kr_stock", "us_stock", "crypto"):
        return JSONResponse(status_code=400, content={"error": "invalid market"})
    if currency not in ("KRW", "USD"):
        return JSONResponse(status_code=400, content={"error": "currency: KRW|USD"})
    # 코인만 currency 의미 있음. 주식은 강제로 시장 통화 사용.
    if market == "kr_stock":
        currency = "KRW"
    elif market == "us_stock":
        currency = "USD"

    session = SessionLocal()
    try:
        exists = session.query(RealQuickWatch).filter(
            RealQuickWatch.owner == user,
            RealQuickWatch.market == market,
            RealQuickWatch.ticker == ticker,
            RealQuickWatch.currency == currency,
        ).first()
        if exists:
            return JSONResponse(status_code=409, content={"error": "이미 등록됨"})

        count = session.query(RealQuickWatch).filter(RealQuickWatch.owner == user).count()
        if count >= MAX_QUICK_WATCH:
            return JSONResponse(status_code=400, content={"error": f"최대 {MAX_QUICK_WATCH}종목"})

        w = RealQuickWatch(
            owner=user, market=market, ticker=ticker,
            ticker_name=ticker_name, currency=currency, sort_order=count,
        )
        session.add(w)
        session.commit()
        return {"id": w.id, "status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.delete("/my/watch/{watch_id}")
async def my_watch_delete(request: Request, watch_id: int):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        w = session.query(RealQuickWatch).filter(
            RealQuickWatch.id == watch_id, RealQuickWatch.owner == user
        ).first()
        if not w:
            return JSONResponse(status_code=404, content={"error": "not found"})
        session.delete(w)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.post("/my/watch/reorder")
async def my_watch_reorder(request: Request):
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    order = body.get("order") or []
    if not isinstance(order, list):
        return JSONResponse(status_code=400, content={"error": "order must be list"})

    session = SessionLocal()
    try:
        rows = {
            w.id: w for w in
            session.query(RealQuickWatch).filter(RealQuickWatch.owner == user).all()
        }
        for idx, wid in enumerate(order):
            try:
                w = rows.get(int(wid))
            except (TypeError, ValueError):
                continue
            if w:
                w.sort_order = idx
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


def _fetch_upbit_prices_batch(tickers: list[str]) -> dict[str, dict]:
    """Upbit로 KRW 코인 가격 일괄 조회 — 1번 호출."""
    if not tickers:
        return {}
    markets = ",".join(f"KRW-{t}" for t in tickers)
    try:
        resp = httpx.get(
            "https://api.upbit.com/v1/ticker",
            params={"markets": markets},
            headers={"User-Agent": "Mozilla/5.0"},
            timeout=10,
        )
        resp.raise_for_status()
        result = {}
        for item in resp.json():
            market = item.get("market", "")
            tk = market.split("-", 1)[1] if "-" in market else ""
            if not tk:
                continue
            result[tk] = {
                "price": float(item.get("trade_price", 0) or 0),
                "change_pct": float(item.get("signed_change_rate", 0) or 0) * 100,
            }
        return result
    except Exception as e:
        logger.error(f"Upbit batch: {e}")
        return {}


def _fetch_binance_prices_batch(tickers: list[str]) -> dict[str, dict]:
    """Binance USDT futures 가격 일괄 조회 — 1번 호출."""
    if not tickers:
        return {}
    import json as _json
    symbols = [f"{t}USDT" for t in tickers]
    try:
        resp = httpx.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            params={"symbols": _json.dumps(symbols)},
            timeout=10,
        )
        resp.raise_for_status()
        result = {}
        for item in resp.json():
            sym = item.get("symbol", "")
            if sym.endswith("USDT"):
                tk = sym[:-4]
                result[tk] = {
                    "price": float(item.get("lastPrice", 0) or 0),
                    "change_pct": float(item.get("priceChangePercent", 0) or 0),
                }
        return result
    except Exception as e:
        logger.error(f"Binance batch: {e}")
        return {}


@router.get("/my/refresh")
async def my_refresh_prices(request: Request):
    """모든 보유종목 + 관심 종목 현재가 일괄 fetch (배치 + 병렬)."""
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        accounts = {
            a.id: a for a in
            session.query(RealAccount).filter(RealAccount.owner == user).all()
        }
        all_holdings = (
            session.query(RealHolding)
            .filter(RealHolding.account_id.in_(accounts.keys()))
            .all()
        ) if accounts else []
        holdings = [h for h in all_holdings if h.qty > 0]
        closed_holdings = [h for h in all_holdings if h.qty == 0]

        # 누적 기준 계산용 — RealTrade 전부 (보유 + 청산 포함, hidden 종목은 집계 시 제외)
        all_trades = (
            session.query(RealTrade)
            .filter(RealTrade.account_id.in_(accounts.keys()))
            .all()
        ) if accounts else []
        # (account_id, market, ticker) → is_hidden
        hidden_set = {
            (h.account_id, h.market, h.ticker) for h in all_holdings if h.is_hidden
        }

        watches = (
            session.query(RealQuickWatch)
            .filter(RealQuickWatch.owner == user)
            .order_by(RealQuickWatch.sort_order, RealQuickWatch.id)
            .all()
        )
        watch_items = [{
            "id": w.id, "market": w.market, "ticker": w.ticker,
            "ticker_name": w.ticker_name, "currency": (w.currency or "KRW"),
        } for w in watches]
    finally:
        session.close()

    # (market, currency) → tickers 그룹화 (보유 + 관심)
    # 관심: 주식은 시장통화 / 코인은 watch 항목의 currency (KRW=Upbit, USD=Binance)
    groups: dict[tuple[str, str], set] = {}
    for h in holdings:
        acc = accounts.get(h.account_id)
        ccy = acc.currency if acc else "KRW"
        groups.setdefault((h.market, ccy), set()).add(h.ticker)
    for w in watch_items:
        wccy = w["currency"]
        if w["market"] == "kr_stock":
            wccy = "KRW"
        elif w["market"] == "us_stock":
            wccy = "USD"
        groups.setdefault((w["market"], wccy), set()).add(w["ticker"])

    price_map: dict[tuple[str, str, str], dict] = {}

    async def kr_one(ticker: str):
        data = await asyncio.to_thread(_fetch_price, ticker, "kr_stock")
        price_map[(ticker, "kr_stock", "KRW")] = data

    async def us_one(ticker: str, ccy: str):
        data = await asyncio.to_thread(_fetch_price, ticker, "us_stock")
        price_map[(ticker, "us_stock", ccy)] = data

    async def crypto_krw_batch(tickers: list[str]):
        data = await asyncio.to_thread(_fetch_upbit_prices_batch, tickers)
        for tk, val in data.items():
            price_map[(tk, "crypto", "KRW")] = val

    async def crypto_usdt_batch(tickers: list[str], ccy: str):
        data = await asyncio.to_thread(_fetch_binance_prices_batch, tickers)
        for tk, val in data.items():
            price_map[(tk, "crypto", ccy)] = val

    tasks = []
    for (market, ccy), tickers in groups.items():
        tickers_list = list(tickers)
        if market == "kr_stock":
            for t in tickers_list:
                tasks.append(kr_one(t))
        elif market == "us_stock":
            for t in tickers_list:
                tasks.append(us_one(t, ccy))
        elif market == "crypto" and ccy == "KRW":
            tasks.append(crypto_krw_batch(tickers_list))
        elif market == "crypto":
            # USD/USDT 모두 Binance 배치
            tasks.append(crypto_usdt_batch(tickers_list, ccy))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # 관심 종목 결과 빌드 (currency 별 lookup)
    watch_out = []
    for w in watch_items:
        wccy = w["currency"]
        if w["market"] == "kr_stock":
            wccy = "KRW"
        elif w["market"] == "us_stock":
            wccy = "USD"
        info = price_map.get((w["ticker"], w["market"], wccy), {})
        item_out = {
            "id": w["id"], "market": w["market"], "ticker": w["ticker"],
            "ticker_name": w["ticker_name"],
            "currency": wccy,
            "price": info.get("price", 0),
            "change_pct": info.get("change_pct", 0),
        }
        # 한국주식은 세션 배지 정보 추가
        if w["market"] == "kr_stock":
            item_out["session"] = info.get("session", "")
            item_out["session_label"] = info.get("session_label", "")
        watch_out.append(item_out)

    # 집계
    out_holdings = []
    by_account: dict[int, dict] = {}

    for h in holdings:
        acc = accounts.get(h.account_id)
        ccy = acc.currency if acc else "KRW"
        info = price_map.get((h.ticker, h.market, ccy), {})
        cur = info.get("price", 0)
        change_pct = info.get("change_pct", 0)

        eval_amt = cur * h.qty
        cost_amt = h.avg_cost * h.qty
        unrealized = eval_amt - cost_amt
        unrealized_pct = (unrealized / cost_amt * 100) if cost_amt > 0 else 0

        out_holdings.append({
            "id": h.id,
            "account_id": h.account_id,
            "ticker": h.ticker,
            "ticker_name": h.ticker_name,
            "market": h.market,
            "qty": h.qty,
            "avg_cost": h.avg_cost,
            "current_price": cur,
            "change_pct": change_pct,
            "eval_amount": eval_amt,
            "cost_amount": cost_amt,
            "unrealized_pnl": unrealized,
            "unrealized_pct": unrealized_pct,
            "realized_pnl": h.realized_pnl,
            "is_hidden": h.is_hidden,
        })

        # 숨김 종목은 계좌별/통화별 집계에서 제외
        if h.is_hidden:
            continue

        agg = by_account.setdefault(h.account_id, {
            "currency": ccy, "eval": 0.0, "cost": 0.0,
            "unrealized": 0.0, "realized": 0.0,
        })
        agg["eval"] += eval_amt
        agg["cost"] += cost_amt
        agg["unrealized"] += unrealized
        agg["realized"] += h.realized_pnl

    # 닫힌 포지션 (qty=0) 출력 + 실현손익 합산 (숨김 제외)
    out_closed = []
    for h in closed_holdings:
        acc = accounts.get(h.account_id)
        ccy = acc.currency if acc else "KRW"
        out_closed.append({
            "id": h.id,
            "account_id": h.account_id,
            "ticker": h.ticker,
            "ticker_name": h.ticker_name,
            "market": h.market,
            "currency": ccy,
            "realized_pnl": h.realized_pnl,
            "is_hidden": h.is_hidden,
        })
        if h.is_hidden or not acc:
            continue
        agg = by_account.setdefault(h.account_id, {
            "currency": ccy, "eval": 0.0, "cost": 0.0,
            "unrealized": 0.0, "realized": 0.0,
            "cost_invested": 0.0, "sell_received": 0.0,
        })
        agg["realized"] += h.realized_pnl

    # 누적 기준 — RealTrade 합산 (hidden 종목 제외)
    # cost_invested = 매수 trade 누적 (참고용)
    # sell_received = 매도 trade 누적 (참고용)
    # peak_invested = 통화별 시간순 cumulative net invested (buy-sell) 의 최댓값
    #   = "내가 실제로 가장 많이 깔아둔 자본" (재투자/인출 모두 자연스럽게 처리)
    # 기존 by_account 항목들에 새 키 없을 수 있어 보장
    for agg in by_account.values():
        agg.setdefault("cost_invested", 0.0)
        agg.setdefault("sell_received", 0.0)

    # 통화별 cumulative net invested 와 peak 계산 (시간순)
    peak_per_ccy: dict[str, float] = {}
    running_per_ccy: dict[str, float] = {}
    trades_sorted = sorted(
        all_trades,
        key=lambda t: (t.executed_at or datetime.min, t.id)
    )
    for t in trades_sorted:
        if (t.account_id, t.market, t.ticker) in hidden_set:
            continue
        acc = accounts.get(t.account_id)
        if not acc:
            continue
        ccy = acc.currency

        agg = by_account.setdefault(t.account_id, {
            "currency": ccy, "eval": 0.0, "cost": 0.0,
            "unrealized": 0.0, "realized": 0.0,
            "cost_invested": 0.0, "sell_received": 0.0,
        })

        if t.side == "buy":
            amt = t.qty * t.price + (t.fee or 0.0)
            agg["cost_invested"] += amt
            running_per_ccy[ccy] = running_per_ccy.get(ccy, 0.0) + amt
        elif t.side == "sell":
            amt = t.qty * t.price - (t.fee or 0.0) - (t.tax or 0.0)
            agg["sell_received"] += amt
            running_per_ccy[ccy] = running_per_ccy.get(ccy, 0.0) - amt

        cur = running_per_ccy.get(ccy, 0.0)
        if cur > peak_per_ccy.get(ccy, 0.0):
            peak_per_ccy[ccy] = cur

    # 통화별 합산
    by_currency: dict[str, dict] = {}
    for acc_id, agg in by_account.items():
        agg.setdefault("cost_invested", 0.0)
        agg.setdefault("sell_received", 0.0)
        ccy = agg["currency"]
        c = by_currency.setdefault(ccy, {
            "eval": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0,
            "cost_invested": 0.0, "sell_received": 0.0,
            "peak_invested": 0.0,
        })
        c["eval"] += agg["eval"]
        c["cost"] += agg["cost"]
        c["unrealized"] += agg["unrealized"]
        c["realized"] += agg["realized"]
        c["cost_invested"] += agg["cost_invested"]
        c["sell_received"] += agg["sell_received"]

    # peak_invested 는 통화별 직접 set (account 합산 X)
    for ccy, peak in peak_per_ccy.items():
        c = by_currency.setdefault(ccy, {
            "eval": 0.0, "cost": 0.0, "unrealized": 0.0, "realized": 0.0,
            "cost_invested": 0.0, "sell_received": 0.0,
            "peak_invested": 0.0,
        })
        c["peak_invested"] = peak
    for c in by_currency.values():
        c.setdefault("peak_invested", 0.0)

    return {
        "holdings": out_holdings,
        "closed_holdings": out_closed,
        "by_account": by_account,
        "by_currency": by_currency,
        "watches": watch_out,
        "fetched_at": datetime.now(KST).strftime("%H:%M:%S"),
    }


# ── 가상매매 (paper trading, user별 격리) ─────────────────────

def _get_setting(session, owner: str) -> PortfolioSetting | None:
    return session.query(PortfolioSetting).filter(PortfolioSetting.owner == owner).first()


@router.get("/portfolio/state")
async def portfolio_state(request: Request):
    """초기 로드용. setting + holdings + 최근 거래내역."""
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        setting = _get_setting(session, user)
        holdings = session.query(PaperHolding).filter(PaperHolding.owner == user).all()
        trades = (
            session.query(Trade)
            .filter(Trade.owner == user)
            .order_by(Trade.executed_at.desc(), Trade.id.desc())
            .limit(50).all()
        )
        return {
            "initial_capital": setting.initial_capital if setting else 0,
            "has_setting": bool(setting and setting.initial_capital > 0),
            "holdings": [{
                "id": h.id, "ticker": h.ticker, "ticker_name": h.ticker_name,
                "market": h.market, "qty": h.qty,
                "avg_cost": h.avg_cost, "avg_cost_krw": h.avg_cost_krw,
                "realized_pnl": h.realized_pnl, "realized_pnl_krw": h.realized_pnl_krw,
            } for h in holdings],
            "trades": [{
                "id": t.id, "ticker": t.ticker, "ticker_name": t.ticker_name,
                "market": t.market, "broker": t.broker, "side": t.side,
                "qty": t.qty, "price": t.price, "fee": t.fee, "tax": t.tax,
                "fx_rate": t.fx_rate, "realized_pnl": t.realized_pnl,
                "executed_at": t.executed_at.isoformat() if t.executed_at else "",
                "memo": t.memo,
            } for t in trades],
            "brokers_by_market": paper_trader.BROKERS_BY_MARKET,
        }
    finally:
        session.close()


@router.post("/portfolio/init")
async def portfolio_init(request: Request):
    """시작자본 설정 (KRW). 처음 1회 또는 리셋 후."""
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    try:
        capital = float(body.get("initial_capital") or 0)
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "invalid capital"})
    if capital <= 0:
        return JSONResponse(status_code=400, content={"error": "0보다 커야 함"})

    session = SessionLocal()
    try:
        s = _get_setting(session, user)
        if s:
            s.initial_capital = capital
        else:
            s = PortfolioSetting(owner=user, initial_capital=capital)
            session.add(s)
        session.commit()
        return {"status": "ok", "initial_capital": capital}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.post("/portfolio/reset")
async def portfolio_reset(request: Request):
    """모든 거래/잔고 삭제. body.initial_capital 있으면 시작자본도 변경."""
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    new_cap = body.get("initial_capital")
    try:
        new_cap = float(new_cap) if new_cap not in (None, "") else None
    except (TypeError, ValueError):
        return JSONResponse(status_code=400, content={"error": "invalid capital"})

    session = SessionLocal()
    try:
        paper_trader.reset_all(session, user)
        if new_cap and new_cap > 0:
            s = _get_setting(session, user)
            if s:
                s.initial_capital = new_cap
            else:
                session.add(PortfolioSetting(owner=user, initial_capital=new_cap))
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


def _parse_paper_body(body: dict) -> dict:
    try:
        ticker = (body.get("ticker") or "").strip()
        ticker_name = (body.get("ticker_name") or "").strip()
        market = (body.get("market") or "").strip()
        broker = (body.get("broker") or "").strip()
        side = (body.get("side") or "").strip()
        qty = float(body.get("qty") or 0)
        price = float(body.get("price") or 0)
        fx_rate = float(body.get("fx_rate") or 1)
        memo = (body.get("memo") or "").strip()
    except (TypeError, ValueError):
        raise ValueError("invalid body")

    fee = body.get("fee")
    tax = body.get("tax")
    fee = float(fee) if fee not in (None, "") else None
    tax = float(tax) if tax not in (None, "") else None

    executed_at = None
    if body.get("executed_at"):
        try:
            executed_at = datetime.fromisoformat(body["executed_at"])
        except ValueError:
            pass

    if not ticker or side not in ("buy", "sell") or qty <= 0 or price <= 0:
        raise ValueError("ticker/side/qty/price 필수")
    if market not in paper_trader.MARKET_ACCOUNT_TYPE:
        raise ValueError("invalid market")

    return {
        "ticker": ticker, "ticker_name": ticker_name, "market": market,
        "broker": broker, "side": side, "qty": qty, "price": price,
        "fx_rate": fx_rate, "fee": fee, "tax": tax,
        "executed_at": executed_at, "memo": memo,
    }


@router.post("/portfolio/trade")
async def portfolio_trade_create(request: Request):
    """매수/매도 체결."""
    user, denied = _require_user(request)
    if denied: return denied

    body = await request.json()
    try:
        p = _parse_paper_body(body)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"error": str(e)})

    session = SessionLocal()
    try:
        s = _get_setting(session, user)
        if not s or s.initial_capital <= 0:
            return JSONResponse(status_code=400, content={"error": "시작자본 미설정"})

        # 매수 시 가용현금 체크
        if p["side"] == "buy":
            cash = paper_trader.cash_balance_krw(session, user, s.initial_capital)
            need_krw = (p["price"] * p["qty"] + (p["fee"] or 0)) * p["fx_rate"]
            if p["fee"] is None:
                auto_fee, _ = paper_trader.calc_fee_tax(p["broker"], p["market"], "buy", p["price"], p["qty"])
                need_krw = (p["price"] * p["qty"] + auto_fee) * p["fx_rate"]
            if need_krw > cash + 1e-6:
                return JSONResponse(status_code=400, content={
                    "error": f"가용 현금 부족: 필요 ₩{need_krw:,.0f} / 보유 ₩{cash:,.0f}"
                })

        trade = paper_trader.add_trade(
            session, owner=user,
            ticker=p["ticker"], ticker_name=p["ticker_name"],
            market=p["market"], broker=p["broker"], side=p["side"],
            qty=p["qty"], price=p["price"], fx_rate=p["fx_rate"],
            fee=p["fee"], tax=p["tax"],
            executed_at=p["executed_at"], memo=p["memo"],
        )
        session.commit()
        return {
            "id": trade.id, "fee": trade.fee, "tax": trade.tax,
            "realized_pnl": trade.realized_pnl,
        }
    except ValueError as e:
        session.rollback()
        return JSONResponse(status_code=400, content={"error": str(e)})
    except Exception as e:
        session.rollback()
        logger.error(f"portfolio_trade_create: {e}")
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.delete("/portfolio/trade/{trade_id}")
async def portfolio_trade_delete(request: Request, trade_id: int):
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        t = session.query(Trade).filter(Trade.id == trade_id, Trade.owner == user).first()
        if not t:
            return JSONResponse(status_code=404, content={"error": "not found"})
        paper_trader.delete_trade(session, user, trade_id)
        session.commit()
        return {"status": "ok"}
    except Exception as e:
        session.rollback()
        return JSONResponse(status_code=500, content={"error": str(e)})
    finally:
        session.close()


@router.get("/portfolio/fee-preview")
async def portfolio_fee_preview(request: Request, broker: str, market: str, side: str, price: float, qty: float):
    """수수료/세금 미리보기 (네이티브 통화). 인증만 체크."""
    user, denied = _require_user(request)
    if denied: return denied
    if market not in paper_trader.MARKET_ACCOUNT_TYPE:
        return JSONResponse(status_code=400, content={"error": "invalid market"})
    if side not in ("buy", "sell"):
        return JSONResponse(status_code=400, content={"error": "side: buy|sell"})
    fee, tax = paper_trader.calc_fee_tax(broker, market, side, price, qty)
    return {"fee": fee, "tax": tax}


@router.get("/portfolio/refresh")
async def portfolio_refresh(request: Request):
    """가상매매 라이브 — 보유종목 현재가 + 평가/원금/현금/PnL 집계."""
    user, denied = _require_user(request)
    if denied: return denied

    session = SessionLocal()
    try:
        setting = _get_setting(session, user)
        all_holdings = session.query(PaperHolding).filter(PaperHolding.owner == user).all()
        recent_trades = (
            session.query(Trade)
            .filter(Trade.owner == user)
            .order_by(Trade.executed_at.desc(), Trade.id.desc())
            .limit(50).all()
        )
        cash = paper_trader.cash_balance_krw(session, user, setting.initial_capital) if setting else 0
    finally:
        session.close()

    holdings_open = [h for h in all_holdings if h.qty > 0]
    holdings_closed = [h for h in all_holdings if h.qty == 0 and h.realized_pnl_krw != 0]

    # 시장별 그룹화 (us_stock 은 USD 시세 — fx 환산 필요)
    groups: dict[str, set] = {}
    for h in holdings_open:
        groups.setdefault(h.market, set()).add(h.ticker)

    price_map: dict[tuple[str, str], dict] = {}

    async def kr_one(ticker: str):
        data = await asyncio.to_thread(_fetch_price, ticker, "kr_stock")
        price_map[(ticker, "kr_stock")] = data

    async def us_one(ticker: str):
        data = await asyncio.to_thread(_fetch_price, ticker, "us_stock")
        price_map[(ticker, "us_stock")] = data

    async def crypto_batch(tickers: list[str]):
        data = await asyncio.to_thread(_fetch_upbit_prices_batch, tickers)
        for tk, val in data.items():
            price_map[(tk, "crypto")] = val

    tasks = []
    for market, tickers in groups.items():
        tickers_list = list(tickers)
        if market == "kr_stock":
            for t in tickers_list:
                tasks.append(kr_one(t))
        elif market == "us_stock":
            for t in tickers_list:
                tasks.append(us_one(t))
        elif market == "crypto":
            tasks.append(crypto_batch(tickers_list))

    if tasks:
        await asyncio.gather(*tasks, return_exceptions=True)

    # 클라이언트에서 받은 fx 사용 (보유 평가용 USD→KRW)
    try:
        fx = float(request.query_params.get("fx") or 0)
    except ValueError:
        fx = 0
    if fx <= 0:
        fx = 1400.0  # fallback

    total_eval_krw = 0.0
    total_cost_krw = 0.0
    out_holdings = []

    for h in holdings_open:
        info = price_map.get((h.ticker, h.market), {})
        cur = info.get("price", 0)
        change_pct = info.get("change_pct", 0)

        # KRW 환산 평가액
        if h.market == "us_stock":
            cur_krw = cur * fx
        else:
            cur_krw = cur

        eval_krw = cur_krw * h.qty
        cost_krw = h.avg_cost_krw * h.qty
        unrealized_krw = eval_krw - cost_krw
        unrealized_pct = (unrealized_krw / cost_krw * 100) if cost_krw > 0 else 0

        total_eval_krw += eval_krw
        total_cost_krw += cost_krw

        out_holdings.append({
            "id": h.id, "ticker": h.ticker, "ticker_name": h.ticker_name,
            "market": h.market, "qty": h.qty,
            "avg_cost": h.avg_cost, "avg_cost_krw": h.avg_cost_krw,
            "current_price": cur, "current_price_krw": cur_krw,
            "change_pct": change_pct,
            "eval_krw": eval_krw, "cost_krw": cost_krw,
            "unrealized_krw": unrealized_krw, "unrealized_pct": unrealized_pct,
            "realized_pnl_krw": h.realized_pnl_krw,
        })

    realized_total_krw = sum(h.realized_pnl_krw for h in all_holdings)
    total_asset_krw = total_eval_krw + cash
    initial = setting.initial_capital if setting else 0
    total_return_krw = total_asset_krw - initial
    total_return_pct = (total_return_krw / initial * 100) if initial > 0 else 0

    # 시장별 비중
    by_market: dict[str, float] = {}
    for h in out_holdings:
        by_market[h["market"]] = by_market.get(h["market"], 0) + h["eval_krw"]

    closed_out = [{
        "id": h.id, "ticker": h.ticker, "ticker_name": h.ticker_name,
        "market": h.market, "realized_pnl_krw": h.realized_pnl_krw,
    } for h in holdings_closed]

    trade_out = [{
        "id": t.id, "ticker": t.ticker, "ticker_name": t.ticker_name,
        "market": t.market, "broker": t.broker, "side": t.side,
        "qty": t.qty, "price": t.price, "fee": t.fee, "tax": t.tax,
        "fx_rate": t.fx_rate, "realized_pnl": t.realized_pnl,
        "executed_at": t.executed_at.strftime("%Y-%m-%d %H:%M") if t.executed_at else "",
        "memo": t.memo,
    } for t in recent_trades]

    return {
        "initial_capital": initial,
        "cash": cash,
        "total_eval_krw": total_eval_krw,
        "total_cost_krw": total_cost_krw,
        "total_asset_krw": total_asset_krw,
        "total_return_krw": total_return_krw,
        "total_return_pct": total_return_pct,
        "unrealized_krw": total_eval_krw - total_cost_krw,
        "realized_krw": realized_total_krw,
        "by_market": by_market,
        "holdings": out_holdings,
        "closed": closed_out,
        "trades": trade_out,
        "fetched_at": datetime.now(KST).strftime("%H:%M:%S"),
    }


# ── 테마 AI 브리핑 ───────────────────────────────────────

@router.get("/themes/{no}/brief")
async def themes_brief(no: str, force: int = 0):
    """업종 AI 분석. 캐시 우선 (일 단위)."""
    from services.themes import fetch_sector_stocks, get_sector_by_no
    from services.theme_brief import generate_brief, get_cached

    sector = get_sector_by_no(no)
    if not sector:
        return JSONResponse(status_code=404, content={"error": "sector not found"})

    if not force:
        cached = get_cached(no)
        if cached:
            return {
                "cached": True,
                "headline": cached.headline,
                "risks": cached.risks,
                "top_stocks": cached.top_stocks,
                "based_on_news": cached.based_on_news,
                "date_kst": cached.date_kst,
            }

    stocks = fetch_sector_stocks(no)
    # 상승 순으로 정렬 후 상위 8
    stocks.sort(key=lambda s: s.change_pct, reverse=True)
    top_for_llm = [
        {"code": s.code, "name": s.name, "change_pct": s.change_pct}
        for s in stocks[:8]
    ]
    brief = generate_brief(no, sector.name, top_for_llm, force=bool(force))
    if not brief:
        return JSONResponse(status_code=503, content={"error": "LLM unavailable"})

    return {
        "cached": False,
        "headline": brief.headline,
        "risks": brief.risks,
        "top_stocks": brief.top_stocks,
        "based_on_news": brief.based_on_news,
        "date_kst": brief.date_kst,
    }
