import re
from datetime import datetime, timedelta

import httpx
from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from config import (
    KST, FINNHUB_API_KEY, NAVER_CLIENT_ID, NAVER_CLIENT_SECRET,
    SIGNAL_TYPE_NAMES, get_logger,
)
from db.database import SessionLocal
from db.models import Signal, ResearchTicker, ResearchHistory
from analysis.llm import call_llm
from routes.auth import reset_session, require_auth, logout, COOKIE_NAME

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


@router.post("/trade")
async def create_trade():
    """가상매매 주문 처리 (Phase 4에서 구현)"""
    return {"status": "ok"}


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
    """마켓별 실시간 가격 fetch."""
    try:
        if market == "kr_stock":
            from data.signal_collectors import _parse_naver_number
            resp = httpx.get(
                f"https://m.stock.naver.com/api/stock/{ticker}/basic",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()
            price = _parse_naver_number(data.get("closePrice", "0"))
            diff = _parse_naver_number(data.get("compareToPreviousClosePrice", "0"))
            return {
                "price": price,
                "prev_close": price - diff,
                "change_pct": _parse_naver_number(data.get("fluctuationsRatio", "0")),
                "name": data.get("stockName", ""),
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
