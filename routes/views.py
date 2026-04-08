import math

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_

from config import AUTH_ENABLED, get_logger
from db.database import SessionLocal
from db.models import News, Signal
from routes.auth import require_auth, create_session, COOKIE_NAME, _get_client_ip

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = get_logger("auth")


# ── Mock 데이터 ──────────────────────────────────────────────

MOCK_PORTFOLIO = {
    "total_asset": 112_340_000,
    "cash": 22_340_000,
    "invested": 90_000_000,
    "daily_pnl": 1_850_000,
    "daily_pnl_pct": 1.67,
    "total_pnl_pct": 12.34,
    "positions": [
        {"ticker": "NVDA", "market": "stock", "qty": 15, "avg_price": 850.00, "current_price": 920.50, "pnl_pct": 8.29, "pnl_amount": 1_057_500, "weight": 15.5},
        {"ticker": "BTC/USDT", "market": "crypto", "qty": 0.5, "avg_price": 92_000, "current_price": 97_500, "pnl_pct": 5.98, "pnl_amount": 2_750, "weight": 43.4},
        {"ticker": "ETH/USDT", "market": "crypto", "qty": 8.0, "avg_price": 3_200, "current_price": 3_450, "pnl_pct": 7.81, "pnl_amount": 2_000, "weight": 24.5},
        {"ticker": "AAPL", "market": "stock", "qty": 20, "avg_price": 195.00, "current_price": 188.30, "pnl_pct": -3.44, "pnl_amount": -134_000, "weight": 3.4},
        {"ticker": "005930.KS", "market": "stock", "qty": 50, "avg_price": 72_000, "current_price": 74_500, "pnl_pct": 3.47, "pnl_amount": 125_000, "weight": 3.3},
        {"ticker": "SOL/USDT", "market": "crypto", "qty": 100, "avg_price": 95.00, "current_price": 112.80, "pnl_pct": 18.74, "pnl_amount": 1_780, "weight": 10.0},
    ],
    "trades": [
        {"time": "02/25 13:20", "ticker": "NVDA", "side": "buy", "qty": 5, "price": 918.00, "fee": 689},
        {"time": "02/25 11:05", "ticker": "BTC/USDT", "side": "buy", "qty": 0.1, "price": 97_200, "fee": 9_720},
        {"time": "02/24 16:30", "ticker": "AAPL", "side": "sell", "qty": 10, "price": 189.50, "fee": 284},
        {"time": "02/24 09:15", "ticker": "SOL/USDT", "side": "buy", "qty": 50, "price": 108.50, "fee": 5_425},
        {"time": "02/23 14:00", "ticker": "005930.KS", "side": "buy", "qty": 50, "price": 72_000, "fee": 540},
        {"time": "02/23 10:30", "ticker": "ETH/USDT", "side": "buy", "qty": 3.0, "price": 3_380, "fee": 1_014},
        {"time": "02/22 15:45", "ticker": "NVDA", "side": "buy", "qty": 10, "price": 842.00, "fee": 1_263},
    ],
}


MOCK_CHART_DATA = {
    "dates": ["02/11","02/12","02/13","02/14","02/15","02/16","02/17","02/18","02/19","02/20","02/21","02/22","02/23","02/24","02/25"],
    "portfolio": [100,100.8,101.2,100.5,101.8,103.2,103.0,104.5,105.1,106.8,108.2,109.5,110.1,111.2,112.3],
    "benchmark": [100,100.5,100.8,100.2,100.9,101.5,101.3,102.0,102.4,103.1,103.8,104.2,104.5,104.9,105.3],
}


# ── 인증 체크 공통 ────────────────────────────────────────────

def _auth_or_401(request: Request) -> Response | None:
    """인증 실패 시 401 Response 반환, 성공 시 None. 로컬에서는 항상 통과."""
    if not AUTH_ENABLED:
        return None
    if require_auth(request):
        return None

    auth_header = request.headers.get("authorization")
    if auth_header:
        ip = _get_client_ip(request)
        import base64
        try:
            decoded = base64.b64decode(auth_header[6:]).decode("utf-8")
            username = decoded.split(":", 1)[0]
        except Exception:
            username = "unknown"
        logger.warning(f"Login FAILED / user: {username} / IP: {ip}")

    return Response(
        status_code=401,
        headers={"WWW-Authenticate": "Basic realm='TradeLab'"},
    )


def _page_response(request: Request, template: str, context: dict) -> Response:
    """인증 확인 후 페이지 렌더링. 최초 로그인 시 세션 쿠키 발급."""
    denied = _auth_or_401(request)
    if denied:
        return denied

    context["auth_enabled"] = AUTH_ENABLED
    response = templates.TemplateResponse(request, template, context)

    # 쿠키 없으면 새 세션 발급 (Basic Auth로 최초 통과한 경우)
    if not request.cookies.get(COOKIE_NAME):
        create_session(request, response)

    return response


# ── 페이지 라우트 ────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    session = SessionLocal()
    try:
        recent_news = (
            session.query(News)
            .order_by(News.published_at.desc())
            .limit(5)
            .all()
        )
        news_total = session.query(News).count()
        news_positive = session.query(News).filter(News.sentiment_label == "positive").count()
        news_negative = session.query(News).filter(News.sentiment_label == "negative").count()
        news_neutral = news_total - news_positive - news_negative

        recent_signals = (
            session.query(Signal)
            .order_by(Signal.created_at.desc())
            .limit(5)
            .all()
        )

        return _page_response(request, "pages/dashboard.html", {
            "request": request,
            "page": "dashboard",
            "portfolio": MOCK_PORTFOLIO,
            "signals": recent_signals,
            "news": recent_news,
            "news_stats": {
                "total": news_total,
                "positive": news_positive,
                "negative": news_negative,
                "neutral": news_neutral,
            },
            "chart": MOCK_CHART_DATA,
        })
    finally:
        session.close()


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio(request: Request):
    return _page_response(request, "pages/portfolio.html", {
        "request": request,
        "page": "portfolio",
        "portfolio": MOCK_PORTFOLIO,
    })


@router.get("/research", response_class=HTMLResponse)
async def research(request: Request):
    # 리서치한 종목 리스트
    from db.models import ResearchTicker, ResearchHistory
    session = SessionLocal()
    try:
        tickers = session.query(ResearchTicker).order_by(
            ResearchTicker.last_researched_at.desc()
        ).limit(30).all()
    finally:
        session.close()

    return _page_response(request, "pages/research.html", {
        "request": request,
        "page": "research",
        "research_tickers": tickers,
    })


SIGNALS_PER_PAGE = 20

SIGNAL_MARKET_MAP = {
    "kr_stock": "한국주식",
    "us_stock": "미국주식",
    "crypto": "코인",
    "macro": "매크로",
}


@router.get("/signals", response_class=HTMLResponse)
async def signals(request: Request):
    page = int(request.query_params.get("page", 1))
    direction = request.query_params.get("direction", "")
    market = request.query_params.get("market", "")
    signal_type = request.query_params.get("type", "")
    search = request.query_params.get("q", "").strip()

    session = SessionLocal()
    try:
        query = session.query(Signal)

        if direction in ("bullish", "bearish"):
            query = query.filter(Signal.direction == direction)
        if market in SIGNAL_MARKET_MAP:
            query = query.filter(Signal.market == market)
        if signal_type:
            query = query.filter(Signal.signal_type == signal_type)
        if search:
            query = query.filter(Signal.ticker.ilike(f"%{search}%"))

        total = query.count()
        total_pages = max(1, math.ceil(total / SIGNALS_PER_PAGE))
        page = max(1, min(page, total_pages))

        signals_list = (
            query.order_by(Signal.created_at.desc())
            .offset((page - 1) * SIGNALS_PER_PAGE)
            .limit(SIGNALS_PER_PAGE)
            .all()
        )

        return _page_response(request, "pages/signals.html", {
            "request": request,
            "page": "signals",
            "signals": signals_list,
            "current_page": page,
            "total_pages": total_pages,
            "total_count": total,
            "direction_filter": direction,
            "market_filter": market,
            "type_filter": signal_type,
            "search_query": search,
        })
    finally:
        session.close()


NEWS_PER_PAGE = 20

# 소스 → 카테고리 매핑
SOURCE_CATEGORIES = {
    "stock": ["한국경제 증권", "파이낸셜 증권", "finnhub_general", "finnhub_company"],
    "crypto": ["CoinDesk", "CoinTelegraph", "파이낸셜 블록체인", "finnhub_crypto"],
    "economy": ["한국경제 경제", "파이낸셜 경제"],
}


@router.get("/news", response_class=HTMLResponse)
async def news(request: Request):
    page = int(request.query_params.get("page", 1))
    sentiment = request.query_params.get("sentiment", "")
    search = request.query_params.get("q", "").strip()
    category = request.query_params.get("category", "")  # stock/crypto/economy
    impact_min = request.query_params.get("impact", "")   # 1~10
    date_from = request.query_params.get("from", "")       # YYYY-MM-DD
    date_to = request.query_params.get("to", "")           # YYYY-MM-DD

    session = SessionLocal()
    try:
        query = session.query(News)

        if sentiment in ("positive", "negative", "neutral"):
            query = query.filter(News.sentiment_label == sentiment)

        if category in SOURCE_CATEGORIES:
            query = query.filter(News.source.in_(SOURCE_CATEGORIES[category]))

        if impact_min.isdigit():
            query = query.filter(News.impact >= int(impact_min))

        if date_from:
            try:
                from datetime import datetime
                dt_from = datetime.strptime(date_from, "%Y-%m-%d")
                query = query.filter(News.published_at >= dt_from)
            except ValueError:
                pass

        if date_to:
            try:
                from datetime import datetime, timedelta
                dt_to = datetime.strptime(date_to, "%Y-%m-%d") + timedelta(days=1)
                query = query.filter(News.published_at < dt_to)
            except ValueError:
                pass

        if search:
            query = query.filter(
                or_(
                    News.title.ilike(f"%{search}%"),
                    News.summary.ilike(f"%{search}%"),
                    News.related_tickers.ilike(f"%{search}%"),
                )
            )

        total = query.count()
        total_pages = max(1, math.ceil(total / NEWS_PER_PAGE))
        page = max(1, min(page, total_pages))

        news_list = (
            query.order_by(News.published_at.desc())
            .offset((page - 1) * NEWS_PER_PAGE)
            .limit(NEWS_PER_PAGE)
            .all()
        )

        return _page_response(request, "pages/news.html", {
            "request": request,
            "page": "news",
            "news": news_list,
            "current_page": page,
            "total_pages": total_pages,
            "total_count": total,
            "sentiment_filter": sentiment,
            "search_query": search,
            "category_filter": category,
            "impact_filter": impact_min,
            "date_from": date_from,
            "date_to": date_to,
        })
    finally:
        session.close()
