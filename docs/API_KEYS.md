# TradeLab API 키 관리

## Phase 2: 뉴스 + 센티멘트

### Finnhub (미국 주식/코인 뉴스)
- 가입: https://finnhub.io/register
- 무료 한도: 60 req/분
- 키 발급: 가입 즉시 대시보드에 표시
- `.env` 키명: `FINNHUB_API_KEY`

### Google Gemini (LLM 센티멘트 분석 — 메인)
- 가입: https://aistudio.google.com/apikey
- 무료 한도: 15 RPM, 1,000 RPD (Flash-Lite)
- 카드 등록 안 하면 절대 과금 없음
- `.env` 키명: `GEMINI_API_KEY`

### Groq (LLM 백업)
- 가입: https://console.groq.com
- 무료 한도: 30 RPM, 14,400 RPD (Llama 3.1 8B)
- `.env` 키명: `GROQ_API_KEY`

### Cerebras (LLM 백업2) — 선택사항
- 가입: https://cloud.cerebras.ai
- 무료 한도: 30 RPM, 14,400 RPD
- `.env` 키명: `CEREBRAS_API_KEY`

### RSS — 키 불필요
- 한경, 파이낸셜뉴스, CoinDesk, CoinTelegraph 등
- 가입/키 없이 바로 사용

---

## Phase 3: 선행 시그널 + 매크로

### Etherscan (온체인 고래 추적)
- 가입: https://etherscan.io/register
- 키 페이지: https://etherscan.io/myapikey
- 무료 한도: 5 req/초
- `.env` 키명: `ETHERSCAN_API_KEY`

### FRED API (미국 매크로 — CPI, 금리, 고용)
- 가입: https://fred.stlouisfed.org/docs/api/api_key.html
- 무료 한도: 120 req/분
- `.env` 키명: `FRED_API_KEY`

### DART OpenAPI (한국 공시)
- 가입: https://opendart.fss.or.kr
- 무료 한도: 무제한
- `.env` 키명: `DART_API_KEY`

### 한국은행 ECOS API (한국 매크로 — 기준금리, CPI, 실업률)
- 가입: https://ecos.bok.or.kr/api/#/
- 무료 한도: 100,000 req/일
- 키 발급: 회원가입 후 인증키 신청 (즉시 발급)
- `.env` 키명: `ECOS_API_KEY`

### 키 불필요 서비스

| 서비스 | 용도 | 접근 방식 |
|--------|------|-----------|
| **pykrx** | 외국인/기관 순매수, 공매도 잔고, 프로그램 매매 | `pip install pykrx`, KRX 공개 데이터 |
| **ccxt (Binance)** | 펀딩레이트, OI (미결제약정) | `pip install ccxt`, 공개 마켓 데이터 |
| **alternative.me** | 코인 공포/탐욕 지수 | `https://api.alternative.me/fng/` |
| **CNN Fear & Greed** | 미국주식 공포/탐욕 지수 | CNN 공개 API |
| **Reddit .json** | 소셜 버즈 (r/wallstreetbets 등) | URL 뒤에 `.json` 붙이면 됨 (10 req/분) |
| **네이버 종토방** | 한국주식 소셜 버즈 | 스크래핑 (차단 리스크 있음, 우선순위 낮음) |
| **SEC EDGAR** | 미국 내부자 매매 (Form 4), 대량보유 (13F) | 공개 API, User-Agent 헤더만 필요 |
| **RSS 피드** | 뉴스 수집 | 가입/키 없이 바로 사용 |

---

## .env 예시

```env
# Phase 2: 뉴스
FINNHUB_API_KEY=
GEMINI_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=

# Phase 3: 시그널
ETHERSCAN_API_KEY=
FRED_API_KEY=
DART_API_KEY=
ECOS_API_KEY=
```

---

## 우선순위

Phase 2 시작하려면 최소 이것만 있으면 됨:
1. **Finnhub** — 미국/코인 뉴스 수집
2. **Gemini** — 센티멘트 분석

Phase 3 시작하려면 추가로:
3. **DART** — 한국 공시/내부자 매매
4. **ECOS** — 한국 매크로 지표

나머지는 나중에 발급해도 됨 (RSS, pykrx, ccxt, Reddit 등은 키 불필요).
