# TradeLab — 개인용 AI 트레이딩 리서치 플랫폼

## 개요

- 가상 투자(페이퍼 트레이딩)로 전략 검증
- 실매매는 직접 수동으로
- 로그인 없음, 완전 개인용
- AI 기반 뉴스/센티멘트 분석 + 선행 시그널 탐지
- **전체 무료 (호스팅, AI, 데이터 전부)**

---

## 기술 스택 (전부 무료)

| 구분 | 선택 | 무료 한도 / 이유 |
|------|------|-------------------|
| 프론트+백엔드 | **Streamlit** | 오픈소스, Python만으로 완성 |
| 호스팅 | **Streamlit Community Cloud** | 무료, 슬립 모드 있지만 접속시 자동 기동 |
| 스케줄 작업 | **GitHub Actions** | 월 2000분 무료 (데이터 수집용) |
| DB | **SQLite** | 파일 하나, 설치 불필요, 가벼움 |
| AI (LLM) | **Google Gemini API (Flash)** | 무료: 15 RPM, 하루 1500건 |
| AI (LLM 백업) | **Groq (Llama 3)** | 무료: 30 RPM, 하루 14,400건 |
| ML 예측 | **LightGBM** | 오픈소스, 가볍고 빠름 (무료 호스팅에서도 OK) |
| 백테스트 | **vectorbt** | 오픈소스 |
| 주식 데이터 | **yfinance** | 무료 |
| 코인 데이터 | **ccxt (Binance)** | 무료 |
| 알림 | **python-telegram-bot** | 무료 |

### 호스팅 구조

```
Streamlit Community Cloud  → 대시보드 (무료, 접속시 자동 기동)
GitHub Actions (cron)      → 매 시간 데이터 수집 스크립트 실행 (무료)
GitHub repo 내 SQLite      → DB 파일 (무료)
Google Gemini API (Flash)  → 센티멘트 분석 / 리포트 (무료)
```

---

## 아키텍처

```
┌─────────────────────────────────────────────────┐
│            Streamlit 대시보드 (Cloud)             │
│  [메인] [포트폴리오] [리서치] [시그널] [뉴스]      │
├─────────────────────────────────────────────────┤
│               Python 백엔드                      │
│                                                  │
│  데이터 수집 (GitHub Actions 스케줄)              │
│  ├── 가격 데이터 (yfinance, ccxt)                │
│  ├── 뉴스/소셜 (NewsAPI, Reddit)                 │
│  ├── 온체인 (Whale Alert, Etherscan)             │
│  └── 공시/재무 (SEC EDGAR, DART)                 │
│                                                  │
│  AI 분석 레이어                                   │
│  ├── LLM 센티멘트 (Gemini Flash / Groq)          │
│  ├── ML 방향 예측 (LightGBM)                     │
│  ├── 이상 탐지 (z-score 기반)                    │
│  └── 종합 리포트 생성                             │
│                                                  │
│  가상매매 엔진                                    │
│  ├── 주문 관리                                   │
│  ├── 포지션 추적                                 │
│  ├── P&L 계산                                    │
│  └── 리스크 관리                                 │
├─────────────────────────────────────────────────┤
│               SQLite DB                          │
│  prices / trades / portfolio / news / signals    │
└─────────────────────────────────────────────────┘
```

---

## 프로젝트 구조

```
tradelab/
├── docs/
│   └── PLAN.md
├── .gitignore
├── requirements.txt
├── app.py                   # Streamlit 메인 진입점
├── config.py                # API 키 설정 (.env에서 로드)
├── .env                     # API 키 (gitignore됨)
│
├── data/                    # 데이터 수집
│   ├── price_collector.py   # 주식/코인 가격 수집
│   ├── news_collector.py    # 뉴스 수집
│   ├── social_collector.py  # 소셜 미디어 수집
│   ├── onchain_collector.py # 온체인 데이터 수집
│   └── filing_collector.py  # 공시/재무 수집
│
├── analysis/                # AI 분석
│   ├── sentiment.py         # Gemini 센티멘트 분석
│   ├── technical.py         # 기술적 분석 (RSI, MACD 등)
│   ├── anomaly.py           # 이상 탐지
│   ├── ml_predictor.py      # LightGBM 방향 예측
│   └── report_generator.py  # AI 종합 리포트
│
├── engine/                  # 가상매매 엔진
│   ├── portfolio.py         # 포트폴리오 관리
│   ├── order.py             # 주문 처리
│   ├── risk.py              # 리스크 관리
│   └── backtest.py          # 백테스트
│
├── ui/                      # Streamlit 페이지
│   ├── page_main.py         # 메인 대시보드
│   ├── page_portfolio.py    # 포트폴리오 화면
│   ├── page_research.py     # 종목 리서치
│   ├── page_signals.py      # 시그널 보드
│   └── page_news.py         # 뉴스 피드
│
├── db/
│   ├── database.py          # DB 연결/초기화
│   └── models.py            # 테이블 스키마
│
├── utils/
│   ├── telegram_bot.py      # 텔레그램 알림
│   └── helpers.py           # 공통 유틸
│
└── .github/
    └── workflows/
        └── collect_data.yml # GitHub Actions 데이터 수집 cron
```

---

## 데이터 소스 (전부 무료)

### 가격 데이터
- **미국 주식**: yfinance (무료, 약간 딜레이)
- **한국 주식**: pykrx (무료)
- **코인**: ccxt → Binance (무료, 실시간)

### 뉴스 / 센티멘트
- **뉴스**: NewsAPI (무료 100건/일)
- **소셜**: Reddit API (무료), Twitter/X API (무료 제한적)
- **Fear & Greed Index**: alternative.me API (무료)

### 선행 지표 ("뉴스보다 빠른" 데이터)

| 소스 | 감지 대상 | 비용 |
|------|-----------|------|
| SEC EDGAR | 내부자 매매 (Form 4), 대량보유 (13F) | 무료 |
| DART | 한국 기업 공시 | 무료 |
| Etherscan | 온체인 트랜잭션, 고래 지갑 | 무료 (5건/초) |
| Whale Alert | 거래소 대량 입출금 | 무료 플랜 |
| GitHub API | 크립토 프로젝트 개발 활성도 | 무료 (5000건/시간) |
| Reddit | 소셜 버즈, 종목 언급량 | 무료 |

### 재무 데이터
- **미국**: yfinance (무료)
- **한국**: DART OpenAPI (무료)

---

## 핵심 기능 상세

### 1. 가상매매 (페이퍼 트레이딩)

- 시작 자본 설정 (기본값: 1억원)
- 매수/매도: 현재 시세 기준 즉시 체결
- 수수료 반영: 코인 0.1%, 주식 0.015%
- 포트폴리오 대시보드: 보유 종목, 수익률, 비중
- 수익률 차트: 내 포트폴리오 vs 벤치마크 (KOSPI, S&P500, BTC)
- 거래 히스토리: 전체 매매 내역 + 승률/평균수익 통계

### 2. AI 센티멘트 분석

```
수집 (매 10분~1시간, GitHub Actions)
  → 뉴스 + 소셜 텍스트

분석 (Gemini Flash API)
  → 각 텍스트에 대해:
     - 긍정/부정/중립 분류
     - 영향도 (1~10)
     - 관련 종목 태깅
     - 핵심 요약 1줄

집계
  → 종목별 센티멘트 점수 (시간대별 추이)
  → 전체 시장 센티멘트 지수
```

### 3. 선행 시그널 탐지

```
수집 (스케줄 기반)
  → 온체인, 내부자 매매, 소셜 버즈

이상 탐지 (z-score)
  → 최근 30일 평균 대비 현재값의 표준편차
  → z > 2.0 이면 "이상 시그널" 발생

AI 해석 (Gemini)
  → 시그널 컨텍스트를 Gemini에게 전달
  → "왜 이 데이터가 비정상인지"
  → "과거 유사 패턴에서 어떤 결과가 있었는지"
  → 종합 판단 (매수/매도/관망 + 확신도)
```

### 4. 종목 리서치

종목 검색 시 자동으로 수집/분석:
- 기본 정보 (시가총액, 섹터, PER 등)
- 최근 실적 + AI 해석
- 기술적 분석 (RSI, MACD, 볼린저밴드, 이동평균)
- 최근 뉴스 + 센티멘트
- 내부자 매매 동향
- 소셜 버즈 추이
- (코인) 온체인 지표
- Gemini가 위 데이터 종합하여 리포트 생성

### 5. 대시보드 화면

**메인**: 시장 개요, 오늘의 시그널, 포트폴리오 요약
**포트폴리오**: 가상 보유종목, P&L, 매매 UI
**리서치**: 종목 검색 → AI 종합 분석
**시그널**: 선행 시그널 목록 + 이력
**뉴스**: 실시간 뉴스 피드 + 센티멘트 표시

---

## 구현 순서

### Phase 1: 기반 세팅
- [ ] 프로젝트 폴더/파일 구조 생성
- [ ] requirements.txt + 가상환경
- [ ] SQLite 스키마 설계 + 초기화
- [ ] config.py + .env (API 키)
- [ ] 가격 데이터 수집 (yfinance + ccxt)
- [ ] Streamlit 기본 레이아웃 (탭 구조)

### Phase 2: 가상매매
- [ ] 포트폴리오 엔진 (매수/매도/P&L)
- [ ] 매매 UI (종목 선택, 수량, 매수/매도 버튼)
- [ ] 포트폴리오 대시보드 (보유종목, 수익률 차트)
- [ ] 거래 히스토리 + 통계

### Phase 3: 뉴스 + 센티멘트
- [ ] 뉴스 수집 파이프라인 (NewsAPI)
- [ ] Gemini API 센티멘트 분석 연동
- [ ] 뉴스 피드 UI (센티멘트 점수 표시)
- [ ] 종목별 센티멘트 추이 차트

### Phase 4: 선행 시그널
- [ ] 온체인 데이터 수집 (고래 추적)
- [ ] SEC EDGAR / DART 내부자 매매 수집
- [ ] 소셜 버즈 모니터링 (Reddit)
- [ ] 이상 탐지 알고리즘 (z-score)
- [ ] 시그널 보드 UI + AI 해석

### Phase 5: AI 리서치 + 마무리
- [ ] 종목 검색 → 자동 데이터 수집
- [ ] Gemini 종합 리포트 생성
- [ ] 기술적 분석 차트 (plotly)
- [ ] 텔레그램 알림 연동
- [ ] GitHub Actions 데이터 수집 cron 세팅
- [ ] Streamlit Cloud 배포

---

## 필요한 API 키 목록 (전부 무료)

| 서비스 | 용도 | 무료 한도 |
|--------|------|-----------|
| Google Gemini API | 센티멘트, 리포트 | 15 RPM, 1500건/일 |
| NewsAPI | 뉴스 수집 | 100건/일 |
| Reddit API | 소셜 센티멘트 | 무제한 (레이트리밋 있음) |
| Etherscan | 온체인 데이터 | 5건/초 |
| Whale Alert | 고래 추적 | 기본 무료 |
| DART OpenAPI | 한국 공시 | 무제한 |
| GitHub API | 크립토 프로젝트 활동 | 5000건/시간 |
| Telegram Bot | 알림 | 무제한 |

---

## 메모

- 비용: **완전 무료** (모든 서비스 무료 티어 사용)
- 코드 퀄리티 신경 안 씀, 동작 우선
- 로그인/인증 없음
- 실매매 연동 없음 (가상매매 only)
- LLM: Gemini Flash 메인, Groq (Llama 3) 백업
- LightGBM: 가벼워서 무료 호스팅에서도 문제 없음
