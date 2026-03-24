"""이상 탐지 + LLM 해석 + Signal 저장 + 텔레그램 알림.

cron: */5 * * * * cd ~/tradelab && sleep 30 && venv/bin/python scripts/detect_anomaly.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

from config import KST, AUTH_ENABLED, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from db.database import SessionLocal
from db.models import Signal
from analysis.anomaly import detect_anomalies, get_ai_analysis

logger = get_logger("detect_anomaly")


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


def run():
    logger.info("이상 탐지 시작")

    anomalies = detect_anomalies(lookback_days=30)
    if not anomalies:
        logger.info("이상치 없음")
        return

    session = SessionLocal()
    try:
        created = 0
        for anomaly in anomalies:
            # LLM AI 해석 (높은 신뢰도만)
            ai_text = ""
            if anomaly["confidence"] >= 0.7:
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
                created_at=datetime.now(KST).replace(tzinfo=None),
            )
            session.add(signal)
            created += 1

            # 텔레그램 알림 (서버에서만)
            if AUTH_ENABLED:
                emoji = "\U0001f7e2" if anomaly["direction"] == "bullish" else "\U0001f534"
                msg = (
                    f"[TradeLab 시그널] {emoji} {anomaly['ticker']}\n"
                    f"유형: {anomaly['signal_type']}\n"
                    f"방향: {anomaly['direction']} ({anomaly['confidence']:.0%})\n"
                    f"z-score: {anomaly['z_score']:+.2f}\n"
                    f"{anomaly['description']}"
                )
                if ai_text:
                    msg += f"\n\nAI: {ai_text[:200]}"
                _send_telegram(msg)

        session.commit()
        logger.info(f"시그널 생성 완료: {created}건")
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
