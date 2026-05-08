# TradeLab

개인용 자산 관리 + 시그널 분석 도구. FastAPI + PostgreSQL.

## 구성

- **웹** (`/my`): 실거래 자산, 보유/청산 포지션, 누적 손익
- **시그널**: z-score 이상 탐지, NXT/KRX 실시간 가격 폴링, 텔레그램 알림
- **위젯** (`desktop/`): Tauri 데스크탑 위젯 — 관심종목/자산 두 모드

## 셋업

```bash
cp .env.example .env  # 키 채우기
pip install -r requirements.txt
uvicorn main:app --reload
```

## 환경

- DB: PostgreSQL (`DATABASE_URL`)
- 시세/뉴스: Naver, Finnhub, ccxt
- LLM: Gemini / Groq / Cerebras
- 매크로: FRED, ECOS, DART
- 알림: Telegram Bot

자세한 키 발급은 [docs/API_KEYS.md](docs/API_KEYS.md).
