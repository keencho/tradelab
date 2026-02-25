from fastapi import APIRouter, Request, Response
from fastapi.responses import JSONResponse

from routes.auth import reset_session, require_auth

router = APIRouter()


@router.post("/session/reset")
async def session_reset(request: Request):
    """세션 리셋 — 24시간 타이머 재시작."""
    if not require_auth(request):
        return JSONResponse(status_code=401, content={"error": "unauthorized"})

    response = JSONResponse(content={"status": "ok"})
    reset_session(request, response)
    return response


@router.post("/trade")
async def create_trade():
    """가상매매 주문 처리 (Phase 2에서 구현)"""
    return {"status": "ok"}
