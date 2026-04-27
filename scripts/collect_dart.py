"""DART 공시 수집 (일일).

cron: 30 16 * * * cd ~/tradelab && venv/bin/python scripts/collect_dart.py

dart.fss.or.kr 스크래핑 기반이라 응답 지연이 잦음. 장마감 이후 하루 1회만 수집.
"""

import os
import signal
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta

from config import KST, get_logger
from db.database import SessionLocal
from db.models import SignalData
from data.signal_collectors import collect_dart_insider

logger = get_logger("collect_dart")


# DART는 dart.fss.or.kr 스크래핑 기반이라 무한 hang 가능 — 3분 하드 타임아웃
def _hard_timeout(signum, frame):
    logger.error("collect_dart 하드 타임아웃 (180s) — 프로세스 강제 종료")
    os._exit(1)

signal.signal(signal.SIGALRM, _hard_timeout)
signal.alarm(180)


def run():
    logger.info("DART 공시 수집 시작")
    data = collect_dart_insider()

    if not data:
        logger.info("수집된 DART 공시 없음")
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
                logger.error(f"DART 저장 실패: {e}")

        session.commit()
        logger.info(f"DART 저장 완료: {saved}건")
    except Exception as e:
        session.rollback()
        logger.error(f"DART 파이프라인 에러: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    try:
        run()
    except Exception as e:
        logger.error(f"치명적 에러: {e}", exc_info=True)
