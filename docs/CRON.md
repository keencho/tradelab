# TradeLab — Cron 스케줄 관리

## 현재 운영 중

| 주기 | 스크립트 | 용도 | 상태 |
|------|----------|------|------|
| 매 30분 | `scripts/collect_news.py` | 뉴스 수집 + LLM 센티멘트 분석 | ✅ 운영 중 |

### 현재 crontab (서버)

```cron
TRADELAB_ENV=server

*/30 * * * * cd ~/tradelab && venv/bin/python scripts/collect_news.py >> /dev/null 2>&1
```

---

## Phase 3 추가 예정

### 시그널 수집 (5분 주기)

| 주기 | 스크립트 | 수집 대상 | API |
|------|----------|-----------|-----|
| 5분 | `scripts/collect_signals.py` | 온체인 고래 (ETH) | Etherscan |
| | | 펀딩레이트 / OI | ccxt (Binance) |
| | | 공포/탐욕 지수 | alternative.me + CNN |
| | | 외국인/기관 순매수 | pykrx (장중만) |
| | | 공매도 잔고 | pykrx (장중만) |
| | | 프로그램 매매 | pykrx (장중만) |
| | | SEC EDGAR 내부자 매매 | SEC (키 불필요) |
| | | DART 공시/내부자 매매 | DART API |
| | | 소셜 버즈 (Reddit) | Reddit .json |

### 네이버 종토방 (15분 주기, 별도 분리)

| 주기 | 스크립트 | 수집 대상 | 비고 |
|------|----------|-----------|------|
| 15분 | `scripts/collect_naver_buzz.py` | 네이버 종토방 버즈 | 스크래핑, 차단 방지용 간격 |

### 매크로 지표 (1시간 주기)

| 주기 | 스크립트 | 수집 대상 | API |
|------|----------|-----------|-----|
| 1시간 | `scripts/collect_macro.py` | 미국 CPI, 금리, 고용 | FRED |
| | | 한국 기준금리, CPI, 실업률 | ECOS |

> 매크로 데이터는 업데이트 자체가 느림 (월 1회 수준). 1시간이면 충분.

### 이상 탐지 + 알림 (5분 주기, 시그널 수집 직후)

| 주기 | 스크립트 | 용도 |
|------|----------|------|
| 5분 | `scripts/detect_anomaly.py` | z-score 이상 탐지 + LLM 해석 + 텔레그램 알림 |

---

## 최종 crontab 목표

```cron
TRADELAB_ENV=server

# ── Phase 2: 뉴스 ──────────────────────────────
*/30 * * * *  cd ~/tradelab && venv/bin/python scripts/collect_news.py >> /dev/null 2>&1

# ── Phase 3: 시그널 (5분) ──────────────────────
*/5  * * * *  cd ~/tradelab && venv/bin/python scripts/collect_signals.py >> /dev/null 2>&1

# ── Phase 3: 네이버 종토방 (15분, 차단 방지) ───
*/15 * * * *  cd ~/tradelab && venv/bin/python scripts/collect_naver_buzz.py >> /dev/null 2>&1

# ── Phase 3: 매크로 (1시간) ────────────────────
0    * * * *  cd ~/tradelab && venv/bin/python scripts/collect_macro.py >> /dev/null 2>&1

# ── Phase 3: 이상 탐지 + 알림 (5분, 시그널 직후) ─
*/5  * * * *  cd ~/tradelab && sleep 30 && venv/bin/python scripts/detect_anomaly.py >> /dev/null 2>&1
```

> `detect_anomaly.py`는 `sleep 30`으로 시그널 수집 완료 후 실행되게 함.

---

## API 리밋 검증 (5분 주기 기준)

| API | 한도 | 5분 주기 예상 | 여유 |
|-----|------|--------------|------|
| Etherscan | 5 req/초 | ~1,500/일 | ✅ |
| FRED | 120 req/분 | ~300/일 | ✅ |
| DART | 40,000/일 | ~1,500/일 | ✅ |
| ECOS | 100,000/일 | ~300/일 | ✅ |
| Finnhub | 60 req/분 | 30분 유지 | ✅ |
| pykrx | 제한 없음 | 장중만 | ✅ |
| ccxt Binance | 1,200 req/분 | ~600/일 | ✅ |
| alternative.me | 제한 없음 | ~300/일 | ✅ |
| Reddit .json | 10 req/분 | 1~2/5분 | ⚠️ 서브레딧 3개 이내 |
| 네이버 종토방 | 비공식 | 15분 간격 | ⚠️ 차단 주의 |
| LLM (해석) | Gemini 1,000/일 | 이상 감지 시만 | ✅ |

---

## 참고

- **장중 시간 (KST)**
  - 한국 주식
    - 장전 동시호가: 08:30 ~ 09:00
    - 정규장: 09:00 ~ 15:30
    - 장후 동시호가: 15:30 ~ 15:40
    - 시간외 종가: 15:40 ~ 16:00
    - 시간외 단일가: 16:00 ~ 18:00
    - (2026.06~) 프리마켓: 07:00 ~ 08:00
    - (2026.06~) 애프터마켓: 16:00 ~ 20:00
  - 미국 주식 (KST 기준)
    - 정규장: 23:30 ~ 06:00 (서머타임 22:30 ~ 05:00)
    - 프리마켓: 18:00 ~ 23:30 (서머타임 17:00 ~ 22:30)
    - 애프터마켓: 06:00 ~ 10:00 (서머타임 05:00 ~ 09:00)
  - 코인: 24시간
- pykrx 수급 데이터는 장중에만 의미 있음 → 스크립트 내부에서 시간 체크
- 서버 수동 실행: `TRADELAB_ENV=server venv/bin/python scripts/collect_signals.py`
