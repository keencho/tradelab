from datetime import datetime, timedelta

import pandas as pd
import yfinance as yf
import ccxt

from db.database import SessionLocal
from db.models import Price


def collect_stock_prices(tickers: list[str], period: str = "1d", interval: str = "1h"):
    """주식 가격 수집 (yfinance)"""
    db = SessionLocal()
    try:
        for ticker in tickers:
            df = yf.download(ticker, period=period, interval=interval, progress=False)
            if df.empty:
                continue

            for dt, row in df.iterrows():
                price = Price(
                    ticker=ticker,
                    market="stock",
                    dt=dt.to_pydatetime(),
                    open=float(row["Open"]),
                    high=float(row["High"]),
                    low=float(row["Low"]),
                    close=float(row["Close"]),
                    volume=float(row["Volume"]),
                )
                db.add(price)

            db.commit()
            print(f"[stock] {ticker}: {len(df)}건 수집")
    finally:
        db.close()


def collect_crypto_prices(symbols: list[str], timeframe: str = "1h", limit: int = 24):
    """코인 가격 수집 (ccxt/Binance)"""
    exchange = ccxt.binance()
    db = SessionLocal()
    try:
        for symbol in symbols:
            ohlcv = exchange.fetch_ohlcv(symbol, timeframe=timeframe, limit=limit)
            if not ohlcv:
                continue

            for candle in ohlcv:
                ts, o, h, l, c, v = candle
                price = Price(
                    ticker=symbol,
                    market="crypto",
                    dt=datetime.utcfromtimestamp(ts / 1000),
                    open=o,
                    high=h,
                    low=l,
                    close=c,
                    volume=v,
                )
                db.add(price)

            db.commit()
            print(f"[crypto] {symbol}: {len(ohlcv)}건 수집")
    finally:
        db.close()


# 직접 실행시 테스트용
if __name__ == "__main__":
    collect_stock_prices(["AAPL", "NVDA"], period="5d", interval="1d")
    collect_crypto_prices(["BTC/USDT", "ETH/USDT"], timeframe="1d", limit=5)
    print("수집 완료")
