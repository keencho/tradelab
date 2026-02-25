# TradeLab — 개인용 AI 트레이딩 리서치 플랫폼

## 개요

- 가상 투자(페이퍼 트레이딩)로 전략 검증
- 실매매는 직접 수동으로
- 로그인 없음, 완전 개인용
- AI 기반 뉴스/센티멘트 분석 + 선행 시그널 탐지
- **전체 무료 (호스팅, AI, 데이터 전부)**

---

## 기술 스택 (전부 무료)

| 구분 | 선택 | 비고 |
|------|------|------|
| 백엔드 | **FastAPI** | API + HTML 서빙, Jinja2 템플릿 |
| 템플릿 | **Jinja2** | FastAPI 내장, 공통 레이아웃 분리 |
| 스타일 | **TailwindCSS CDN** | 빌드 없음, CDN으로 로드 |
| 인터랙션 | **HTMX** | JS 없이 동적 업데이트 |
| 차트 | **Plotly.js CDN** | 캔들차트, 라인차트 |
| 폰트 | **Pretendard** | CDN으로 로드 |
| DB | **PostgreSQL** | VM에 설치, 로컬에서도 접속 가능, SQLAlchemy |
| 스케줄러 | **crontab** | 서버 내장, 데이터 수집/분석/알림 |
| AI (LLM) | **Google Gemini API (Flash)** | 무료: 15 RPM, 하루 1500건 |
| AI (LLM 백업) | **Groq (Llama 3)** | 무료: 30 RPM, 하루 14,400건 |
| ML 예측 | **LightGBM** | 오픈소스, 가볍고 빠름 |
| 백테스트 | **vectorbt** | 오픈소스 |
| 주식 데이터 | **yfinance** | 무료 |
| 코인 데이터 | **ccxt (Binance)** | 무료 |
| 알림 | **python-telegram-bot** | 무료 |

---

## 디자인 시스템

### 컬러 팔레트 (Slate Professional Dark)

| 역할 | Hex | Tailwind |
|------|-----|----------|
| Background (base) | `#0F172A` | `slate-900` |
| Surface (카드/사이드바) | `#1E293B` | `slate-800` |
| Elevated (호버/활성) | `#334155` | `slate-700` |
| Border | `#475569` | `slate-600` |
| Text primary | `#F1F5F9` | `slate-100` |
| Text secondary | `#94A3B8` | `slate-400` |
| Accent | `#38BDF8` | `sky-400` |
| Profit / 상승 | `#10B981` | `emerald-500` |
| Loss / 하락 | `#EF4444` | `red-500` |
| Warning | `#F59E0B` | `amber-500` |

### 폰트

- **Pretendard** (본문, UI 전체)
- 숫자/금액: `font-variant-numeric: tabular-nums` (정렬용)

### 레이아웃

- **사이드바** (왼쪽 고정, w-64)
- **메인 콘텐츠** (flex-1)
- 카드: `bg-slate-800 rounded-xl border border-slate-700 p-6`
- 그리드: `grid grid-cols-3 gap-6`

---

## 서버 구조 (Ubuntu)

모든 것이 하나의 Ubuntu 서버 안에서 돌아감:

```
Ubuntu Server
├── crontab (백그라운드, 24/7 자동)
│   ├── 매 1시간: 가격 수집 → PostgreSQL
│   ├── 매 1시간: 뉴스/소셜 수집 → Gemini 센티멘트 분석 → PostgreSQL
│   ├── 매 1시간: 온체인/공시 수집 → 이상 탐지 → PostgreSQL
│   └── 시그널 발생시: 텔레그램 알림 발송
│
├── FastAPI (대시보드, 항상 켜짐, 포트 5050)
│   ├── PostgreSQL 읽어서 HTML 렌더링
│   ├── HTMX로 동적 업데이트
│   └── 가상매매 API → PostgreSQL에 기록
│
└── PostgreSQL (tradelab DB)
    ├── prices      # 가격 히스토리
    ├── news        # 뉴스 + 센티멘트 점수
    ├── signals     # 탐지된 시그널
    ├── trades      # 가상매매 내역
    └── portfolio   # 포트폴리오 상태
```

---

## 작동 흐름

```
[crontab: 매 1시간 자동 실행]

  ① 데이터 수집
  yfinance/ccxt/NewsAPI/Reddit/Etherscan/EDGAR
        │
        ▼
  ② AI 분석
  Gemini Flash로 센티멘트 분석
  z-score로 이상 탐지
  LightGBM으로 방향 예측
        │
        ▼
  ③ 저장
  전부 PostgreSQL에 기록
        │
        ├──▶ ④ 알림: 시그널 발생시 텔레그램 푸시
        │
        ▼
  ⑤ FastAPI 대시보드 (항상 켜져있음)
  브라우저 접속 → Jinja2로 HTML 렌더링
  HTMX로 부분 업데이트
  가상매매 → POST → DB 기록
```

### cron 스크립트별 역할

```
0 * * * *  python scripts/collect_prices.py    # 매시 정각: 가격 수집
5 * * * *  python scripts/collect_news.py      # 매시 5분: 뉴스 수집 + 센티멘트
10 * * * * python scripts/collect_onchain.py   # 매시 10분: 온체인 데이터
15 * * * * python scripts/run_analysis.py      # 매시 15분: 이상탐지 + ML
16 * * * * python scripts/send_alerts.py       # 매시 16분: 시그널 있으면 알림
```

---

## 프로젝트 구조

```
tradelab/
├── docs/
│   ├── PLAN.md
│   └── SETUP.md
├── .gitignore
├── .env                         # 공통 API 키
├── .env.local                   # 로컬 DB 접속
├── .env.server                  # 서버 DB 접속
├── requirements.txt
├── config.py                    # 환경변수 로드
├── main.py                      # FastAPI 앱 진입점
│
├── routes/                      # FastAPI 라우트
│   ├── __init__.py
│   ├── views.py                 # HTML 페이지 렌더링
│   └── api.py                   # HTMX/JSON API (가상매매 등)
│
├── templates/                   # Jinja2 HTML 템플릿
│   ├── base.html                # 공통 레이아웃 (head, sidebar, CDN)
│   ├── partials/                # 공통 컴포넌트
│   │   ├── sidebar.html         # 사이드바 (전 페이지 공통)
│   │   ├── header.html          # 상단 바
│   │   └── card.html            # 카드 매크로
│   ├── pages/                   # 전체 페이지
│   │   ├── dashboard.html
│   │   ├── portfolio.html
│   │   ├── research.html
│   │   ├── signals.html
│   │   └── news.html
│   └── fragments/               # HTMX 부분 렌더링용
│       ├── signal_list.html
│       ├── news_list.html
│       ├── trade_form.html
│       └── chart.html
│
├── static/                      # 정적 파일
│   ├── css/
│   │   └── app.css              # 커스텀 CSS (최소한)
│   └── js/
│       └── app.js               # 커스텀 JS (최소한)
│
├── db/                          # DB
│   ├── __init__.py
│   ├── database.py              # SQLAlchemy 연결
│   └── models.py                # 테이블 모델
│
├── data/                        # 데이터 수집
│   ├── __init__.py
│   ├── price_collector.py
│   ├── news_collector.py
│   ├── social_collector.py
│   ├── onchain_collector.py
│   └── filing_collector.py
│
├── analysis/                    # AI 분석
│   ├── __init__.py
│   ├── sentiment.py
│   ├── technical.py
│   ├── anomaly.py
│   ├── ml_predictor.py
│   └── report_generator.py
│
├── engine/                      # 가상매매 엔진
│   ├── __init__.py
│   ├── portfolio.py
│   ├── order.py
│   ├── risk.py
│   └── backtest.py
│
├── scripts/                     # cron 스크립트
│   ├── collect_prices.py
│   ├── collect_news.py
│   ├── collect_onchain.py
│   ├── run_analysis.py
│   └── send_alerts.py
│
├── utils/
│   ├── __init__.py
│   ├── telegram_bot.py
│   └── helpers.py
│
└── crontab.txt
```

---

## 페이지별 UI 구성

### 공통 레이아웃

```
┌──────────────────────────────────────────────────┐
│ ┌────────┐ ┌──────────────────────────────────┐  │
│ │        │ │  Header (페이지 제목 + 시간)      │  │
│ │  Side  │ ├──────────────────────────────────┤  │
│ │  bar   │ │                                  │  │
│ │        │ │  Main Content                    │  │
│ │  Logo  │ │                                  │  │
│ │  ────  │ │  (페이지별 내용)                  │  │
│ │  메뉴   │ │                                  │  │
│ │        │ │                                  │  │
│ │        │ │                                  │  │
│ └────────┘ └──────────────────────────────────┘  │
└──────────────────────────────────────────────────┘
```

### 사이드바 메뉴

- Dashboard (홈)
- Portfolio (가상매매)
- Research (종목 리서치)
- Signals (선행 시그널)
- News (뉴스 피드)

### Dashboard 페이지

```
┌─────────────────────────────────────────────┐
│  [총 자산]  [일일 수익]  [시그널]  [뉴스]     │  ← 메트릭 카드 4개
├─────────────────────────────────────────────┤
│                                             │
│  포트폴리오 수익률 차트 (라인)                │  ← 큰 차트
│                                             │
├──────────────────────┬──────────────────────┤
│  최근 시그널 5건      │  최근 뉴스 5건        │  ← 2컬럼
│  🟢 BTC whale...     │  🔴 Fed 금리...       │
│  🔴 NVDA insider..   │  🟢 NVDA 실적...      │
└──────────────────────┴──────────────────────┘
```

### Portfolio 페이지

```
┌─────────────────────────────────────────────┐
│  [총 자산]  [현금]  [투자금]  [수익률]         │
├──────────────────────┬──────────────────────┤
│                      │  매수/매도 폼          │
│  보유 포지션 테이블    │  [종목] [수량]        │
│  종목 | 수량 | 손익   │  [매수] [매도]        │
│                      │                      │
├──────────────────────┴──────────────────────┤
│  거래 히스토리 테이블                         │
└─────────────────────────────────────────────┘
```

### Research 페이지

```
┌─────────────────────────────────────────────┐
│  [종목 검색 입력]  [분석 버튼]                │
├─────────────────────────────────────────────┤
│  [현재가] [시총] [PER] [52주]                │  ← 메트릭
├──────────────────────┬──────────────────────┤
│                      │  기업 정보             │
│  캔들 차트            │  센티멘트 점수         │
│                      │  내부자 매매           │
│                      │  AI 종합 분석          │
└──────────────────────┴──────────────────────┘
```

### Signals 페이지

```
┌─────────────────────────────────────────────┐
│  [필터: 전체/Bullish/Bearish]  [종목 필터]    │
├─────────────────────────────────────────────┤
│  시그널 카드 리스트                           │
│  ┌─────────────────────────────────────┐    │
│  │ 🟢 BTC | whale_alert | 85%         │    │
│  │ 거래소 대량 출금 감지...              │    │
│  │ AI: 과거 유사 패턴에서 72%...        │    │
│  └─────────────────────────────────────┘    │
│  ┌─────────────────────────────────────┐    │
│  │ 🔴 AAPL | insider_trade | 72%      │    │
│  │ CEO 주식 매도 $5M...               │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
```

### News 페이지

```
┌─────────────────────────────────────────────┐
│  [필터: 전체/긍정/부정]  [종목 필터]          │
├─────────────────────────────────────────────┤
│  뉴스 카드 리스트                            │
│  ┌─────────────────────────────────────┐    │
│  │ 🟢 긍정 +0.8 | 영향도 8/10         │    │
│  │ NVDA, AI 서버 수주 급증...           │    │
│  │ 관련: NVDA, AMD | 02/25 13:00      │    │
│  └─────────────────────────────────────┘    │
└─────────────────────────────────────────────┘
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

### 선행 지표

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
- 매수/매도: 현재 시세 기준 즉시 체결 (HTMX POST)
- 수수료 반영: 코인 0.1%, 주식 0.015%
- 포트폴리오 대시보드: 보유 종목, 수익률, 비중
- 수익률 차트: 내 포트폴리오 vs 벤치마크 (KOSPI, S&P500, BTC)
- 거래 히스토리: 전체 매매 내역 + 승률/평균수익 통계

### 2. AI 센티멘트 분석

```
수집 (매 1시간, crontab)
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

---

## 구현 순서

### Phase 1: 기반 + UI 셸
- [ ] 프로젝트 구조 갈아엎기 (Streamlit → FastAPI)
- [ ] requirements.txt 업데이트
- [ ] FastAPI 앱 세팅 (main.py, routes)
- [ ] Jinja2 공통 레이아웃 (base.html, sidebar.html, header.html)
- [ ] TailwindCSS + Pretendard + HTMX CDN 연결
- [ ] 5개 페이지 기본 구조 (mock 데이터로 꽉 채움)
- [ ] 다크 테마 적용
- [ ] DB 모델 유지 (기존 것 재사용)

### Phase 2: 가상매매
- [ ] 포트폴리오 엔진 (매수/매도/P&L)
- [ ] 매매 폼 (HTMX POST)
- [ ] 포트폴리오 페이지 (보유종목, 수익률 차트)
- [ ] 거래 히스토리 테이블

### Phase 3: 뉴스 + 센티멘트
- [ ] 뉴스 수집 파이프라인 (NewsAPI)
- [ ] Gemini API 센티멘트 분석 연동
- [ ] 뉴스 페이지 (센티멘트 점수 + 필터)
- [ ] 종목별 센티멘트 추이 차트

### Phase 4: 선행 시그널
- [ ] 온체인 데이터 수집 (고래 추적)
- [ ] SEC EDGAR / DART 내부자 매매 수집
- [ ] 소셜 버즈 모니터링 (Reddit)
- [ ] 이상 탐지 알고리즘 (z-score)
- [ ] 시그널 페이지 + AI 해석

### Phase 5: AI 리서치 + 마무리
- [ ] 종목 검색 → 자동 데이터 수집
- [ ] Gemini 종합 리포트 생성
- [ ] 기술적 분석 차트 (Plotly.js)
- [ ] 텔레그램 알림 연동
- [ ] crontab 설정

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
- 코드 퀄리티 신경 씀 (읽기 좋게, 구조 깔끔하게)
- 로그인/인증 없음
- 실매매 연동 없음 (가상매매 only)
- 서버: Ubuntu (배포는 별도 진행)
- LLM: Gemini Flash 메인, Groq (Llama 3) 백업
- 백그라운드: crontab으로 수집/분석/알림 자동화
- UI: FastAPI + Jinja2 + TailwindCSS + HTMX (React 없음, npm 없음)
- 폰트: Pretendard
- 레이아웃: 사이드바 (왼쪽 고정)
- 테마: 다크 (Slate Professional)
