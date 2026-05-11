"""테마 AI 브리핑 일일 생성 + 텔레그램 푸시.

cron 예: 0 16 * * 1-5 cd ~/tradelab && venv/bin/python scripts/generate_theme_briefs.py

장 마감 후 (16:00 KST) 1회 실행.

- 절대값 등락률 상위 N개 업종 선정
- 각 업종에 대해 AI 브리핑 생성 (캐시 -> 새로 생성)
- 텔레그램으로 묶어서 1통 전송
"""

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime

from config import KST, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger
from services.themes import fetch_sectors, fetch_sector_stocks
from services.theme_brief import generate_brief

logger = get_logger("theme_briefs_cron")

TOP_N = 3            # 상위 몇 개 업종에 대해 브리핑 생성
MIN_ABS_CHANGE = 1.5  # 이 정도 안 움직였으면 무시 (잡음 방지)


def _send_telegram(message: str) -> None:
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        logger.warning("TELEGRAM_BOT_TOKEN/CHAT_ID 미설정 — 푸시 스킵")
        return
    try:
        from urllib.request import urlopen, Request as UrlRequest
        from urllib.parse import urlencode
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urlencode({
            "chat_id": TELEGRAM_CHAT_ID,
            "text": message,
            "disable_web_page_preview": "true",
        }).encode()
        req = UrlRequest(url, data=data, method="POST")
        urlopen(req, timeout=10)
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")


def main() -> int:
    sectors = fetch_sectors(force=True)
    if not sectors:
        logger.error("업종 데이터 없음")
        return 1

    # 절대값 변동 순 정렬 → 상위 N개
    sectors.sort(key=lambda s: abs(s.change_pct), reverse=True)
    top = [s for s in sectors if abs(s.change_pct) >= MIN_ABS_CHANGE][:TOP_N]
    if not top:
        logger.info("주목할 만한 움직임 없음")
        return 0

    logger.info(f"상위 {len(top)}개 업종 브리핑 생성 시작")

    today = datetime.now(KST).strftime("%Y-%m-%d")
    blocks: list[str] = [f"📊 오늘의 테마 ({today})", ""]

    for s in top:
        try:
            stocks = fetch_sector_stocks(s.no, force=True)
            stocks.sort(key=lambda x: x.change_pct, reverse=True)
            top_for_llm = [
                {"code": x.code, "name": x.name, "change_pct": x.change_pct}
                for x in stocks[:8]
            ]
            brief = generate_brief(s.no, s.name, top_for_llm, force=True)
            if not brief:
                logger.warning(f"브리핑 실패: {s.name}")
                continue

            arrow = "🔴" if s.change_pct > 0 else "🔵"
            blocks.append(f"{arrow} {s.name} ({s.change_pct:+.2f}%)")
            blocks.append(f"   • {brief.headline}")
            if brief.risks:
                blocks.append(f"   ⚠ {brief.risks}")
            if brief.top_stocks:
                blocks.append(f"   📌 {' · '.join(brief.top_stocks[:3])}")
            blocks.append("")
        except Exception as e:
            logger.error(f"브리핑 실패 {s.name}: {e}")
            continue

    msg = "\n".join(blocks).strip()
    if len(msg.splitlines()) <= 2:
        logger.info("전송할 내용 없음")
        return 0

    _send_telegram(msg)
    logger.info(f"테마 브리핑 푸시 완료 — {len(top)}개 업종")
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
