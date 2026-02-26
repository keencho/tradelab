from fastapi import APIRouter, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from config import AUTH_ENABLED, get_logger
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

MOCK_SIGNALS = [
    {"ticker": "BTC", "type": "whale_alert", "direction": "bullish", "confidence": 0.85, "time": "02/25 12:30", "desc": "바이낸스에서 2,400 BTC ($234M) 출금 감지. 콜드월렛 이동 추정.", "ai": "최근 30일 내 유사 패턴 7회 발생, 이 중 5회(71%)에서 48시간 내 3~8% 상승. 기관의 장기 보유 전환 가능성."},
    {"ticker": "NVDA", "type": "insider_trade", "direction": "bearish", "confidence": 0.72, "time": "02/25 09:15", "desc": "CFO가 $5.2M 규모 주식 매도 (Form 4 제출)", "ai": "경영진 매도 자체는 흔하나, 실적 발표 2주 전 대량 매도는 주의 필요. 최근 6개월 내부자 순매도 비율 증가 추세."},
    {"ticker": "ETH", "type": "social_buzz", "direction": "bullish", "confidence": 0.68, "time": "02/25 08:00", "desc": "Reddit r/ethereum 언급량 평소 대비 4.2배 급증. 긍정 센티멘트 78%.", "ai": "Pectra 업그레이드 기대감 반영. 과거 언급량 급증 후 평균 5.2% 상승 (3일 내)."},
    {"ticker": "AAPL", "type": "option_flow", "direction": "bearish", "confidence": 0.65, "time": "02/24 16:00", "desc": "풋옵션 거래량 평소 3.8배. 행사가 $180 (3월 만기) 집중.", "ai": "기관 헤지 가능성 높음. 실적 시즌 앞두고 방어적 포지셔닝으로 해석."},
    {"ticker": "SOL", "type": "onchain", "direction": "bullish", "confidence": 0.78, "time": "02/24 14:30", "desc": "DEX 거래량 7일 연속 증가. TVL $12.8B 역대 최고 경신.", "ai": "네트워크 활성도와 가격은 강한 상관관계(0.82). TVL 신고 후 평균 2주 내 15~25% 상승 이력."},
    {"ticker": "005930.KS", "type": "dart_filing", "direction": "bullish", "confidence": 0.60, "time": "02/24 10:00", "desc": "삼성전자, HBM3E 양산 승인 관련 공시.", "ai": "HBM 시장 점유율 확보 시 반도체 사이클 수혜 예상. 다만 SK하이닉스 대비 후발주자 리스크 존재."},
]

MOCK_NEWS = [
    {"title": "Fed 파월 의장, 금리 인하 가능성 시사", "sentiment": "positive", "score": 0.82, "impact": 9, "tickers": "SPY, QQQ, BTC", "time": "02/25 13:00", "summary": "파월 의장이 인플레이션 둔화 추세를 확인하며 올해 내 금리 인하 가능성을 시사. 시장 즉각 반응."},
    {"title": "NVIDIA, 데이터센터 매출 전년 대비 120% 증가", "sentiment": "positive", "score": 0.91, "impact": 8, "tickers": "NVDA, AMD, SMCI", "time": "02/25 11:30", "summary": "4분기 실적 발표. AI 인프라 수요 폭증으로 데이터센터 부문 사상 최대 매출 기록."},
    {"title": "중국, 크립토 거래소 규제 강화 방침 발표", "sentiment": "negative", "score": -0.75, "impact": 7, "tickers": "BTC, ETH", "time": "02/25 10:00", "summary": "중국 인민은행, 해외 크립토 거래소의 위안화 결제 차단 강화. 단기 매도 압력 우려."},
    {"title": "테슬라, 신형 로보택시 플랫폼 공개", "sentiment": "positive", "score": 0.65, "impact": 6, "tickers": "TSLA", "time": "02/25 08:30", "summary": "완전자율주행 기반 로보택시 전용 플랫폼 발표. 2026년 3분기 상용화 목표."},
    {"title": "일본 BOJ, 추가 금리 인상 동결", "sentiment": "neutral", "score": 0.10, "impact": 5, "tickers": "USD/JPY", "time": "02/24 22:00", "summary": "BOJ, 시장 예상대로 금리 동결 결정. 엔화 약세 지속 전망."},
    {"title": "이더리움 Pectra 업그레이드 일정 확정", "sentiment": "positive", "score": 0.73, "impact": 7, "tickers": "ETH", "time": "02/24 18:00", "summary": "3월 중 메인넷 적용 확정. 스테이킹 효율성 개선 및 가스비 절감 기대."},
    {"title": "삼성전자, HBM3E 엔비디아 품질 테스트 통과", "sentiment": "positive", "score": 0.80, "impact": 8, "tickers": "005930.KS", "time": "02/24 15:00", "summary": "HBM3E 제품이 엔비디아 품질 테스트를 최종 통과. 하반기 본격 납품 예정."},
    {"title": "미국 소비자물가지수(CPI) 예상 상회", "sentiment": "negative", "score": -0.60, "impact": 8, "tickers": "SPY, BTC, GOLD", "time": "02/24 09:30", "summary": "1월 CPI 3.1% 기록, 시장 예상 2.9% 상회. 금리 인하 지연 우려 확대."},
]

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

    response = templates.TemplateResponse(template, context)

    # 쿠키 없으면 새 세션 발급 (Basic Auth로 최초 통과한 경우)
    if not request.cookies.get(COOKIE_NAME):
        create_session(request, response)

    return response


# ── 페이지 라우트 ────────────────────────────────────────────

@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request):
    return _page_response(request, "pages/dashboard.html", {
        "request": request,
        "page": "dashboard",
        "portfolio": MOCK_PORTFOLIO,
        "signals": MOCK_SIGNALS[:5],
        "news": MOCK_NEWS[:5],
        "chart": MOCK_CHART_DATA,
    })


@router.get("/portfolio", response_class=HTMLResponse)
async def portfolio(request: Request):
    return _page_response(request, "pages/portfolio.html", {
        "request": request,
        "page": "portfolio",
        "portfolio": MOCK_PORTFOLIO,
    })


@router.get("/research", response_class=HTMLResponse)
async def research(request: Request):
    return _page_response(request, "pages/research.html", {
        "request": request,
        "page": "research",
    })


@router.get("/signals", response_class=HTMLResponse)
async def signals(request: Request):
    return _page_response(request, "pages/signals.html", {
        "request": request,
        "page": "signals",
        "signals": MOCK_SIGNALS,
    })


@router.get("/news", response_class=HTMLResponse)
async def news(request: Request):
    return _page_response(request, "pages/news.html", {
        "request": request,
        "page": "news",
        "news": MOCK_NEWS,
    })
