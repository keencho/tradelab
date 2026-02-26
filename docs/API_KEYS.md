# TradeLab API 키 관리

## Phase 2: 뉴스 + 센티멘트

### Finnhub (미국 주식/코인 뉴스)
- 가입: https://finnhub.io/register
- 무료 한도: 60 req/분
- 키 발급: 가입 즉시 대시보드에 표시
- `.env` 키명: `FINNHUB_API_KEY`

### CryptoPanic (코인 뉴스 + 센티멘트)
- 가입: https://cryptopanic.com/ (회원가입 후)
- 키 페이지: https://cryptopanic.com/developers/api/keys
- 무료 한도: ~100 req/일
- `.env` 키명: `CRYPTOPANIC_API_KEY`

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

### 키 불필요 서비스
- **alternative.me** — 코인 공포/탐욕 지수 (공개 API)
- **Reddit .json** — 서브레딧 URL 뒤에 .json 붙이면 됨
- **ccxt (Binance)** — 공개 마켓 데이터는 키 불필요
- **RSS 피드** — 전부 공개

---

## .env 예시

```env
# Phase 2: 뉴스
FINNHUB_API_KEY=
CRYPTOPANIC_API_KEY=
GEMINI_API_KEY=
GROQ_API_KEY=
CEREBRAS_API_KEY=

# Phase 3: 시그널
ETHERSCAN_API_KEY=
FRED_API_KEY=
DART_API_KEY=
```

---

## 우선순위

Phase 2 시작하려면 최소 이것만 있으면 됨:
1. **Finnhub** — 미국/코인 뉴스 수집
2. **Gemini** — 센티멘트 분석

나머지는 나중에 발급해도 됨 (RSS는 키 없이 바로 동작).
