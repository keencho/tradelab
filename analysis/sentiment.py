"""뉴스 센티멘트 분석 — LLM 배치 호출."""

import time

from analysis.llm import call_llm, parse_json_response
from config import get_logger

logger = get_logger("sentiment")

BATCH_SIZE = 8

PROMPT_TEMPLATE = """다음 뉴스 {count}건을 분석해주세요. 반드시 JSON 배열로만 응답하세요.

각 뉴스에 대해:
- index: 뉴스 번호 (0부터)
- sentiment: "positive" | "negative" | "neutral"
- score: -1.0 ~ 1.0 (부정 ~ 긍정)
- impact: 1~10 (시장 영향도. 10이 가장 큼)
- tickers: 관련 종목 배열 (예: ["AAPL", "BTC"])
- summary: 반드시 한국어로 요약 1줄 (30자 이내, 영어 뉴스도 한국어로 번역하여 요약)

중요: summary는 무조건 한국어로 작성하세요. 영문 뉴스라도 한국어로 번역해서 요약합니다.

{news_block}

JSON 배열만 출력하세요:"""


def _build_news_block(articles: list[dict]) -> str:
    lines = []
    for i, a in enumerate(articles):
        title = a.get("title", "")
        summary = a.get("summary", "")
        text = f"[{i}] {title}"
        if summary:
            text += f" — {summary[:200]}"
        lines.append(text)
    return "\n".join(lines)


def analyze_batch(articles: list[dict]) -> list[dict]:
    """뉴스 배치 센티멘트 분석. 각 article에 sentiment 필드 추가하여 반환."""
    if not articles:
        return []

    results = []

    total_batches = (len(articles) + BATCH_SIZE - 1) // BATCH_SIZE

    for i in range(0, len(articles), BATCH_SIZE):
        batch_num = i // BATCH_SIZE + 1
        batch = articles[i:i + BATCH_SIZE]
        news_block = _build_news_block(batch)
        prompt = PROMPT_TEMPLATE.format(count=len(batch), news_block=news_block)

        # RPM 제한 방지: 첫 배치 이후 5초 대기 (Gemini 15 RPM 기준)
        if i > 0:
            time.sleep(5)

        logger.info(f"센티멘트 분석 배치 {batch_num}/{total_batches} ({len(batch)}건)")
        response = call_llm(prompt)
        parsed = parse_json_response(response)

        if parsed and len(parsed) >= len(batch):
            for j, article in enumerate(batch):
                analysis = parsed[j] if j < len(parsed) else {}
                article["sentiment"] = analysis.get("sentiment", "neutral")
                article["score"] = analysis.get("score", 0.0)
                article["impact"] = analysis.get("impact", 5)
                article["ai_tickers"] = analysis.get("tickers", [])
                article["ai_summary"] = analysis.get("summary", "")
                results.append(article)
        else:
            # 파싱 실패 — 기본값으로 채움
            logger.warning(f"센티멘트 분석 파싱 실패, 배치 {i}~{i + len(batch)}")
            for article in batch:
                article["sentiment"] = "neutral"
                article["score"] = 0.0
                article["impact"] = 5
                article["ai_tickers"] = []
                article["ai_summary"] = ""
                results.append(article)

    return results
