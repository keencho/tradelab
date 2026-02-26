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
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")

# --- 시그널/매크로 ---
ETHERSCAN_API_KEY = os.getenv("ETHERSCAN_API_KEY", "")
FRED_API_KEY = os.getenv("FRED_API_KEY", "")
DART_API_KEY = os.getenv("DART_API_KEY", "")

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
