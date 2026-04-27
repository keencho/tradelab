import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.request import urlopen, Request as UrlRequest
from urllib.parse import urlencode

from fastapi import Request, Response, HTTPException, status
from fastapi.responses import JSONResponse

from config import (
    AUTH_ENABLED, AUTH_USERS, SESSION_EXPIRE_HOURS,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger,
)

logger = get_logger("auth")

COOKIE_NAME = "tl_session"
# token -> (expire_timestamp, username)
_sessions: dict[str, tuple[float, str]] = {}

_SESSIONS_FILE = Path(__file__).resolve().parent.parent / "sessions.json"


def _load_sessions():
    """앱 시작 시 디스크에서 세션 복원 — 재시작/배포 후에도 로그인 유지."""
    global _sessions
    if not _SESSIONS_FILE.exists():
        return
    try:
        with open(_SESSIONS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        now = time.time()
        _sessions = {
            tok: (float(v[0]), str(v[1]))
            for tok, v in data.items()
            if isinstance(v, (list, tuple)) and len(v) == 2 and float(v[0]) > now
        }
        logger.info(f"세션 복원: {len(_sessions)}개")
    except Exception as e:
        logger.error(f"세션 로드 실패: {e}")
        _sessions = {}


def _save_sessions():
    """현재 세션을 디스크에 저장 — 변경 시마다 호출."""
    try:
        tmp = _SESSIONS_FILE.with_suffix(".json.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in _sessions.items()}, f)
        tmp.replace(_SESSIONS_FILE)
    except Exception as e:
        logger.error(f"세션 저장 실패: {e}")


_load_sessions()


def _make_token() -> str:
    return secrets.token_hex(32)


def _session_ttl() -> float:
    return SESSION_EXPIRE_HOURS * 3600


def _check_cookie(request: Request) -> bool:
    """쿠키 세션이 유효한지 확인."""
    token = request.cookies.get(COOKIE_NAME)
    if not token or token not in _sessions:
        return False
    expire, _ = _sessions[token]
    if time.time() > expire:
        del _sessions[token]
        return False
    return True


def _get_client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _send_telegram(message: str):
    """Telegram 알림 전송."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
        req = UrlRequest(url, data=data, method="POST")
        urlopen(req, timeout=5)
    except Exception:
        pass


def require_auth(request: Request) -> bool:
    """인증 확인. 쿠키 유효하면 통과, 아니면 Basic Auth 요구."""
    if _check_cookie(request):
        return True

    # Basic Auth 헤더 확인
    auth = request.headers.get("authorization")
    if not auth or not auth.startswith("Basic "):
        return False

    import base64
    try:
        decoded = base64.b64decode(auth[6:]).decode("utf-8")
        username, password = decoded.split(":", 1)
    except Exception:
        return False

    if username in AUTH_USERS and AUTH_USERS[username] == password:
        return True

    return False


def _get_username(request: Request) -> str:
    """Basic Auth 헤더에서 유저명 추출."""
    import base64
    auth = request.headers.get("authorization", "")
    if auth.startswith("Basic "):
        try:
            decoded = base64.b64decode(auth[6:]).decode("utf-8")
            return decoded.split(":", 1)[0]
        except Exception:
            pass
    return "unknown"


def create_session(request: Request, response: Response):
    """세션 생성 + 쿠키 설정 + Telegram 알림."""
    username = _get_username(request)
    token = _make_token()
    _sessions[token] = (time.time() + _session_ttl(), username)
    _save_sessions()

    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(_session_ttl()),
        httponly=True,
        samesite="lax",
    )

    ip = _get_client_ip(request)
    logger.info(f"Login OK / user: {username} / IP: {ip}")
    if AUTH_ENABLED:
        _send_telegram(f"[TradeLab] Login\nUser: {username}\nIP: {ip}")


def reset_session(request: Request, response: Response):
    """세션 리셋 — 기존 삭제 후 새로 발급 (알림 없음)."""
    old_token = request.cookies.get(COOKIE_NAME)
    username = "unknown"
    if old_token and old_token in _sessions:
        _, username = _sessions[old_token]
        del _sessions[old_token]

    token = _make_token()
    _sessions[token] = (time.time() + _session_ttl(), username)
    _save_sessions()
    response.set_cookie(
        key=COOKIE_NAME,
        value=token,
        max_age=int(_session_ttl()),
        httponly=True,
        samesite="lax",
    )


def logout(request: Request):
    """세션 삭제."""
    token = request.cookies.get(COOKIE_NAME)
    if token and token in _sessions:
        del _sessions[token]
        _save_sessions()


def get_current_user(request: Request) -> str:
    """현재 로그인 username 반환. 인증 비활성화면 'sycho' (로컬 개발용)."""
    if not AUTH_ENABLED:
        return "sycho"
    token = request.cookies.get(COOKIE_NAME)
    if token and token in _sessions:
        expire, username = _sessions[token]
        if time.time() <= expire:
            return username
    # 쿠키 없거나 만료 — Basic Auth 헤더에서 추출
    return _get_username(request)
