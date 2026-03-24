"""매크로 지표 + DART 공시 수집.

cron: 0 * * * * cd ~/tradelab && venv/bin/python scripts/collect_macro.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from config import KST, get_logger
from db.database import SessionLocal
from db.models import SignalData
from data.signal_collectors import collect_all_macro

logger = get_logger("collect_macro")


def run():
    logger.info("매크로 수집 시작")
    data = collect_all_macro()

    if not data:
        logger.info("수집된 매크로 없음")
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
                logger.error(f"매크로 저장 실패: {e}")

        session.commit()
        logger.info(f"매크로 저장 완료: {saved}건")
    except Exception as e:
        session.rollback()
        logger.error(f"매크로 파이프라인 에러: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"치명적 에러: {e}", exc_info=True)
