"""시그널 수집기 -- 코인/미국주식/한국주식/매크로."""

import socket
import time
from datetime import datetime, timedelta

import httpx

# 타임아웃 없는 외부 라이브러리(fredapi, OpenDartReader) 보호
socket.setdefaulttimeout(20)

from config import (
    KST, FINNHUB_API_KEY, ETHERSCAN_API_KEY, FRED_API_KEY, DART_API_KEY, ECOS_API_KEY,
    get_logger,
)

logger = get_logger("signal_collector")

# ── 워치리스트 (DB에서 동적 로드, 없으면 기본값) ───────────────

_DEFAULT_KR = ["005930", "000660", "373220", "005380", "035420"]
_DEFAULT_CRYPTO = ["BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT", "XRP/USDT:USDT"]
_DEFAULT_US = ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOG"]


def _get_watchlist(market: str, default: list[str]) -> list[str]:
    """DB에서 활성 워치리스트 조회. 비어있으면 기본값 사용."""
    try:
        from db.database import SessionLocal
        from db.models import Watchlist
        session = SessionLocal()
        rows = (
            session.query(Watchlist.ticker)
            .filter(Watchlist.market == market, Watchlist.is_active == True)
            .order_by(Watchlist.sort_order)
            .all()
        )
        session.close()
        tickers = [r[0] for r in rows]
        return tickers if tickers else default
    except Exception:
        return default


def get_kr_watchlist():
    return _get_watchlist("kr_stock", _DEFAULT_KR)

def get_crypto_watchlist():
    return _get_watchlist("crypto", _DEFAULT_CRYPTO)

def get_us_watchlist():
    return _get_watchlist("us_stock", _DEFAULT_US)


# ── 3-1. pykrx — 한국주식 수급 ──────────────────────────────

def collect_foreign_institutional(tickers: list[str] | None = None) -> list[dict]:
    """외국인/기관 순매수 수집."""
    from pykrx import stock as krx

    if tickers is None:
        tickers = get_kr_watchlist()

    today = datetime.now(KST).strftime("%Y%m%d")
    results = []

    for ticker in tickers:
        try:
            df = krx.get_market_trading_value_by_date(today, today, ticker)
            if df.empty:
                continue
            row = df.iloc[-1]
            foreign_val = float(row.get("외국인합계", 0))
            inst_val = float(row.get("기관합계", 0))
            results.append({
                "source": "pykrx", "data_type": "foreign_net_buy",
                "ticker": ticker, "market": "kr_stock",
                "value": foreign_val,
                "extra": {"institutional": inst_val},
            })
            results.append({
                "source": "pykrx", "data_type": "institutional_net_buy",
                "ticker": ticker, "market": "kr_stock",
                "value": inst_val,
            })
        except Exception as e:
            logger.error(f"pykrx 수급 [{ticker}]: {e}")

    return results


def collect_short_selling(tickers: list[str] | None = None) -> list[dict]:
    """공매도 잔고 비중 수집."""
    from pykrx import stock as krx

    if tickers is None:
        tickers = get_kr_watchlist()

    today = datetime.now(KST).strftime("%Y%m%d")
    start = (datetime.now(KST) - timedelta(days=7)).strftime("%Y%m%d")
    results = []

    for ticker in tickers:
        try:
            df = krx.get_shorting_balance_by_date(start, today, ticker)
            if df.empty:
                continue
            row = df.iloc[-1]
            ratio = float(row.get("공매도비중", 0))
            results.append({
                "source": "pykrx", "data_type": "short_ratio",
                "ticker": ticker, "market": "kr_stock",
                "value": ratio,
            })
        except Exception as e:
            logger.error(f"pykrx 공매도 [{ticker}]: {e}")

    return results


def collect_program_trading(tickers: list[str] | None = None) -> list[dict]:
    """프로그램 매매 순매수 수집."""
    from pykrx import stock as krx

    if tickers is None:
        tickers = get_kr_watchlist()

    today = datetime.now(KST).strftime("%Y%m%d")
    results = []

    for ticker in tickers:
        try:
            df = krx.get_market_trading_value_by_date(today, today, ticker, detail=True)
            if df.empty:
                continue
            row = df.iloc[-1]
            # detail=True 시 프로그램 매매 정보 포함
            net_buy = float(row.get("순매수", 0))
            results.append({
                "source": "pykrx", "data_type": "program_buy",
                "ticker": ticker, "market": "kr_stock",
                "value": net_buy,
            })
        except Exception as e:
            logger.error(f"pykrx 프로그램 [{ticker}]: {e}")

    return results


# ── 3-1b. 네이버 금융 — 한국주식 실시간 가격 ─────────────────

def _parse_naver_number(val) -> float:
    """네이버 API 숫자 파싱 (쉼표 문자열 → float)."""
    if isinstance(val, (int, float)):
        return float(val)
    if isinstance(val, str):
        return float(val.replace(",", "")) if val else 0.0
    return 0.0


def collect_kr_price(tickers: list[str] | None = None) -> list[dict]:
    """네이버 금융 비공식 API로 실시간 현재가 수집.

    API 필드:
      closePrice           "196,500"  — 현재가 (장중) / 종가 (장후)
      compareToPreviousClosePrice "1,500" — 전일 종가 대비 차이
      fluctuationsRatio    "0.76"     — 등락률 (%)
    """
    if tickers is None:
        tickers = get_kr_watchlist()

    results = []

    for ticker in tickers:
        try:
            resp = httpx.get(
                f"https://m.stock.naver.com/api/stock/{ticker}/basic",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            price = _parse_naver_number(data.get("closePrice", "0"))
            diff = _parse_naver_number(data.get("compareToPreviousClosePrice", "0"))
            prev_close = price - diff
            change_pct = _parse_naver_number(data.get("fluctuationsRatio", "0"))

            if price == 0 or prev_close == 0:
                continue

            results.append({
                "source": "naver", "data_type": "realtime_price",
                "ticker": ticker, "market": "kr_stock",
                "value": price,
                "extra": {
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                },
            })
            time.sleep(0.3)  # 차단 방지
        except Exception as e:
            logger.error(f"네이버 가격 [{ticker}]: {e}")

    return results


# ── 3-2. ccxt Binance — 코인 펀딩레이트 + OI ────────────────

def collect_funding_rate(symbols: list[str] | None = None) -> list[dict]:
    """Binance 선물 펀딩레이트 수집."""
    import ccxt

    if symbols is None:
        symbols = get_crypto_watchlist()

    exchange = ccxt.binance({"timeout": 15000, "options": {"defaultType": "future"}})
    results = []

    for symbol in symbols:
        try:
            funding = exchange.fetch_funding_rate(symbol)
            ticker_name = symbol.split("/")[0]
            results.append({
                "source": "ccxt", "data_type": "funding_rate",
                "ticker": ticker_name, "market": "crypto",
                "value": float(funding["fundingRate"]),
                "extra": {"next_funding_time": funding.get("fundingTimestamp")},
            })
        except Exception as e:
            logger.error(f"ccxt 펀딩레이트 [{symbol}]: {e}")

    return results


def collect_open_interest(symbols: list[str] | None = None) -> list[dict]:
    """Binance 선물 미결제약정 수집."""
    import ccxt

    if symbols is None:
        symbols = get_crypto_watchlist()

    exchange = ccxt.binance({"timeout": 15000, "options": {"defaultType": "future"}})
    results = []

    for symbol in symbols:
        try:
            oi = exchange.fetch_open_interest(symbol)
            ticker_name = symbol.split("/")[0]
            results.append({
                "source": "ccxt", "data_type": "open_interest",
                "ticker": ticker_name, "market": "crypto",
                "value": float(oi["openInterestAmount"]),
            })
        except Exception as e:
            logger.error(f"ccxt OI [{symbol}]: {e}")

    return results


# ── 3-2b. ccxt — 코인 실시간 가격 + 거래량 ─────────────────

def collect_crypto_price(symbols: list[str] | None = None) -> list[dict]:
    """코인 현재가 + 24시간 거래대금 수집."""
    import ccxt

    if symbols is None:
        symbols = get_crypto_watchlist()

    exchange = ccxt.binance({"timeout": 15000, "options": {"defaultType": "future"}})
    results = []

    for symbol in symbols:
        try:
            t = exchange.fetch_ticker(symbol)
            ticker_name = symbol.split("/")[0]
            change_pct = float(t.get("percentage", 0) or 0)
            volume = float(t.get("quoteVolume", 0) or 0)
            last = float(t.get("last", 0) or 0)
            prev_close = float(t.get("previousClose", 0) or 0) or (last / (1 + change_pct / 100) if change_pct != 0 else last)

            results.append({
                "source": "ccxt", "data_type": "realtime_price",
                "ticker": ticker_name, "market": "crypto",
                "value": last,
                "extra": {
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                    "trading_value": volume,
                },
            })
            results.append({
                "source": "ccxt", "data_type": "volume_spike",
                "ticker": ticker_name, "market": "crypto",
                "value": volume,
                "extra": {"last": last, "change_pct": change_pct},
            })
        except Exception as e:
            logger.error(f"ccxt 가격 [{symbol}]: {e}")

    return results


# ── 3-3. 공포/탐욕 지수 ──────────────────────────────────────

def collect_crypto_fear_greed() -> list[dict]:
    """코인 공포/탐욕 지수 수집 (alternative.me)."""
    try:
        resp = httpx.get("https://api.alternative.me/fng/?limit=1", timeout=10)
        resp.raise_for_status()
        data = resp.json()["data"][0]
        return [{
            "source": "alternative_me", "data_type": "fear_greed",
            "ticker": "CRYPTO", "market": "crypto",
            "value": float(data["value"]),
            "extra": {"classification": data["value_classification"]},
        }]
    except Exception as e:
        logger.error(f"alternative.me 공포/탐욕: {e}")
        return []


def collect_cnn_fear_greed() -> list[dict]:
    """CNN Fear & Greed Index — 현재 차단됨. 빈 리스트 반환."""
    # CNN dataviz API가 서버 IP를 차단 (418 에러)
    # 크립토 Fear & Greed (alternative.me)는 정상 작동
    return []


# ── 3-4. Etherscan — 고래 이체 ───────────────────────────────

def collect_etherscan_whales(min_value_eth: float = 100.0) -> list[dict]:
    """Etherscan V2 API로 최근 블록에서 고래 대량 이체 감지."""
    if not ETHERSCAN_API_KEY:
        return []

    results = []
    try:
        # V2 API: chainid=1 (Ethereum mainnet)
        resp = httpx.get(
            "https://api.etherscan.io/v2/api",
            params={
                "chainid": "1",
                "module": "proxy", "action": "eth_getBlockByNumber",
                "tag": "latest", "boolean": "true",
                "apikey": ETHERSCAN_API_KEY,
            },
            timeout=15,
        )
        resp.raise_for_status()
        block = resp.json().get("result", {})

        if not isinstance(block, dict):
            return results

        for tx in (block.get("transactions") or []):
            if not isinstance(tx, dict):
                continue
            value_wei = int(tx.get("value", "0x0"), 16)
            value_eth = value_wei / 1e18
            if value_eth >= min_value_eth:
                results.append({
                    "source": "etherscan", "data_type": "whale_transfer",
                    "ticker": "ETH", "market": "crypto",
                    "value": value_eth,
                    "extra": {
                        "from": tx.get("from", ""),
                        "to": tx.get("to", ""),
                        "hash": tx.get("hash", ""),
                    },
                })
    except Exception as e:
        logger.error(f"Etherscan 고래: {e}")

    return results


# ── 3-5. FRED — 미국 매크로 ──────────────────────────────────

def collect_fred_macro() -> list[dict]:
    """FRED에서 미국 매크로 지표 수집."""
    if not FRED_API_KEY:
        return []

    from fredapi import Fred
    fred = Fred(api_key=FRED_API_KEY)

    SERIES = [
        ("CPIAUCSL", "us_cpi", "미국 CPI"),
        ("FEDFUNDS", "us_fed_rate", "미국 연방기금금리"),
        ("UNRATE", "us_unemployment", "미국 실업률"),
        ("T10Y2Y", "us_yield_spread", "미국 10년-2년 금리차"),
        ("VIXCLS", "us_vix", "VIX 변동성 지수"),
    ]

    results = []
    for series_id, data_type, desc in SERIES:
        try:
            s = fred.get_series(
                series_id,
                observation_start=(datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d"),
            )
            if s.empty:
                continue
            latest = s.dropna().iloc[-1]
            results.append({
                "source": "fred", "data_type": data_type,
                "ticker": "", "market": "macro",
                "value": float(latest),
                "extra": {"series": series_id, "desc": desc, "date": str(s.index[-1].date())},
            })
        except Exception as e:
            logger.error(f"FRED [{series_id}]: {e}")

    return results


# ── 3-6. ECOS — 한국 매크로 ──────────────────────────────────

def collect_ecos_macro() -> list[dict]:
    """한국은행 ECOS에서 한국 매크로 지표 수집."""
    if not ECOS_API_KEY:
        return []

    SERIES = [
        ("722Y001", "M", "0101000", "kr_base_rate", "한국 기준금리"),
        ("901Y009", "M", "0", "kr_cpi", "한국 CPI"),
        ("901Y027", "M", "I16AA", "kr_unemployment", "한국 실업률"),
    ]

    today = datetime.now(KST)
    start = (today - timedelta(days=90)).strftime("%Y%m")
    end = today.strftime("%Y%m")

    results = []
    for table_code, freq, item_code, data_type, desc in SERIES:
        try:
            url = (
                f"https://ecos.bok.or.kr/api/StatisticSearch/{ECOS_API_KEY}"
                f"/json/kr/1/1/{table_code}/{freq}/{start}/{end}/{item_code}"
            )
            resp = httpx.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            rows = data.get("StatisticSearch", {}).get("row", [])
            if rows:
                latest = rows[-1]
                results.append({
                    "source": "ecos", "data_type": data_type,
                    "ticker": "", "market": "macro",
                    "value": float(latest["DATA_VALUE"]),
                    "extra": {"period": latest["TIME"], "desc": desc},
                })
        except Exception as e:
            logger.error(f"ECOS [{table_code}]: {e}")

    return results


# ── 3-7. DART — 한국 내부자 매매 ─────────────────────────────

def collect_dart_insider() -> list[dict]:
    """DART에서 최근 공시 목록 수집 (내부자/대량보유 관련 필터)."""
    if not DART_API_KEY:
        return []

    import OpenDartReader
    api = OpenDartReader(DART_API_KEY)

    results = []
    today = datetime.now(KST).strftime("%Y%m%d")

    # 당일 공시 목록 조회
    try:
        df = api.list_date_ex(today)
        if df is not None and not df.empty:
            # 내부자/대량보유 관련 공시 필터
            keywords = ["임원", "주요주주", "대량보유", "소유상황"]
            for _, row in df.iterrows():
                report_nm = str(row.get("report_nm", ""))
                if any(kw in report_nm for kw in keywords):
                    corp_name = str(row.get("corp_name", ""))
                    stock_code = str(row.get("stock_code", ""))
                    results.append({
                        "source": "dart",
                        "data_type": "insider_trade",
                        "ticker": stock_code,
                        "market": "kr_stock",
                        "value": 1.0,
                        "extra": {
                            "name": corp_name,
                            "report": report_nm,
                        },
                    })
    except Exception as e:
        logger.error(f"DART 공시 목록: {e}")

    return results


# ── 3-8. SEC EDGAR — 미국 내부자 매매 ────────────────────────

SEC_HEADERS = {"User-Agent": "TradeLab research@tradelab.app", "Accept-Encoding": "gzip, deflate"}


def collect_sec_insider(tickers: list[str] | None = None) -> list[dict]:
    """SEC EDGAR Form 4 (내부자 매매) 최근 제출 수집."""
    if tickers is None:
        tickers = get_us_watchlist()

    results = []
    start_date = (datetime.now() - timedelta(days=3)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            resp = httpx.get(
                "https://efts.sec.gov/LATEST/search-index",
                params={
                    "q": f'"{ticker}"',
                    "forms": "4",
                    "dateRange": "custom",
                    "startdt": start_date,
                },
                headers=SEC_HEADERS,
                timeout=15,
            )
            resp.raise_for_status()
            hits = resp.json().get("hits", {}).get("hits", [])

            for hit in hits[:5]:
                source = hit.get("_source", {})
                results.append({
                    "source": "sec_edgar", "data_type": "insider_trade",
                    "ticker": ticker, "market": "us_stock",
                    "value": 1.0,
                    "extra": {
                        "filing_date": source.get("file_date", ""),
                        "form_type": source.get("form_type", ""),
                        "entity": source.get("entity_name", ""),
                    },
                })
            time.sleep(0.5)  # SEC 10req/초 제한 준수
        except Exception as e:
            logger.error(f"SEC EDGAR [{ticker}]: {e}")

    return results


# ── 3-8b. Finnhub — 미국주식 실시간 가격 ────────────────────

def collect_us_price(tickers: list[str] | None = None) -> list[dict]:
    """Finnhub API로 미국주식 현재가 수집.

    GET https://finnhub.io/api/v1/quote?symbol=AAPL&token=KEY
    → { "c": 현재가, "pc": 전일종가, "dp": 등락률%, "d": 변동폭 }
    """
    if not FINNHUB_API_KEY:
        return []

    if tickers is None:
        tickers = get_us_watchlist()

    results = []

    for ticker in tickers:
        try:
            resp = httpx.get(
                "https://finnhub.io/api/v1/quote",
                params={"symbol": ticker, "token": FINNHUB_API_KEY},
                timeout=10,
            )
            resp.raise_for_status()
            data = resp.json()

            price = float(data.get("c", 0))
            prev_close = float(data.get("pc", 0))
            change_pct = float(data.get("dp", 0))

            if price == 0 or prev_close == 0:
                continue

            results.append({
                "source": "finnhub", "data_type": "realtime_price",
                "ticker": ticker, "market": "us_stock",
                "value": price,
                "extra": {
                    "prev_close": prev_close,
                    "change_pct": change_pct,
                },
            })
            time.sleep(0.3)
        except Exception as e:
            logger.error(f"Finnhub 가격 [{ticker}]: {e}")

    return results


# ── 3-9. Reddit — 소셜 버즈 ──────────────────────────────────

SUBREDDITS = [
    ("wallstreetbets", "us_stock"),
    ("cryptocurrency", "crypto"),
    ("ethtrader", "crypto"),
]


def collect_reddit_buzz() -> list[dict]:
    """Reddit 서브레딧 RSS 피드로 버즈 측정 (JSON API 차단 대응)."""
    import feedparser

    results = []

    for subreddit, market in SUBREDDITS:
        try:
            feed = feedparser.parse(f"https://www.reddit.com/r/{subreddit}/hot.rss")
            entries = feed.entries[:25]

            if not entries:
                continue

            results.append({
                "source": "reddit", "data_type": "reddit_buzz",
                "ticker": subreddit, "market": market,
                "value": float(len(entries)),
                "extra": {
                    "posts_count": len(entries),
                    "titles": [e.title for e in entries[:5]],
                },
            })
            time.sleep(2)
        except Exception as e:
            logger.error(f"Reddit [{subreddit}]: {e}")

    return results


# ── 3-10. 네이버 종토방 — 한국 소셜 버즈 ─────────────────────

def collect_naver_buzz(tickers: list[str] | None = None) -> list[dict]:
    """네이버 종토방 게시글 수 수집 (스크래핑)."""
    from bs4 import BeautifulSoup

    if tickers is None:
        tickers = get_kr_watchlist()

    results = []

    for ticker in tickers:
        try:
            resp = httpx.get(
                f"https://finance.naver.com/item/board.naver?code={ticker}",
                headers={"User-Agent": "Mozilla/5.0"},
                timeout=10,
            )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")

            rows = soup.select("table.type2 tr")
            post_count = len([r for r in rows if r.select("td.title")])

            results.append({
                "source": "naver", "data_type": "naver_buzz",
                "ticker": ticker, "market": "kr_stock",
                "value": float(post_count),
            })
            time.sleep(3)  # 차단 방지
        except Exception as e:
            logger.error(f"네이버 종토방 [{ticker}]: {e}")

    return results


# ── 3-11. 엔트리포인트 ───────────────────────────────────────

def is_kr_market_hours() -> bool:
    """한국 장중 시간인지 확인 (08:30~18:00 KST, 평일만)."""
    now = datetime.now(KST)
    hour_min = now.hour * 100 + now.minute
    weekday = now.weekday()  # 0=월 ~ 6=일
    return weekday < 5 and 830 <= hour_min <= 1800


def collect_all_signals() -> list[dict]:
    """모든 시그널 소스에서 수집 (5분 주기용)."""
    all_data = []

    # 코인 (24시간)
    all_data.extend(collect_funding_rate())
    all_data.extend(collect_open_interest())
    all_data.extend(collect_crypto_fear_greed())
    all_data.extend(collect_etherscan_whales())
    all_data.extend(collect_crypto_price())

    # 미국주식
    all_data.extend(collect_cnn_fear_greed())
    all_data.extend(collect_sec_insider())
    all_data.extend(collect_us_price())

    # 한국주식 (장중만)
    if is_kr_market_hours():
        all_data.extend(collect_foreign_institutional())
        all_data.extend(collect_short_selling())
        all_data.extend(collect_program_trading())
        all_data.extend(collect_kr_price())

    # 소셜
    all_data.extend(collect_reddit_buzz())

    logger.info(f"시그널 수집 완료: {len(all_data)}건")
    return all_data


def collect_all_macro() -> list[dict]:
    """매크로 지표 수집 (1시간 주기용). DART는 별도 스크립트에서 처리."""
    all_data = []
    all_data.extend(collect_fred_macro())
    all_data.extend(collect_ecos_macro())
    logger.info(f"매크로 수집 완료: {len(all_data)}건")
    return all_data
