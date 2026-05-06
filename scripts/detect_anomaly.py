"""이상 탐지 + 실시간 가격 감지 + LLM 해석 + Signal 저장 + 텔레그램 알림.

cron: */5 * * * * cd ~/tradelab && sleep 30 && venv/bin/python scripts/detect_anomaly.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from config import (
    KST, AUTH_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
    MARKET_NAMES, SIGNAL_TYPE_NAMES,
    COOLDOWN_VS_CLOSE, COOLDOWN_MOMENTUM, COOLDOWN_ZSCORE,
    COOLDOWN_DEFAULT, COOLDOWN_DIRECTION_FLIP,
    PRICE_VS_CLOSE_RATCHET,
    get_logger,
)
from db.database import SessionLocal
from db.models import Signal
from analysis.anomaly import detect_anomalies, detect_price_anomalies, get_ai_analysis

logger = get_logger("detect_anomaly")

# 시그널 타입별 "같은 방향" 쿨다운 (분)
SAME_DIR_COOLDOWN = {
    "price_vs_close": COOLDOWN_VS_CLOSE,   # 24h — 실제 차단은 ratchet 가 함
    "price_momentum": COOLDOWN_MOMENTUM,   # 30분
}


def _vs_close_step(pct: float) -> int:
    """전일 대비 % → ratchet 단계 인덱스. 단계 변동 없으면 알림 X."""
    p = abs(pct)
    step = 0
    for level in PRICE_VS_CLOSE_RATCHET:
        if p >= level:
            step += 1
        else:
            break
    return step


def _send_telegram(message: str):
    """텔레그램 알림 전송."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        from urllib.request import urlopen, Request as UrlRequest
        from urllib.parse import urlencode
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": message}).encode()
        req = UrlRequest(url, data=data, method="POST")
        urlopen(req, timeout=5)
    except Exception:
        pass


def _is_duplicate(session, anomaly: dict, now: datetime) -> bool:
    """
    중복 알림 차단 정책:
    - 같은 방향 + 같은 ticker/signal_type 의 마지막 알림 조회
    - price_vs_close: ratchet 단계가 더 높아졌을 때만 허용 (단계 동일 → 24h 차단)
    - 그 외: 시그널 타입별 같은 방향 쿨다운 (z-score 시그널은 12시간)
    - 방향 전환: COOLDOWN_DIRECTION_FLIP 분 (기본 30분) 차단 — 가격 흔들림으로 무력화 방지
    """
    sig_type = anomaly["signal_type"]
    direction = anomaly["direction"]

    recent = (
        session.query(Signal.direction, Signal.created_at, Signal.z_score)
        .filter(
            Signal.ticker == anomaly["ticker"],
            Signal.signal_type == sig_type,
        )
        .order_by(Signal.created_at.desc())
        .first()
    )

    if not recent:
        return False

    last_dir, last_at, last_z = recent
    age_min = (now - last_at).total_seconds() / 60

    # 방향 전환 — 무조건 통과가 아니라 짧은 별도 쿨다운 적용
    if last_dir != direction:
        return age_min < COOLDOWN_DIRECTION_FLIP

    # ── 같은 방향 ──

    # vs_close: ratchet
    if sig_type == "price_vs_close":
        cur_pct = anomaly.get("pct", 0.0)
        # last_z = pct/2 로 저장됨 → pct 복원
        last_pct = last_z * 2
        cur_step = _vs_close_step(cur_pct)
        last_step = _vs_close_step(last_pct)
        if cur_step > last_step:
            return False  # 더 큰 단계 도달 → 알림
        # 같은 단계: 안전망 24시간 (다음 거래일 리셋용)
        return age_min < COOLDOWN_VS_CLOSE

    # 그 외 시그널 — 시그널 타입별 같은 방향 쿨다운
    same_dir_cooldown = SAME_DIR_COOLDOWN.get(sig_type)
    if same_dir_cooldown is None:
        # z-score 시그널 (open_interest, funding_rate, foreign_net_buy 등)
        same_dir_cooldown = COOLDOWN_ZSCORE

    return age_min < same_dir_cooldown


def run():
    logger.info("이상 탐지 시작")

    # 기존 z-score 이상 탐지 + 실시간 가격 감지
    anomalies = detect_anomalies(lookback_days=30)
    anomalies.extend(detect_price_anomalies())

    if not anomalies:
        logger.info("이상치 없음")
        return

    session = SessionLocal()
    try:
        now = datetime.now(KST).replace(tzinfo=None)

        created = 0
        new_anomalies = []
        for anomaly in anomalies:
            if _is_duplicate(session, anomaly, now):
                continue

            # LLM AI 해석
            ai_text = get_ai_analysis(anomaly)

            signal = Signal(
                ticker=anomaly["ticker"],
                signal_type=anomaly["signal_type"],
                direction=anomaly["direction"],
                confidence=anomaly["confidence"],
                description=anomaly["description"],
                ai_analysis=ai_text,
                source=anomaly.get("source", ""),
                market=anomaly.get("market", ""),
                z_score=anomaly.get("z_score", 0.0),
                raw_value=anomaly.get("raw_value", 0.0),
                created_at=now,
            )
            session.add(signal)
            new_anomalies.append(anomaly)
            created += 1

        session.commit()
        skipped = len(anomalies) - created
        logger.info(f"시그널 생성 완료: {created}건 (쿨다운 스킵: {skipped}건)")

        # 텔레그램 알림 — 묶어서 1건으로 발송 (서버에서만)
        if AUTH_ENABLED and new_anomalies:
            lines = [f"[TradeLab] 알림 {created}건\n"]
            for anomaly in new_anomalies:
                emoji = "\U0001f7e2" if anomaly["direction"] == "bullish" else "\U0001f534"
                direction_kr = "오를 수 있음" if anomaly["direction"] == "bullish" else "내릴 수 있음"
                ticker_display = anomaly.get("ticker_name") or anomaly["ticker"]
                signal_type_kr = SIGNAL_TYPE_NAMES.get(anomaly["signal_type"], anomaly["signal_type"])
                market_kr = MARKET_NAMES.get(anomaly.get("market", ""), "")

                line = (
                    f"{emoji} {ticker_display} ({market_kr})\n"
                    f"   {signal_type_kr} — {direction_kr}\n"
                    f"   {anomaly['description']}"
                )
                lines.append(line)

            _send_telegram("\n\n".join(lines))
    except Exception as e:
        session.rollback()
        logger.error(f"이상 탐지 에러: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"치명적 에러: {e}", exc_info=True)
