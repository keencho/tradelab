import math

from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_

from config import (
    AUTH_ENABLED, MARKET_NAMES, SIGNAL_TYPE_NAMES, IS_LOCAL,
    BROKER_NAMES, ACCOUNT_TYPE_NAMES, get_logger,
)
from db.database import SessionLocal
from db.models import News, Signal, RealAccount, RealHolding, RealTrade
from routes.auth import require_auth, create_session, get_current_user, COOKIE_NAME, _get_client_ip

router = APIRouter()
templates = Jinja2Templates(directory="templates")
logger = get_logger("auth")

# Jinja2 커스텀 필터
templates.env.filters["market_kr"] = lambda v: MARKET_NAMES.get(v, v)
templates.env.filters["signal_type_kr"] = lambda v: SIGNAL_TYPE_NAMES.get(v, v)
templates.env.filters["z_label"] = lambda z: (
    f"평소 대비 {abs(z):.1f}배 ({'매우 이례적' if abs(z) >= 3 else '이례적' if abs(z) >= 2.5 else '주의'})"
    if z else ""
)


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
    context["user"] = get_current_user(request)
    context["is_local"] = IS_LOCAL
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


@router.get("/settings", response_class=HTMLResponse)
async def settings(request: Request):
    return _page_response(request, "pages/settings.html", {
        "request": request,
        "page": "settings",
    })


@router.get("/research", response_class=HTMLResponse)
async def research(request: Request):
    from db.models import ResearchTicker, ResearchHistory
    session = SessionLocal()
    try:
        tickers = session.query(ResearchTicker).order_by(
            ResearchTicker.last_researched_at.desc()
        ).limit(30).all()

        # 각 종목의 분석 횟수
        counts = {}
        ticker_ids = [t.id for t in tickers]
        if ticker_ids:
            from sqlalchemy import func as sqlfunc
            count_rows = (
                session.query(ResearchHistory.research_ticker_id, sqlfunc.count(ResearchHistory.id))
                .filter(ResearchHistory.research_ticker_id.in_(ticker_ids))
                .group_by(ResearchHistory.research_ticker_id)
                .all()
            )
            counts = {row[0]: row[1] for row in count_rows}

        return _page_response(request, "pages/research.html", {
            "request": request,
            "page": "research",
            "research_tickers": tickers,
            "research_counts": counts,
        })
    finally:
        session.close()


@router.get("/research/ticker/{ticker}", response_class=HTMLResponse)
async def research_ticker_detail(request: Request, ticker: str):
    """종목별 리서치 이력 페이지."""
    from db.models import ResearchTicker, ResearchHistory
    session = SessionLocal()
    try:
        rt = session.query(ResearchTicker).filter(ResearchTicker.ticker == ticker).first()
        if not rt:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/research")

        histories = (
            session.query(ResearchHistory)
            .filter(ResearchHistory.research_ticker_id == rt.id)
            .order_by(ResearchHistory.created_at.desc())
            .limit(50)
            .all()
        )

        return _page_response(request, "pages/research_detail.html", {
            "request": request,
            "page": "research",
            "rt": rt,
            "histories": histories,
        })
    finally:
        session.close()


@router.get("/research/history/{history_id}", response_class=HTMLResponse)
async def research_history_view(request: Request, history_id: int):
    """리서치 스냅샷 보기."""
    from db.models import ResearchTicker, ResearchHistory
    session = SessionLocal()
    try:
        h = session.query(ResearchHistory).filter(ResearchHistory.id == history_id).first()
        if not h:
            from fastapi.responses import RedirectResponse
            return RedirectResponse(url="/research")

        rt = session.query(ResearchTicker).filter(ResearchTicker.id == h.research_ticker_id).first()

        return _page_response(request, "pages/research_snapshot.html", {
            "request": request,
            "page": "research",
            "rt": rt,
            "h": h,
        })
    finally:
        session.close()


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


# ── 실투자 (로그인 사용자별) ─────────────────────────────────

TRADES_PER_PAGE = 30


def _block_local() -> Response | None:
    """로컬 환경에서는 /my/* 페이지 접근 차단 (외부 시세 호출 회피)."""
    if IS_LOCAL:
        return HTMLResponse(
            """<!DOCTYPE html><html lang="ko"><head><meta charset="utf-8"><title>접근 불가</title>
            <style>body{margin:0;background:#17171B;color:#F2F4F6;font-family:-apple-system,sans-serif;
            display:flex;align-items:center;justify-content:center;height:100vh;text-align:center;padding:20px;}
            h1{font-size:18px;margin:0 0 8px;}p{color:#8B95A1;font-size:14px;margin:4px 0;}
            a{color:#3182F6;text-decoration:none;font-size:13px;display:inline-block;margin-top:16px;}</style>
            </head><body><div><h1>로컬 환경에서는 사용할 수 없습니다</h1>
            <p>'내 자산' 기능은 서버 환경에서만 동작합니다.</p>
            <a href="/">← 대시보드로</a></div></body></html>""",
            status_code=403,
        )
    return None


@router.get("/my/report", response_class=HTMLResponse)
async def my_report(request: Request):
    denied = _auth_or_401(request)
    if denied:
        return denied
    blocked = _block_local()
    if blocked:
        return blocked

    user = get_current_user(request)
    session = SessionLocal()
    try:
        accounts = (
            session.query(RealAccount)
            .filter(RealAccount.owner == user)
            .order_by(RealAccount.sort_order, RealAccount.id)
            .all()
        )
        account_ids = [a.id for a in accounts]

        if not account_ids:
            return _page_response(request, "pages/my_report.html", {
                "request": request, "page": "my", "empty": True,
                "accounts": [], "kpi": {}, "monthly": [], "top_tickers": [],
                "by_account": [], "by_market": [],
            })

        trades = (
            session.query(RealTrade)
            .filter(RealTrade.account_id.in_(account_ids))
            .order_by(RealTrade.executed_at.asc())
            .all()
        )
        holdings = (
            session.query(RealHolding)
            .filter(RealHolding.account_id.in_(account_ids), RealHolding.qty > 0)
            .all()
        )

        # KPI
        total_realized = sum(t.realized_pnl for t in trades)
        total_fee = sum(t.fee for t in trades)
        total_tax = sum(t.tax for t in trades)
        buy_count = sum(1 for t in trades if t.side == "buy")
        sell_count = sum(1 for t in trades if t.side == "sell")
        div_count = sum(1 for t in trades if t.side == "dividend")
        total_cost_open = sum(h.avg_cost * h.qty for h in holdings)

        # 월별 실현손익 (매도 + 배당 기준)
        monthly_map: dict[str, float] = {}
        for t in trades:
            if t.side not in ("sell", "dividend"):
                continue
            key = t.executed_at.strftime("%Y-%m")
            monthly_map[key] = monthly_map.get(key, 0) + t.realized_pnl
        monthly = [{"month": k, "pnl": v} for k, v in sorted(monthly_map.items())]

        # 종목별 누적 (실현 + 미실현 평가는 못 — current 가격 없으니 실현만)
        ticker_map: dict[tuple[str, str], dict] = {}
        for t in trades:
            key = (t.ticker, t.ticker_name or t.ticker)
            row = ticker_map.setdefault(key, {"ticker": t.ticker, "name": t.ticker_name or t.ticker, "realized": 0.0, "fee": 0.0, "tax": 0.0, "trades": 0})
            row["realized"] += t.realized_pnl
            row["fee"] += t.fee
            row["tax"] += t.tax
            row["trades"] += 1
        top_tickers = sorted(ticker_map.values(), key=lambda r: r["realized"], reverse=True)
        # 절대값 기준 TOP 10 (이익+손실 양쪽)
        top_abs = sorted(ticker_map.values(), key=lambda r: abs(r["realized"]), reverse=True)[:10]

        # 계좌별 취득금액 비중 (현재 보유 기준)
        acc_meta = {a.id: {"nickname": a.nickname, "broker": a.broker, "currency": a.currency} for a in accounts}
        by_account: dict[int, float] = {}
        for h in holdings:
            by_account[h.account_id] = by_account.get(h.account_id, 0) + h.avg_cost * h.qty
        by_account_list = [
            {"name": acc_meta[aid]["nickname"], "value": v, "currency": acc_meta[aid]["currency"]}
            for aid, v in by_account.items()
        ]

        # 시장별
        by_market: dict[str, float] = {}
        for h in holdings:
            by_market[h.market] = by_market.get(h.market, 0) + h.avg_cost * h.qty
        market_label = {"kr_stock": "한국주식", "us_stock": "미국주식", "crypto": "코인"}
        by_market_list = [
            {"name": market_label.get(m, m), "value": v}
            for m, v in by_market.items()
        ]

        return _page_response(request, "pages/my_report.html", {
            "request": request,
            "page": "my",
            "empty": False,
            "accounts": accounts,
            "kpi": {
                "total_realized": total_realized,
                "total_fee": total_fee,
                "total_tax": total_tax,
                "trade_count": len(trades),
                "buy_count": buy_count,
                "sell_count": sell_count,
                "div_count": div_count,
                "total_cost_open": total_cost_open,
                "holding_count": len(holdings),
            },
            "monthly": monthly,
            "top_tickers": top_abs,
            "by_account": by_account_list,
            "by_market": by_market_list,
        })
    finally:
        session.close()


@router.get("/my", response_class=HTMLResponse)
async def my_assets(request: Request):
    denied = _auth_or_401(request)
    if denied:
        return denied
    blocked = _block_local()
    if blocked:
        return blocked

    user = get_current_user(request)

    # 거래내역 필터
    q = request.query_params.get("q", "").strip()
    side_filter = request.query_params.get("side", "")
    try:
        acc_filter = int(request.query_params.get("acc", "") or 0)
    except ValueError:
        acc_filter = 0
    try:
        page = max(1, int(request.query_params.get("page", "1")))
    except ValueError:
        page = 1

    session = SessionLocal()
    try:
        accounts = (
            session.query(RealAccount)
            .filter(RealAccount.owner == user)
            .order_by(RealAccount.sort_order, RealAccount.id)
            .all()
        )
        account_ids = [a.id for a in accounts]

        holdings = []
        closed_holdings = []
        trades = []
        total_trades = 0
        total_pages = 1
        if account_ids:
            all_h = (
                session.query(RealHolding)
                .filter(RealHolding.account_id.in_(account_ids))
                .all()
            )
            holdings = [h for h in all_h if h.qty > 0]
            closed_holdings = [h for h in all_h if h.qty == 0 and h.realized_pnl != 0]

            tq = session.query(RealTrade).filter(RealTrade.account_id.in_(account_ids))
            if q:
                tq = tq.filter(or_(
                    RealTrade.ticker.ilike(f"%{q}%"),
                    RealTrade.ticker_name.ilike(f"%{q}%"),
                ))
            if side_filter in ("buy", "sell", "dividend"):
                tq = tq.filter(RealTrade.side == side_filter)
            if acc_filter and acc_filter in account_ids:
                tq = tq.filter(RealTrade.account_id == acc_filter)

            total_trades = tq.count()
            total_pages = max(1, math.ceil(total_trades / TRADES_PER_PAGE))
            page = min(page, total_pages)

            trades = (
                tq.order_by(RealTrade.executed_at.desc(), RealTrade.id.desc())
                .offset((page - 1) * TRADES_PER_PAGE)
                .limit(TRADES_PER_PAGE)
                .all()
            )

        account_map = {
            a.id: {
                "id": a.id,
                "broker": a.broker,
                "broker_name": BROKER_NAMES.get(a.broker, a.broker),
                "account_type": a.account_type,
                "account_type_name": ACCOUNT_TYPE_NAMES.get(a.account_type, a.account_type),
                "nickname": a.nickname,
                "currency": a.currency,
            } for a in accounts
        }

        holdings_by_account = {a.id: [] for a in accounts}
        for h in holdings:
            holdings_by_account.setdefault(h.account_id, []).append(h)

        closed_by_account = {a.id: [] for a in accounts}
        for h in closed_holdings:
            closed_by_account.setdefault(h.account_id, []).append(h)

        return _page_response(request, "pages/my.html", {
            "request": request,
            "page": "my",
            "accounts": accounts,
            "account_map": account_map,
            "holdings_by_account": holdings_by_account,
            "closed_by_account": closed_by_account,
            "trades": trades,
            "broker_options": BROKER_NAMES,
            "account_type_options": ACCOUNT_TYPE_NAMES,
            "trade_q": q,
            "trade_side": side_filter,
            "trade_acc": acc_filter,
            "trade_page": page,
            "trade_total_pages": total_pages,
            "trade_total": total_trades,
        })
    finally:
        session.close()
