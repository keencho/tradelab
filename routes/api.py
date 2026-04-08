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

@router.get("/ticker/search")
async def ticker_search(q: str = "", market: str = ""):
    """워치리스트 + 외부 API에서 종목 검색."""
    if not q or len(q) < 1:
        return []

    results = []

    # 1) DB 워치리스트에서 검색
    session = SessionLocal()
    try:
        from sqlalchemy import text
        query = text(
            "SELECT ticker, name, market FROM watchlist "
            "WHERE is_active = true AND (ticker ILIKE :q OR name ILIKE :q) "
            + ("AND market = :market " if market else "")
            + "ORDER BY sort_order LIMIT 10"
        )
        params = {"q": f"%{q}%"}
        if market:
            params["market"] = market
        rows = session.execute(query, params).fetchall()
        for row in rows:
            results.append({"ticker": row[0], "name": row[1], "market": row[2]})
    finally:
        session.close()

    # 2) 결과가 부족하면 외부 API 검색
    if len(results) < 3 and not market:
        results.extend(_search_external(q, existing=[r["ticker"] for r in results]))

    return results[:10]


def _search_external(q: str, existing: list[str]) -> list[dict]:
    """Finnhub symbol lookup으로 외부 종목 검색."""
    results = []
    if not FINNHUB_API_KEY:
        return results

    try:
        resp = httpx.get(
            "https://finnhub.io/api/v1/search",
            params={"q": q, "token": FINNHUB_API_KEY},
            timeout=5,
        )
        resp.raise_for_status()
        for item in resp.json().get("result", [])[:5]:
            ticker = item.get("symbol", "")
            if ticker and ticker not in existing:
                results.append({
                    "ticker": ticker,
                    "name": item.get("description", ""),
                    "market": "us_stock",
                })
    except Exception:
        pass

    return results


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


@router.get("/research/history")
async def research_history(ticker: str = "", limit: int = 20):
    """리서치 이력 조회."""
    session = SessionLocal()
    try:
        query = session.query(ResearchHistory).order_by(ResearchHistory.created_at.desc())
        if ticker:
            query = query.filter(ResearchHistory.ticker == ticker)
        rows = query.limit(limit).all()

        return [{
            "id": r.id,
            "ticker": r.ticker,
            "market": r.market,
            "price": r.price,
            "change_pct": r.change_pct,
            "ai_report": r.ai_report[:200] if r.ai_report else "",
            "created_at": r.created_at.isoformat(),
        } for r in rows]
    finally:
        session.close()


@router.get("/research/{history_id}")
async def research_detail(history_id: int):
    """리서치 이력 상세 조회."""
    session = SessionLocal()
    try:
        r = session.query(ResearchHistory).filter(ResearchHistory.id == history_id).first()
        if not r:
            return JSONResponse(status_code=404, content={"error": "not found"})

        rt = session.query(ResearchTicker).filter(ResearchTicker.id == r.research_ticker_id).first()

        return {
            "id": r.id,
            "ticker": r.ticker,
            "ticker_name": rt.ticker_name if rt else "",
            "market": r.market,
            "price": {"price": r.price, "prev_close": r.prev_close, "change_pct": r.change_pct},
            "news": r.news_data,
            "signals": r.signals_data,
            "ai_report": r.ai_report,
            "created_at": r.created_at.isoformat(),
        }
    finally:
        session.close()


@router.get("/research/tickers")
async def research_tickers():
    """리서치한 종목 리스트."""
    session = SessionLocal()
    try:
        rows = session.query(ResearchTicker).order_by(
            ResearchTicker.last_researched_at.desc()
        ).limit(30).all()

        return [{
            "id": r.id,
            "ticker": r.ticker,
            "ticker_name": r.ticker_name,
            "market": r.market,
            "last_researched_at": r.last_researched_at.isoformat(),
        } for r in rows]
    finally:
        session.close()


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
            exchange = ccxt.binance({"options": {"defaultType": "future"}})
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
    """리서치용 뉴스 fetch (제목 + 요약)."""
    query = ticker_name or ticker

    try:
        if market == "kr_stock" and NAVER_CLIENT_ID:
            resp = httpx.get(
                "https://openapi.naver.com/v1/search/news.json",
                params={"query": query, "display": 5, "sort": "date"},
                headers={
                    "X-Naver-Client-Id": NAVER_CLIENT_ID,
                    "X-Naver-Client-Secret": NAVER_CLIENT_SECRET,
                },
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json().get("items", [])
            return [{
                "title": re.sub(r"<.*?>|&[a-z]+;", "", item["title"]),
                "description": re.sub(r"<.*?>|&[a-z]+;", "", item.get("description", "")),
                "url": item.get("originallink", ""),
                "published": item.get("pubDate", ""),
            } for item in items]

        elif market == "us_stock" and FINNHUB_API_KEY:
            today = datetime.now(KST).strftime("%Y-%m-%d")
            week_ago = (datetime.now(KST) - timedelta(days=3)).strftime("%Y-%m-%d")
            resp = httpx.get(
                "https://finnhub.io/api/v1/company-news",
                params={"symbol": ticker, "from": week_ago, "to": today, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            items = resp.json()[:5]
            return [{
                "title": item.get("headline", ""),
                "description": item.get("summary", "")[:200],
                "url": item.get("url", ""),
                "published": item.get("datetime", ""),
            } for item in items if item.get("headline")]

    except Exception as e:
        logger.error(f"리서치 뉴스 [{ticker}]: {e}")

    return []


def _fetch_signals(ticker: str) -> list[dict]:
    """DB에서 최근 시그널 조회."""
    session = SessionLocal()
    try:
        rows = (
            session.query(Signal)
            .filter(Signal.ticker == ticker)
            .order_by(Signal.created_at.desc())
            .limit(5)
            .all()
        )
        return [{
            "signal_type": SIGNAL_TYPE_NAMES.get(s.signal_type, s.signal_type),
            "direction": s.direction,
            "description": s.description,
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
        news_lines = "\n".join(f"- {n['title']}" for n in news[:5])
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
