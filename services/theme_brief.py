"""테마 AI 브리핑 — Phase 2.

핫 업종을 LLM 으로 해석:
- 왜 뜨는가 (related 뉴스 기반)
- 핵심 종목 3개
- 리스크

캐시: (sector_no, date) 키 — 하루 1번만 생성.
"""

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Iterable

from sqlalchemy import or_

from config import KST, get_logger
from analysis.llm import call_llm, parse_json_response

logger = get_logger("theme_brief")


# ── 캐시 (in-memory, 일 단위) ──────────────────────────

@dataclass
class ThemeBrief:
    sector_no: str
    sector_name: str
    headline: str            # 한 줄 요약 (왜 핫한가)
    risks: str               # 리스크 1줄
    top_stocks: list[str]    # 핵심 종목명 3개
    date_kst: str            # 'YYYY-MM-DD'
    based_on_news: int       # 사용된 뉴스 개수


_cache: dict[str, ThemeBrief] = {}  # key: f"{sector_no}:{date}"


def _cache_key(no: str, date_kst: str) -> str:
    return f"{no}:{date_kst}"


# ── 뉴스 매칭 ──────────────────────────────────────────

# 섹터명에서 핵심 키워드 추출
_NOISE_WORDS = {"및", "와", "과", "그리고", "장비", "제품", "서비스", "관련"}


def _extract_keywords(sector_name: str) -> list[str]:
    """업종명을 키워드들로 분해. ex) '반도체와반도체장비' → ['반도체']"""
    # '와', '및', '/', ',' 등으로 분리
    parts = re.split(r"[와및과,/&]|그리고", sector_name)
    kws: list[str] = []
    for p in parts:
        p = p.strip()
        if not p:
            continue
        # '장비', '제품' 같은 꼬리 제거
        for tail in ("와반도체장비", "장비및부품", "장비", "부품", "제품"):
            if p.endswith(tail) and len(p) > len(tail):
                p = p[: -len(tail)]
        p = p.strip()
        if p and p not in _NOISE_WORDS and len(p) >= 2:
            kws.append(p)
    # 중복 제거 + 원본 sector_name 도 포함 (긴 매칭용)
    if sector_name not in kws:
        kws.insert(0, sector_name)
    return list(dict.fromkeys(kws))[:5]


def _fetch_related_news(keywords: list[str], hours: int = 48, limit: int = 15) -> list[dict]:
    """제목/요약에 키워드 포함된 최근 뉴스."""
    from db.database import SessionLocal
    from db.models import News

    if not keywords:
        return []

    session = SessionLocal()
    try:
        since = datetime.now(KST).replace(tzinfo=None) - timedelta(hours=hours)
        conds = []
        for kw in keywords:
            like = f"%{kw}%"
            conds.append(News.title.ilike(like))
            conds.append(News.summary.ilike(like))
        rows = (
            session.query(News)
            .filter(News.published_at >= since)
            .filter(or_(*conds))
            .order_by(News.published_at.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "title": r.title,
                "summary": (r.summary or "")[:200],
                "sentiment": r.sentiment_label,
                "impact": r.impact,
            }
            for r in rows
        ]
    finally:
        session.close()


# ── LLM 프롬프트 ───────────────────────────────────────


def _build_prompt(sector_name: str, top_stocks: list[dict], news_items: list[dict]) -> str:
    stocks_txt = "\n".join(
        f"  - {s['name']} ({s['code']}): {s['change_pct']:+.2f}%"
        for s in top_stocks[:8]
    )
    news_txt = "\n".join(
        f"  - [{n.get('sentiment','')}] {n['title']}"
        + (f" — {n['summary'][:80]}" if n.get('summary') else "")
        for n in news_items[:10]
    )

    return f"""다음은 한국 증시의 한 업종(테마) 데이터입니다. 왜 오늘 핫한지 한국어로 분석하세요.

업종: {sector_name}

오늘의 상승 종목 (변동률 순):
{stocks_txt or '  (데이터 없음)'}

관련 최근 뉴스 (48시간):
{news_txt or '  (관련 뉴스 없음)'}

다음 JSON 형식으로만 답하세요. 코드블록(```) 없이 순수 JSON만:
{{
  "headline": "이 업종이 왜 뜨는지 한 줄 요약 (60자 이내, 사실 기반)",
  "risks": "이 테마의 단기 리스크 한 줄 (60자 이내)",
  "top_stocks": ["핵심 종목명 1", "핵심 종목명 2", "핵심 종목명 3"]
}}

규칙:
- 뉴스 데이터가 빈약하면 헤드라인에 "뉴스 부족" 같은 솔직한 표현 사용.
- "추천", "매수", "강력" 같은 자극적 단어 금지.
- 사실만. 추측 X.
- top_stocks 는 위에서 가장 변동 큰 종목들 중 선택."""


# ── 생성 ──────────────────────────────────────────────


def generate_brief(
    sector_no: str,
    sector_name: str,
    top_stocks: list[dict],  # [{code, name, change_pct}, ...]
    force: bool = False,
) -> ThemeBrief | None:
    """캐시 우선. 없으면 LLM 호출 → 캐시."""
    date_kst = datetime.now(KST).strftime("%Y-%m-%d")
    key = _cache_key(sector_no, date_kst)

    if not force and key in _cache:
        return _cache[key]

    keywords = _extract_keywords(sector_name)
    news_items = _fetch_related_news(keywords)

    prompt = _build_prompt(sector_name, top_stocks, news_items)
    raw = call_llm(prompt)
    if not raw:
        logger.warning(f"theme_brief: LLM 호출 실패 ({sector_name})")
        return None

    parsed = parse_json_response(raw)
    if not parsed:
        logger.warning(f"theme_brief: JSON 파싱 실패 ({sector_name})")
        return None

    obj = parsed[0] if isinstance(parsed, list) else parsed
    brief = ThemeBrief(
        sector_no=sector_no,
        sector_name=sector_name,
        headline=str(obj.get("headline", ""))[:200],
        risks=str(obj.get("risks", ""))[:200],
        top_stocks=[str(x)[:50] for x in (obj.get("top_stocks") or [])][:5],
        date_kst=date_kst,
        based_on_news=len(news_items),
    )
    _cache[key] = brief
    logger.info(f"theme_brief: 생성 [{sector_name}] news={len(news_items)}")
    return brief


def get_cached(sector_no: str) -> ThemeBrief | None:
    date_kst = datetime.now(KST).strftime("%Y-%m-%d")
    return _cache.get(_cache_key(sector_no, date_kst))


def clear_cache() -> None:
    _cache.clear()
