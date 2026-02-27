"""LLM 호출 모듈 — Gemini → Groq → Cerebras 자동 폴백."""

import json
import time
from dataclasses import dataclass

import httpx

from config import GEMINI_API_KEY, GROQ_API_KEY, CEREBRAS_API_KEY, get_logger

logger = get_logger("llm")


@dataclass
class LLMProvider:
    name: str
    api_key: str
    url: str
    model: str
    rpm: int
    rpd: int
    _today_count: int = 0
    _last_reset: float = 0.0

    def available(self) -> bool:
        if not self.api_key:
            return False
        now = time.time()
        # 일일 카운터 리셋 (24시간 단위)
        if now - self._last_reset > 86400:
            self._today_count = 0
            self._last_reset = now
        return self._today_count < self.rpd

    def increment(self):
        self._today_count += 1


# ── 프로바이더 목록 ─────────────────────────────────

_providers: list[LLMProvider] = []


def _init_providers():
    global _providers
    _providers = [
        LLMProvider(
            name="gemini",
            api_key=GEMINI_API_KEY,
            url=f"https://generativelanguage.googleapis.com/v1beta/models/{{model}}:generateContent?key={{key}}",
            model="gemini-2.5-flash",
            rpm=10,
            rpd=250,
        ),
        LLMProvider(
            name="groq",
            api_key=GROQ_API_KEY,
            url="https://api.groq.com/openai/v1/chat/completions",
            model="llama-3.1-8b-instant",
            rpm=30,
            rpd=14400,
        ),
        LLMProvider(
            name="cerebras",
            api_key=CEREBRAS_API_KEY,
            url="https://api.cerebras.ai/v1/chat/completions",
            model="llama3.1-8b",
            rpm=30,
            rpd=14400,
        ),
    ]


def _call_gemini(provider: LLMProvider, prompt: str) -> str:
    """Gemini API 호출."""
    url = provider.url.format(model=provider.model, key=provider.api_key)
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 4096},
    }
    resp = httpx.post(url, json=payload, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _call_openai_compat(provider: LLMProvider, prompt: str) -> str:
    """OpenAI 호환 API 호출 (Groq, Cerebras)."""
    headers = {
        "Authorization": f"Bearer {provider.api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": provider.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 4096,
    }
    resp = httpx.post(provider.url, json=payload, headers=headers, timeout=30)
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def call_llm(prompt: str, max_retries: int = 2) -> str | None:
    """LLM 호출 + 자동 폴백. 모든 프로바이더 실패 시 None 반환."""
    if not _providers:
        _init_providers()

    for provider in _providers:
        if not provider.available():
            continue

        for attempt in range(max_retries):
            try:
                if provider.name == "gemini":
                    result = _call_gemini(provider, prompt)
                else:
                    result = _call_openai_compat(provider, prompt)

                provider.increment()
                return result

            except httpx.HTTPStatusError as e:
                if e.response.status_code == 429:
                    if attempt < max_retries - 1:
                        wait = (attempt + 1) * 10  # 10초, 20초 대기
                        logger.warning(f"LLM rate limit: {provider.name}, {wait}초 후 재시도 ({attempt + 1}/{max_retries})")
                        time.sleep(wait)
                        continue
                    # 재시도 소진 → 이 프로바이더 당일 스킵 + 다음으로 전환
                    logger.warning(f"LLM rate limit: {provider.name}, 당일 스킵 처리 후 다음 프로바이더로 전환")
                    provider._today_count = provider.rpd
                    break
                logger.error(f"LLM HTTP error: {provider.name} {e.response.status_code}")
                break  # 429 외 HTTP 에러는 재시도 안 함
            except Exception as e:
                logger.error(f"LLM error: {provider.name} {e}")
                break  # 일반 에러도 재시도 안 함

    logger.error("모든 LLM 프로바이더 실패")
    return None


def parse_json_response(text: str) -> list[dict] | None:
    """LLM 응답에서 JSON 배열 추출."""
    if not text:
        return None
    # ```json ... ``` 블록 추출
    if "```" in text:
        start = text.find("```")
        end = text.rfind("```")
        if start != end:
            inner = text[start:end]
            # ```json 제거
            first_newline = inner.find("\n")
            if first_newline != -1:
                inner = inner[first_newline + 1:]
            text = inner
    try:
        parsed = json.loads(text.strip())
        if isinstance(parsed, list):
            return parsed
        if isinstance(parsed, dict):
            return [parsed]
    except json.JSONDecodeError:
        logger.warning(f"LLM JSON 파싱 실패: {text[:200]}")
    return None
