"""테마/업종 분석 (AI 없음) — Naver Finance 업종 페이지 스크래핑.

- fetch_sectors(): 전체 업종 + 등락률 + 종목수 (상승/하락)
- fetch_sector_stocks(no): 해당 업종 종목 + 가격/거래량/등락률

캐시 정책: 10분 TTL — 장중에도 적당히 갱신.
"""

import re
import time
from dataclasses import dataclass, field

import httpx

from config import get_logger

logger = get_logger("themes")

_NAVER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "Accept-Language": "ko-KR,ko;q=0.9",
}

CACHE_TTL = 600  # 10분


@dataclass
class Sector:
    no: str
    name: str
    change_pct: float
    total: int       # 전체 종목수
    up: int          # 상승
    flat: int        # 보합
    down: int        # 하락


@dataclass
class SectorStock:
    code: str
    name: str
    price: float
    change_pct: float
    volume: int      # 거래량
    value: int       # 거래대금


# ── 캐시 ────────────────────────────────────────────────

_sectors_cache: tuple[float, list[Sector]] | None = None
_sector_stocks_cache: dict[str, tuple[float, list[SectorStock]]] = {}


# ── 파서 ────────────────────────────────────────────────

_SECTOR_ROW_RE = re.compile(
    r"<a href=\"/sise/sise_group_detail\.naver\?type=upjong&no=(\d+)\">([^<]+)</a>.*?"
    r"<span class=\"tah p11 (red01|nv01)\">\s*([+\-\d.]+)%\s*</span>.*?"
    r"<td class=\"number\">(\d+)</td>\s*"
    r"<td class=\"number\">(\d+)</td>\s*"
    r"<td class=\"number\">(\d+)</td>\s*"
    r"<td class=\"number\">(\d+)</td>",
    re.DOTALL,
)


def _parse_sectors(html: str) -> list[Sector]:
    out: list[Sector] = []
    for m in _SECTOR_ROW_RE.finditer(html):
        no, name, _color, pct_s, total, up, flat, down = m.groups()
        try:
            pct = float(pct_s)
        except ValueError:
            pct = 0.0
        out.append(Sector(
            no=no, name=name.strip(), change_pct=pct,
            total=int(total), up=int(up), flat=int(flat), down=int(down),
        ))
    return out


# 종목 행 — 6자리 종목코드 기준으로 앵커 찾고 그 후 td 들에서 숫자 추출
_STOCK_ANCHOR_RE = re.compile(
    r"<a href=\"/item/main\.naver\?code=(\d{6})\">([^<]+)</a>.*?"
    r"<td class=\"number\"[^>]*>([\d,]+)</td>.*?"          # 현재가
    r"<span class=\"tah p11 (red01|nv01)\">\s*([+\-\d.]+)%\s*</span>",
    re.DOTALL,
)

# 거래량/거래대금은 행 안에서 등락률 이후 td 들 — 별도로 추출
_NUM_TD_RE = re.compile(r'<td class="number"[^>]*>([\d,]+)</td>')


def _parse_sector_stocks(html: str) -> list[SectorStock]:
    """행 단위로 분해 후 각각 추출."""
    out: list[SectorStock] = []
    # 종목명 앵커 위치 기준으로 행 슬라이스
    anchors = list(re.finditer(r"<a href=\"/item/main\.naver\?code=(\d{6})\">([^<]+)</a>", html))
    for i, m in enumerate(anchors):
        code, name = m.group(1), m.group(2).strip()
        start = m.end()
        end = anchors[i + 1].start() if i + 1 < len(anchors) else min(start + 4000, len(html))
        chunk = html[start:end]

        pct_m = re.search(r'<span class="tah p11 (?:red01|nv01)">\s*([+\-\d.]+)%\s*</span>', chunk)
        if not pct_m:
            continue
        try:
            pct = float(pct_m.group(1))
        except ValueError:
            pct = 0.0

        # 행의 number td 순서 (전일비/등락률은 span 안이라 _NUM_TD_RE 가 매치 안 함):
        #   nums[0]=현재가, nums[1]=매수호가, nums[2]=매도호가,
        #   nums[3]=거래량, nums[4]=거래대금(백만원), nums[5]=전일거래량
        nums = [t.replace(",", "") for t in _NUM_TD_RE.findall(chunk)]
        try:
            price = float(nums[0]) if nums else 0.0
        except (ValueError, IndexError):
            price = 0.0
        volume = 0
        value = 0
        if len(nums) >= 5:
            try:
                volume = int(nums[3])
                value = int(nums[4])  # 백만원 단위
            except ValueError:
                pass

        out.append(SectorStock(
            code=code, name=name, price=price, change_pct=pct,
            volume=volume, value=value,
        ))
    return out


# ── Fetch ────────────────────────────────────────────────


def _decode(content: bytes) -> str:
    try:
        return content.decode("euc-kr")
    except Exception:
        return content.decode("utf-8", errors="replace")


def fetch_sectors(force: bool = False) -> list[Sector]:
    global _sectors_cache
    now = time.time()
    if not force and _sectors_cache and now - _sectors_cache[0] < CACHE_TTL:
        return _sectors_cache[1]

    try:
        r = httpx.get(
            "https://finance.naver.com/sise/sise_group.naver?type=upjong",
            headers=_NAVER_HEADERS, timeout=15,
        )
        r.raise_for_status()
        sectors = _parse_sectors(_decode(r.content))
        _sectors_cache = (now, sectors)
        logger.info(f"themes: fetched {len(sectors)} sectors")
        return sectors
    except Exception as e:
        logger.error(f"themes fetch_sectors: {e}")
        if _sectors_cache:
            return _sectors_cache[1]
        return []


def fetch_sector_stocks(no: str, force: bool = False) -> list[SectorStock]:
    now = time.time()
    cached = _sector_stocks_cache.get(no)
    if not force and cached and now - cached[0] < CACHE_TTL:
        return cached[1]

    try:
        r = httpx.get(
            f"https://finance.naver.com/sise/sise_group_detail.naver?type=upjong&no={no}",
            headers=_NAVER_HEADERS, timeout=15,
        )
        r.raise_for_status()
        stocks = _parse_sector_stocks(_decode(r.content))
        _sector_stocks_cache[no] = (now, stocks)
        return stocks
    except Exception as e:
        logger.error(f"themes fetch_sector_stocks({no}): {e}")
        if cached:
            return cached[1]
        return []


def get_sector_by_no(no: str) -> Sector | None:
    for s in fetch_sectors():
        if s.no == no:
            return s
    return None
