"""NXT 애프터마켓 종가 일일 캐시 — services/widget_pricing.py 의 nxt_close.json 갱신.

cron: 5 20 * * 1-5  (평일 20:05, NXT 마감 직후)

대상: RealHolding + RealQuickWatch + Watchlist 의 kr_stock 종목.
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

import time
from datetime import datetime

from config import KST, get_logger
from db.database import SessionLocal
from db.models import RealHolding, RealQuickWatch, Watchlist
from services.widget_pricing import collect_nxt_close_for

logger = get_logger("nxt_close_cron")


def _collect_tickers() -> set[str]:
    session = SessionLocal()
    try:
        tickers: set[str] = set()
        # 실투자 보유
        for h in session.query(RealHolding).filter(RealHolding.market == "kr_stock", RealHolding.qty > 0).all():
            tickers.add(h.ticker)
        # 관심 종목 (위젯)
        for w in session.query(RealQuickWatch).filter(RealQuickWatch.market == "kr_stock").all():
            tickers.add(w.ticker)
        # 일반 워치리스트
        for w in session.query(Watchlist).filter(Watchlist.market == "kr_stock", Watchlist.is_active == True).all():
            tickers.add(w.ticker)
        return tickers
    finally:
        session.close()


def main() -> int:
    today = datetime.now(KST).date()
    tickers = _collect_tickers()
    if not tickers:
        logger.info("대상 종목 없음")
        return 0

    logger.info(f"NXT 종가 캐시 시작 — {len(tickers)} 종목 ({today})")
    ok = 0
    for tk in tickers:
        try:
            price = collect_nxt_close_for(tk, today)
            if price and price > 0:
                ok += 1
        except Exception as e:
            logger.error(f"{tk}: {e}")
        time.sleep(0.2)  # 네이버 차단 방지

    logger.info(f"NXT 종가 캐시 완료 — {ok}/{len(tickers)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
