"""서버 헬스 체크 + 이상시 텔레그램 알림.

cron: */5 * * * * flock -n /tmp/health.lock -c "cd ~/tradelab && venv/bin/python scripts/health_check.py"

체크 항목:
1. 사용 가능 메모리 < 2GB
2. python 프로세스 개수 > 8 (크론 누적)
3. /tmp/*.lock 수명 > 30분 (stuck cron)
4. http://127.0.0.1:5050/healthz 5초 timeout
5. systemctl is-active postgresql / cron / tradelab
6. dmesg OOM 이벤트 최근 30분
7. signals 테이블 최근 insert > 30분 전

알림 dedup: /tmp/health_<key>.mark mtime 기반, 같은 경고 30분 쿨다운
"""

import os
import subprocess
import sys
import time
from pathlib import Path
from urllib.request import urlopen, Request as UrlRequest
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent.parent))

from datetime import datetime, timedelta
from config import KST, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, get_logger

logger = get_logger("health_check")

DEDUP_DIR = Path("/tmp")
DEDUP_COOLDOWN_SEC = 30 * 60  # 30분


def _send(msg: str):
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
        data = urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": msg}).encode()
        req = UrlRequest(url, data=data, method="POST")
        urlopen(req, timeout=5)
    except Exception as e:
        logger.error(f"텔레그램 전송 실패: {e}")


def _alert(key: str, msg: str):
    """dedup — 같은 key는 30분에 1번만."""
    marker = DEDUP_DIR / f"health_{key}.mark"
    now = time.time()
    if marker.exists() and (now - marker.stat().st_mtime) < DEDUP_COOLDOWN_SEC:
        logger.info(f"[dedup skip] {key}")
        return
    marker.touch()
    full = f"[서버 헬스] {msg}"
    logger.warning(full)
    _send(full)


def _run(cmd: list[str], timeout: int = 5) -> str:
    try:
        return subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout
        ).stdout.strip()
    except Exception:
        return ""


# ── 체크 함수들 ─────────────────────────────────────

def check_memory():
    out = _run(["free", "-m"])
    for line in out.splitlines():
        if line.startswith("Mem:"):
            parts = line.split()
            available_mb = int(parts[6])
            if available_mb < 2048:
                _alert("mem", f"메모리 부족: available {available_mb}MB (< 2GB)")
            return


def check_python_procs():
    # ps -eo comm= 으로 실행파일명만 가져와서 python만 카운트 (sh/flock wrapper 제외)
    out = _run(["ps", "-eo", "comm="])
    count = sum(1 for line in out.splitlines() if line.strip() == "python")
    if count > 8:
        _alert("pyprocs", f"python 인터프리터 누적: {count}개 (> 8)")


def check_stuck_locks():
    """실제로 누가 락을 쥐고 있는 경우만 감지. mtime은 의미 없음(파일 생성 시각일 뿐)."""
    stuck = []
    for p in Path("/tmp").glob("*.lock"):
        # flock -n 시도 — 못 얻으면 누가 쥐고 있는 중
        r = subprocess.run(
            ["flock", "-n", str(p), "-c", "true"],
            capture_output=True, timeout=3,
        )
        if r.returncode != 0:
            # 누가 쥐고 있는지 찾기 (mtime을 오래된 락의 지속 시간 추정에 활용)
            try:
                holder = _run(["ps", "-eo", "pid,etime,cmd"]).splitlines()
                hit = [l for l in holder if f"flock" in l and p.name in l]
                info = hit[0].strip() if hit else "holder unknown"
            except Exception:
                info = "holder unknown"
            stuck.append(f"{p.name} ({info})")
    if stuck:
        _alert("locks", f"락 보유 중인 프로세스 있음: {'; '.join(stuck)}")


def check_app_healthz():
    try:
        urlopen("http://127.0.0.1:5050/healthz", timeout=5)
    except Exception as e:
        _alert("app", f"Uvicorn /healthz 응답 없음: {e}")


def check_services():
    for svc in ("postgresql", "cron", "tradelab"):
        out = _run(["systemctl", "is-active", svc])
        if out not in ("active", "activating"):
            _alert(f"svc_{svc}", f"서비스 {svc} 상태: {out or 'unknown'}")


def check_oom():
    out = _run(["sudo", "-n", "dmesg", "-T"], timeout=5)
    if not out:
        return
    cutoff = datetime.now() - timedelta(minutes=30)
    recent_oom = 0
    for line in out.splitlines()[-500:]:
        if "Out of memory" in line or "oom-kill" in line:
            # dmesg -T 형식: [Mon Apr 14 03:00:24 2026] ...
            try:
                ts_str = line[line.index("[") + 1:line.index("]")]
                ts = datetime.strptime(ts_str, "%a %b %d %H:%M:%S %Y")
                if ts > cutoff:
                    recent_oom += 1
            except Exception:
                pass
    if recent_oom:
        _alert("oom", f"최근 30분 내 OOM 이벤트 {recent_oom}건")


def check_signal_pipeline():
    try:
        from db.database import SessionLocal
        from db.models import SignalData
        session = SessionLocal()
        try:
            last = session.query(SignalData).order_by(SignalData.collected_at.desc()).first()
            if last is None:
                return
            age_min = (datetime.now() - last.collected_at).total_seconds() / 60
            if age_min > 30:
                _alert("pipeline", f"시그널 수집 정지: 마지막 insert {int(age_min)}분 전")
        finally:
            session.close()
    except Exception as e:
        logger.error(f"pipeline 체크 실패: {e}")


def main():
    logger.info("health check 시작")
    checks = [
        check_memory, check_python_procs, check_stuck_locks,
        check_app_healthz, check_services, check_oom, check_signal_pipeline,
    ]
    for c in checks:
        try:
            c()
        except Exception as e:
            logger.error(f"{c.__name__} 실패: {e}")
    logger.info("health check 종료")


if __name__ == "__main__":
    main()
