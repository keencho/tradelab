import os
import logging
from datetime import timezone, timedelta
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from dotenv import load_dotenv

# --- 시간대 ---
KST = timezone(timedelta(hours=9))

# .env 로딩 순서:
# 1) .env (공통 — API 키 등)
# 2) .env.local 또는 .env.server (환경별 — DB 접속 등)
#
# TRADELAB_ENV 환경변수로 구분:
#   - 미설정 or "local" → .env.local 로드
#   - "server"           → .env.server 로드

BASE_DIR = Path(__file__).parent

load_dotenv(BASE_DIR / ".env")

env = os.getenv("TRADELAB_ENV", "local")
load_dotenv(BASE_DIR / f".env.{env}", override=True)

# --- DB ---
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://tradelab:tradelab@localhost:5432/tradelab")

# --- LLM (센티멘트 분석 + 리포트) ---
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
CEREBRAS_API_KEY = os.getenv("CEREBRAS_API_KEY", "")

# --- 알림 ---
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

# --- 뉴스 수집 ---
FINNHUB_API_KEY = os.getenv("FINNHUB_API_KEY", "")
NAVER_CLIENT_ID = os.getenv("NAVER_CLIENT_ID", "")
NAVER_CLIENT_SECRET = os.getenv("NAVER_CLIENT_SECRET", "")

# --- 시그널/매크로 ---
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
DART_API_KEY = os.getenv("DART_API_KEY", "")
ECOS_API_KEY = os.getenv("ECOS_API_KEY", "")

# --- 인증 ---
AUTH_ENABLED = env != "local"  # 로컬에서는 인증 비활성화
SESSION_EXPIRE_HOURS = 24

# AUTH_USERS 형식: "user1:pw1,user2:pw2"
_raw_users = os.getenv("AUTH_USERS", "admin:admin")
AUTH_USERS: dict[str, str] = {}
for pair in _raw_users.split(","):
    if ":" in pair:
        u, p = pair.strip().split(":", 1)
        AUTH_USERS[u] = p

# --- 워치리스트 제한 ---
MAX_WATCHLIST = {
    "kr_stock": 15,
    "us_stock": 15,
    "crypto": 20,
}

MARKET_NAMES = {
    "kr_stock": "한국주식",
    "us_stock": "미국주식",
    "crypto": "코인",
    "macro": "매크로",
}

SIGNAL_TYPE_NAMES = {
    "foreign_net_buy": "외국인 순매수",
    "institutional_net_buy": "기관 순매수",
    "short_ratio": "공매도 비중",
    "program_buy": "프로그램 매매",
    "funding_rate": "펀딩레이트",
    "open_interest": "미결제약정",
    "fear_greed": "공포/탐욕지수",
    "whale_transfer": "고래 이체",
    "reddit_buzz": "Reddit 버즈",
    "naver_buzz": "네이버 버즈",
    "insider_buy": "내부자 매수",
    "insider_sell": "내부자 매도",
    "insider_trade": "내부자 거래",
    "us_vix": "변동성지수(VIX)",
    "us_yield_spread": "장단기 금리차",
    "price_vs_close": "전일 대비 급등락",
    "price_momentum": "장중 급등락",
    "volume_spike": "거래량 급증",
    "us_cpi": "미국 CPI",
    "us_fed_rate": "연방기금금리",
    "us_unemployment": "미국 실업률",
    "kr_base_rate": "한국 기준금리",
    "kr_cpi": "한국 CPI",
    "kr_unemployment": "한국 실업률",
}

# --- 가격 알림 기준 (%) ---
PRICE_ALERT_VS_CLOSE = 5.0    # 전일 종가 대비 ±5%
PRICE_ALERT_MOMENTUM = 3.0    # 직전 수집가 대비 ±3%

# --- 쿨다운 (분) ---
COOLDOWN_VS_CLOSE = 120       # 전일 대비: 2시간
COOLDOWN_MOMENTUM = 30        # 직전 대비: 30분
COOLDOWN_DEFAULT = 60         # 기타 시그널: 1시간

# --- 가상매매 ---
DEFAULT_CAPITAL = 100_000_000  # 1억원
STOCK_FEE_RATE = 0.00015      # 주식 수수료 0.015%
CRYPTO_FEE_RATE = 0.001       # 코인 수수료 0.1%


# --- 로깅 ---
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(exist_ok=True)

LOG_LEVEL = logging.DEBUG if env == "local" else logging.INFO
LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
LOG_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


def get_logger(name: str) -> logging.Logger:
    """모듈별 로거 생성. 파일 + 콘솔 동시 출력."""
    logger = logging.getLogger(name)

    if logger.handlers:
        return logger

    logger.setLevel(LOG_LEVEL)
    formatter = logging.Formatter(LOG_FORMAT, datefmt=LOG_DATE_FORMAT)

    # 파일 핸들러: 서버는 일별 로테이션(30일 보관), 로컬은 단순 append
    if env == "server":
        file_handler = TimedRotatingFileHandler(
            LOG_DIR / "app.log",
            when="midnight",
            backupCount=30,
            encoding="utf-8",
        )
    else:
        file_handler = logging.FileHandler(
            LOG_DIR / "app.log",
            encoding="utf-8",
        )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    # 콘솔 핸들러
    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
