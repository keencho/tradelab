"""초기 워치리스트 시딩.

서버에서 1회 실행:
  TRADELAB_ENV=server venv/bin/python scripts/seed_watchlist.py
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from db.database import SessionLocal, init_db
from db.models import Watchlist

SEED_DATA = [
    # 한국주식
    ("kr_stock", "005930", "삼성전자"),
    ("kr_stock", "000660", "SK하이닉스"),
    ("kr_stock", "373220", "LG에너지솔루션"),
    ("kr_stock", "005380", "현대차"),
    ("kr_stock", "035420", "NAVER"),
    ("kr_stock", "035720", "카카오"),
    ("kr_stock", "006400", "삼성SDI"),
    ("kr_stock", "051910", "LG화학"),
    ("kr_stock", "068270", "셀트리온"),
    ("kr_stock", "105560", "KB금융"),

    # 미국주식
    ("us_stock", "AAPL", "Apple Inc."),
    ("us_stock", "NVDA", "NVIDIA Corp."),
    ("us_stock", "MSFT", "Microsoft Corp."),
    ("us_stock", "TSLA", "Tesla Inc."),
    ("us_stock", "AMZN", "Amazon.com Inc."),
    ("us_stock", "GOOG", "Alphabet Inc. (Google)"),
    ("us_stock", "META", "Meta Platforms"),
    ("us_stock", "AMD", "Advanced Micro Devices"),
    ("us_stock", "PLTR", "Palantir Technologies"),
    ("us_stock", "COIN", "Coinbase Global"),

    # 코인
    ("crypto", "BTC/USDT:USDT", "비트코인"),
    ("crypto", "ETH/USDT:USDT", "이더리움"),
    ("crypto", "SOL/USDT:USDT", "솔라나"),
    ("crypto", "XRP/USDT:USDT", "리플"),
    ("crypto", "BNB/USDT:USDT", "바이낸스코인"),
    ("crypto", "DOGE/USDT:USDT", "도지코인"),
    ("crypto", "ADA/USDT:USDT", "카르다노"),
    ("crypto", "AVAX/USDT:USDT", "아발란체"),
]


def run():
    init_db()
    session = SessionLocal()
    try:
        added = 0
        for idx, (market, ticker, name) in enumerate(SEED_DATA):
            exists = session.query(Watchlist).filter(
                Watchlist.market == market, Watchlist.ticker == ticker
            ).first()
            if exists:
                continue
            session.add(Watchlist(
                market=market, ticker=ticker, name=name,
                sort_order=idx, is_active=True,
            ))
            added += 1

        session.commit()
        print(f"워치리스트 시딩 완료: {added}건 추가 (기존 {len(SEED_DATA) - added}건 스킵)")
    finally:
        session.close()


if __name__ == "__main__":
    run()
