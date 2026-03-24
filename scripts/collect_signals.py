"""시그널 수집 + DB 저장 파이프라인.

cron: */5 * * * * cd ~/tradelab && venv/bin/python scripts/collect_signals.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from config import KST, get_logger
from db.database import SessionLocal
from db.models import SignalData
from data.signal_collectors import collect_all_signals

logger = get_logger("collect_signals")


def run():
    logger.info("시그널 수집 시작")
    data = collect_all_signals()

    if not data:
        logger.info("수집된 시그널 없음")
        return

    session = SessionLocal()
    try:
        saved = 0
        now = datetime.now(KST).replace(tzinfo=None)

        for idx, item in enumerate(data):
            try:
                row = SignalData(
                    source=item["source"],
                    data_type=item["data_type"],
                    ticker=item.get("ticker", ""),
                    market=item.get("market", ""),
                    value=float(item["value"]),
                    extra=item.get("extra", {}),
                    collected_at=now + timedelta(microseconds=idx),
                )
                session.add(row)
                saved += 1
            except Exception as e:
                logger.error(f"시그널 저장 실패: {e}")

        session.commit()
        logger.info(f"시그널 저장 완료: {saved}건")
    except Exception as e:
        session.rollback()
        logger.error(f"시그널 파이프라인 에러: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"치명적 에러: {e}", exc_info=True)
