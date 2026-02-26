# Phase 2: 뉴스 + 센티멘트 분석

## 수집 대상 & 소스

| 분야 | 소스 | 방식 | 제한 |
|------|------|------|------|
| 한국 경제/증시 | 한국경제 RSS, 파이낸셜뉴스 RSS | RSS 파싱 | 없음 |
| 미국 주식 | Finnhub company-news API | REST API | 60 req/분 (공유) |
| 미국 시장 전반 | Finnhub market-news (general) | REST API | 위와 공유 |
| 코인 뉴스 | Finnhub (crypto), CoinDesk RSS, CoinTelegraph RSS | REST + RSS | Finnhub만 제한 |
| 코인 센티멘트 | CryptoPanic | REST API | ~100 req/일 |

### RSS 피드 URL

**한국:**
- `https://www.hankyung.com/feed/finance` — 한경 증권
- `https://www.hankyung.com/feed/economy` — 한경 경제
- `https://www.fnnews.com/rss/r20/fn_realnews_stock.xml` — 파이낸셜 증권
- `https://www.fnnews.com/rss/r20/fn_realnews_economy.xml` — 파이낸셜 경제
- `https://www.fnnews.com/rss/r20/fn_realnews_blockpost.xml` — 파이낸셜 블록체인

**미국/코인:**
- `https://www.coindesk.com/arc/outboundfeeds/rss/` — CoinDesk
- `https://cointelegraph.com/rss` — CoinTelegraph
- `https://feeds.finance.yahoo.com/rss/2.0/headline?s={TICKER}` — Yahoo Finance 종목별

### Finnhub 엔드포인트

- 종목별 뉴스: `GET /api/v1/company-news?symbol=AAPL&from=2026-02-01&to=2026-02-26`
- 마켓 뉴스: `GET /api/v1/news?category=general|forex|crypto|merger`
- 센티멘트: `GET /api/v1/news-sentiment?symbol=AAPL` (bullishPercent, bearishPercent)

### CryptoPanic 엔드포인트

- `GET /api/v1/posts/?auth_token=TOKEN&currencies=BTC,ETH&filter=rising&regions=ko`
- 필터: `rising`, `hot`, `bullish`, `bearish`, `important`
- 커뮤니티 투표 기반 센티멘트 (votes.positive / votes.negative)

---

## LLM 센티멘트 분석

### 호출 방식

뉴스 5~10건을 배치로 묶어서 1회 호출:

```
프롬프트:
다음 뉴스 기사들을 분석해주세요. 각각에 대해 JSON으로:
- sentiment: "positive" | "negative" | "neutral"
- score: -1.0 ~ 1.0
- impact: 1~10 (시장 영향도)
- tickers: 관련 종목 배열
- summary: 한국어 요약 1줄

[1] Fed 파월 의장, 금리 인하 가능성 시사...
[2] NVIDIA 데이터센터 매출 120% 증가...
```

### Gemini 무료 한도 (2026년 2월 기준)

| 모델 | RPM | RPD (일일) | TPM |
|------|-----|-----------|-----|
| Gemini 2.5 Flash-Lite | 15 | 1,000 | 250,000 |
| Gemini 2.5 Flash | 10 | 250 | 250,000 |
| Gemini 1.5 Flash | 15 | 1,500 | 250,000 |

- 한도 초과 시 → 429 에러 (카드 미등록이면 과금 없음)
- 리셋: 매일 자정 Pacific Time (한국시간 오후 5시)

### 처리량 계산

10건 배치 기준:
- Flash-Lite 1,000 RPD × 10건 = 일일 최대 10,000건
- RPM 15 × 10건 = 분당 150건

실제 수집 예상: ~180건/시간 = ~4,300건/일 → 여유 있음

### 멀티 프로바이더 폴백

429 에러 시 자동으로 다음 프로바이더로 전환:

| 순서 | 프로바이더 | 모델 | 일일 한도 | 비용 |
|------|-----------|------|----------|------|
| 1 (메인) | Gemini | 2.5 Flash-Lite | 1,000 RPD | 무료 |
| 2 (백업) | Groq | Llama 3.1 8B | 14,400 RPD | 무료 |
| 3 (백업) | Cerebras | Llama 3.1 8B | 14,400 RPD | 무료 |

합산 일일 처리량: ~30,000건

---

## 파이프라인

```
[cron 매 10분] RSS 수집 (한경/파이낸셜/CoinDesk/CoinTelegraph)
    ↓
[cron 매 30분] Finnhub API (종목별 + 마켓 + 크립토)
    ↓
[cron 매 30분] CryptoPanic (코인 센티멘트)
    ↓
중복 제거 (URL 기준)
    ↓
[배치] LLM 센티멘트 분석 (Gemini → Groq → Cerebras 폴백)
    ↓
PostgreSQL 저장
    ↓
FastAPI 뉴스 페이지 (실데이터 + 필터)
```

---

## 필요한 API 키

| 서비스 | 가입 | 비용 |
|--------|------|------|
| Finnhub | finnhub.io | 무료 |
| CryptoPanic | cryptopanic.com | 무료 |
| Gemini | Google AI Studio | 무료 |
| Groq | console.groq.com | 무료 |
| Cerebras | cerebras.ai | 무료 |
| RSS | - | 키 불필요 |

---

## DB 테이블 (news)

```
id              SERIAL PRIMARY KEY
title           TEXT NOT NULL
summary         TEXT
source          VARCHAR(100)        -- "hankyung", "finnhub", "cryptopanic", ...
url             TEXT UNIQUE         -- 중복 체크용
published_at    TIMESTAMPTZ
sentiment       VARCHAR(10)         -- positive / negative / neutral
score           FLOAT               -- -1.0 ~ 1.0
impact          SMALLINT            -- 1 ~ 10
tickers         TEXT[]              -- {"AAPL", "BTC", "005930.KS"}
ai_summary      TEXT                -- LLM 생성 한국어 요약
raw_data        JSONB               -- 원본 응답 보관
created_at      TIMESTAMPTZ DEFAULT NOW()
```

---

## Phase 3에서 추가될 시그널/매크로 데이터

뉴스만으로는 매매 판단이 어려움. 뉴스는 대부분 후행 지표.
아래 데이터와 합쳐질 때 진짜 가치가 생김.

### 선행 지표 (가격보다 먼저 움직이는 것들)

| 데이터 | 소스 | 왜 중요한지 |
|--------|------|------------|
| 고래/내부자 매매 | Etherscan, SEC EDGAR, DART | 정보 가진 사람들이 먼저 움직임 |
| 소셜 버즈 | Reddit .json | 급격한 언급량 증가 = 가격 움직임 선행 |
| 온체인 거래소 입출금 | Etherscan | 출금 = 장기보유, 입금 = 매도 준비 |

### 매크로 지표 (시장 방향 결정)

| 데이터 | 소스 | 왜 중요한지 |
|--------|------|------------|
| CPI / PCE | FRED API | 인플레이션 → 금리 방향 → 시장 전체 방향 |
| Fed 금리 결정 | FRED API | 가장 큰 시장 이벤트 |
| 고용 지표 (NFP) | FRED API | 경기 과열/침체 판단 |

### 센티멘트 지표 (극단값에서 반전 신호)

| 데이터 | 소스 | 왜 중요한지 |
|--------|------|------------|
| 코인 공포/탐욕 지수 | alternative.me API | 극단적 공포 = 매수 기회, 극단적 탐욕 = 주의 |
| 코인 펀딩레이트 | ccxt (Binance) | 과열 시 높아짐 → 청산 위험 → 급락 가능 |
| 코인 미결제약정 (OI) | ccxt (Binance) | 레버리지 과도 축적 시 급변동 |

### 활용 방식

```
뉴스 센티멘트 (Phase 2)
    +
선행 시그널 + 매크로 (Phase 3)
    ↓
종합 점수 (AI가 합산 판단)
    ↓
bullish / bearish / neutral + 확신도
    ↓
텔레그램 알림 (확신도 높을 때만)
```

여러 데이터가 같은 방향을 가리킬 때 (confluence) 확신도가 높아짐.
예: 고래가 BTC 매집 + 뉴스 긍정 + 공포지수 극단적 공포 = 강한 매수 시그널
