"""뉴스 수집기 — RSS + Finnhub."""

from datetime import datetime, timezone, timedelta

import feedparser
import httpx

from config import FINNHUB_API_KEY, KST, get_logger

logger = get_logger("news_collector")


# ── RSS 피드 설정 ────────────────────────────────────

RSS_FEEDS = {
    # 한국 경제/증시
    "한국경제 증권": "https://www.hankyung.com/feed/finance",
    "한국경제 경제": "https://www.hankyung.com/feed/economy",
    "파이낸셜 증권": "https://www.fnnews.com/rss/r20/fn_realnews_stock.xml",
    "파이낸셜 경제": "https://www.fnnews.com/rss/r20/fn_realnews_economy.xml",
    "파이낸셜 블록체인": "https://www.fnnews.com/rss/r20/fn_realnews_blockpost.xml",
    # 코인
    "CoinDesk": "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "CoinTelegraph": "https://cointelegraph.com/rss",
}


def collect_rss() -> list[dict]:
    """모든 RSS 피드에서 뉴스 수집."""
    articles = []

    for source_name, feed_url in RSS_FEEDS.items():
        try:
            feed = feedparser.parse(feed_url)
            for entry in feed.entries[:20]:  # 피드당 최대 20건
                published = _parse_rss_date(entry)
                # 24시간 이내 기사만
                if published and (datetime.now(KST) - published) > timedelta(hours=24):
                    continue

                articles.append({
                    "title": entry.get("title", "").strip(),
                    "url": entry.get("link", ""),
                    "summary": entry.get("description", entry.get("summary", ""))[:500],
                    "source": source_name,
                    "published_at": published or datetime.now(KST),
                })
        except Exception as e:
            logger.error(f"RSS 수집 실패 [{source_name}]: {e}")

    logger.info(f"RSS 수집 완료: {len(articles)}건")
    return articles


def _parse_rss_date(entry) -> datetime | None:
    """RSS 항목에서 날짜 파싱."""
    published = entry.get("published_parsed") or entry.get("updated_parsed")
    if published:
        from calendar import timegm
        ts = timegm(published)
        return datetime.fromtimestamp(ts, tz=KST)
    return None


# ── Finnhub ──────────────────────────────────────────

FINNHUB_BASE = "https://finnhub.io/api/v1"


def collect_finnhub_market() -> list[dict]:
    """Finnhub 마켓 뉴스 수집 (general + crypto)."""
    if not FINNHUB_API_KEY:
        return []

    articles = []
    for category in ("general", "crypto"):
        try:
            resp = httpx.get(
                f"{FINNHUB_BASE}/news",
                params={"category": category, "token": FINNHUB_API_KEY},
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json()[:30]:
                published = datetime.fromtimestamp(item["datetime"], tz=KST)
                if (datetime.now(KST) - published) > timedelta(hours=24):
                    continue
                articles.append({
                    "title": item.get("headline", ""),
                    "url": item.get("url", ""),
                    "summary": item.get("summary", "")[:500],
                    "source": f"finnhub_{category}",
                    "published_at": published,
                    "tickers": item.get("related", ""),
                })
        except Exception as e:
            logger.error(f"Finnhub {category} 수집 실패: {e}")

    logger.info(f"Finnhub 수집 완료: {len(articles)}건")
    return articles


def collect_finnhub_company(tickers: list[str]) -> list[dict]:
    """Finnhub 종목별 뉴스 수집."""
    if not FINNHUB_API_KEY:
        return []

    articles = []
    today = datetime.now(KST).strftime("%Y-%m-%d")
    yesterday = (datetime.now(KST) - timedelta(days=1)).strftime("%Y-%m-%d")

    for ticker in tickers:
        try:
            resp = httpx.get(
                f"{FINNHUB_BASE}/company-news",
                params={
                    "symbol": ticker,
                    "from": yesterday,
                    "to": today,
                    "token": FINNHUB_API_KEY,
                },
                timeout=15,
            )
            resp.raise_for_status()
            for item in resp.json()[:10]:
                published = datetime.fromtimestamp(item["datetime"], tz=KST)
                articles.append({
                    "title": item.get("headline", ""),
                    "url": item.get("url", ""),
                    "summary": item.get("summary", "")[:500],
                    "source": "finnhub_company",
                    "published_at": published,
                    "tickers": ticker,
                })
        except Exception as e:
            logger.error(f"Finnhub company [{ticker}] 수집 실패: {e}")

    logger.info(f"Finnhub company 수집 완료: {len(articles)}건 ({len(tickers)} 종목)")
    return articles


# ── 전체 수집 ────────────────────────────────────────

# 기본 워치리스트 (Finnhub company 뉴스 조회용)
DEFAULT_WATCHLIST = ["AAPL", "NVDA", "MSFT", "TSLA", "AMZN", "GOOG"]


def collect_all(watchlist: list[str] | None = None) -> list[dict]:
    """모든 소스에서 뉴스 수집 + URL 기준 중복 제거."""
    if watchlist is None:
        watchlist = DEFAULT_WATCHLIST

    all_articles = []
    all_articles.extend(collect_rss())
    all_articles.extend(collect_finnhub_market())
    all_articles.extend(collect_finnhub_company(watchlist))

    # URL 기준 중복 제거
    seen_urls: set[str] = set()
    unique = []
    for article in all_articles:
        url = article.get("url", "")
        if not url or url in seen_urls:
            continue
        seen_urls.add(url)
        unique.append(article)

    logger.info(f"전체 수집: {len(all_articles)}건 → 중복 제거 후 {len(unique)}건")
    return unique
