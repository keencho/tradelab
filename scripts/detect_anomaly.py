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
    COOLDOWN_VS_CLOSE, COOLDOWN_MOMENTUM, COOLDOWN_DEFAULT,
    get_logger,
)
from db.database import SessionLocal
from db.models import Signal
from analysis.anomaly import detect_anomalies, detect_price_anomalies, get_ai_analysis

logger = get_logger("detect_anomaly")

# 시그널 타입별 쿨다운 (분)
COOLDOWN_MAP = {
    "price_vs_close": COOLDOWN_VS_CLOSE,   # 2시간
    "price_momentum": COOLDOWN_MOMENTUM,    # 30분
}


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
    """타입별 쿨다운 + 방향 전환 시 즉시 허용."""
    cooldown_min = COOLDOWN_MAP.get(anomaly["signal_type"], COOLDOWN_DEFAULT)
    cutoff = now - timedelta(minutes=cooldown_min)

    recent = (
        session.query(Signal.direction)
        .filter(
            Signal.ticker == anomaly["ticker"],
            Signal.signal_type == anomaly["signal_type"],
            Signal.created_at >= cutoff,
        )
        .order_by(Signal.created_at.desc())
        .first()
    )

    if not recent:
        return False

    # 방향이 바뀌면 쿨다운 무시
    if recent[0] != anomaly["direction"]:
        return False

    return True


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
