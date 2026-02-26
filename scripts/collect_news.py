"""뉴스 수집 + 센티멘트 분석 + DB 저장 파이프라인.

cron으로 주기적 실행:
    */10 * * * * cd /home/ubuntu/tradelab && .venv/bin/python scripts/collect_news.py
"""

import sys
from pathlib import Path

# 프로젝트 루트를 path에 추가
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

from sqlalchemy import select

from config import KST, get_logger


def _to_kst_naive(dt_value) -> datetime:
    """timezone-aware datetime → KST naive datetime (DB 저장용)."""
    if dt_value is None:
        return datetime.now(KST).replace(tzinfo=None)
    if hasattr(dt_value, "tzinfo") and dt_value.tzinfo is not None:
        return dt_value.astimezone(KST).replace(tzinfo=None)
    return dt_value
from db.database import SessionLocal
from db.models import News
from data.news_collector import collect_all
from analysis.sentiment import analyze_batch

logger = get_logger("collect_news")


def _already_exists(session, url: str) -> bool:
    """URL 기준 중복 체크."""
    stmt = select(News.id).where(News.url == url).limit(1)
    return session.execute(stmt).first() is not None


def run():
    logger.info("뉴스 수집 시작")

    # 1. 수집
    articles = collect_all()
    if not articles:
        logger.info("수집된 뉴스 없음")
        return

    # 2. DB 중복 체크
    session = SessionLocal()
    try:
        new_articles = []
        for article in articles:
            url = article.get("url", "")
            if url and not _already_exists(session, url):
                new_articles.append(article)

        if not new_articles:
            logger.info("새로운 뉴스 없음 (전부 중복)")
            return

        logger.info(f"새 뉴스 {len(new_articles)}건, 센티멘트 분석 시작")

        # 3. 센티멘트 분석
        analyzed = analyze_batch(new_articles)

        # 4. DB 저장
        saved = 0
        for article in analyzed:
            try:
                tickers = article.get("tickers", "")
                ai_tickers = article.get("ai_tickers", [])
                if ai_tickers and isinstance(ai_tickers, list):
                    tickers = ", ".join(ai_tickers)

                news = News(
                    title=article["title"][:500],
                    url=article.get("url", "")[:1000],
                    source=article.get("source", "")[:100],
                    content=article.get("summary", ""),
                    sentiment_label=article.get("sentiment", "neutral"),
                    sentiment_score=float(article.get("score", 0.0)),
                    impact=int(article.get("impact", 5)),
                    related_tickers=str(tickers)[:200],
                    summary=article.get("ai_summary", "")[:500],
                    published_at=_to_kst_naive(article.get("published_at")),
                    analyzed_at=datetime.now(KST).replace(tzinfo=None),
                )
                session.add(news)
                saved += 1
            except Exception as e:
                logger.error(f"뉴스 저장 실패: {e}")

        session.commit()
        logger.info(f"뉴스 저장 완료: {saved}건")

    except Exception as e:
        session.rollback()
        logger.error(f"뉴스 파이프라인 에러: {e}")
    finally:
        session.close()


if __name__ == "__main__":
    run()
