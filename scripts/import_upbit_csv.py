"""Upbit orders.csv → real_trades / real_holdings SQL 생성.

사용:
  python scripts/import_upbit_csv.py <orders.csv> <out.sql>

생성되는 SQL:
  - account_id=7 (업비트 코인) 의 csv 종목 기존 trade/holding DELETE
  - real_trades INSERT (매도 trade 의 realized_pnl 시뮬값 포함)
  - real_holdings INSERT (종목별 최종 누적 상태)
"""
import csv
import sys
from datetime import datetime

ACCOUNT_ID = 7
MARKET = "crypto"

TICKER_NAME = {
    "AGLD": "어드벤처골드",
    "ANIME": "애니메코인",
    "AQT": "알파쿼크",
    "BTC": "비트코인",
    "BTG": "비트코인골드",
    "ETH": "이더리움",
    "MOCA": "모카네트워크",
    "MOVE": "무브먼트",
    "PCI": "페이코인",
    "SBD": "스팀달러",
    "SOL": "솔라나",
    "STRIKE": "스트라이크",
    "SXP": "솔라(구SWIPE)",
    "UXLINKOLD": "UXLINK(구)",
    "XRP": "엑스알피(리플)",
}


def sql_str(s: str) -> str:
    return "'" + str(s).replace("'", "''") + "'"


def main(csv_path: str, sql_path: str):
    rows = []
    with open(csv_path, encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for r in reader:
            qty = float(r["체결수량"])
            # status='done' 뿐 아니라 'cancel' 의 부분 체결 건도 포함 (qty>0)
            # Upbit 의 지정가 주문은 일부 체결 후 나머지 cancel 되는 경우가 흔함
            if qty <= 0:
                continue
            rows.append({
                "ts": datetime.strptime(r["일시"], "%Y-%m-%d %H:%M:%S"),
                "ticker": r["종목"].strip(),
                "side": "buy" if r["구분"] == "매수" else "sell",
                "qty": qty,
                "price": float(r["체결가"]),
                "krw_amount": float(r["체결금액(KRW)"]),
                "fee": float(r["수수료"]),
                "uuid": r["uuid"].strip(),
                "status": r["상태"],
            })
    rows.sort(key=lambda x: x["ts"])

    state: dict[str, dict] = {}
    trade_inserts = []

    for r in rows:
        st = state.setdefault(r["ticker"], {"qty": 0.0, "avg_cost": 0.0, "realized": 0.0})
        realized_for_this = 0.0

        if r["side"] == "buy":
            # 매수원가 = 체결금액(KRW) + 수수료 (CSV 의 절대 KRW 흐름 기준 — round-off 누락 방지)
            cost_for_this = r["krw_amount"] + r["fee"]
            new_qty = st["qty"] + r["qty"]
            if new_qty > 0:
                st["avg_cost"] = (st["avg_cost"] * st["qty"] + cost_for_this) / new_qty
            st["qty"] = new_qty
        else:  # sell
            # 매도 수익 = 체결금액(KRW) - 수수료
            sell_value = r["krw_amount"] - r["fee"]
            realized_for_this = sell_value - st["avg_cost"] * r["qty"]
            st["realized"] += realized_for_this
            st["qty"] -= r["qty"]
            if st["qty"] <= 1e-12:
                st["qty"] = 0.0
                st["avg_cost"] = 0.0

        trade_inserts.append({
            "ticker": r["ticker"],
            "ticker_name": TICKER_NAME.get(r["ticker"], r["ticker"]),
            "side": r["side"],
            "qty": r["qty"],
            "price": r["price"],
            "fee": r["fee"],
            "tax": 0.0,
            "realized_pnl": realized_for_this,
            "executed_at": r["ts"].strftime("%Y-%m-%d %H:%M:%S"),
            "memo": f"upbit:{r['uuid'][:8]}",
        })

    tickers = sorted(state.keys())
    ticker_list_sql = ", ".join(sql_str(t) for t in tickers)

    out = []
    out.append("-- Upbit orders.csv import")
    out.append(f"-- account_id={ACCOUNT_ID}, market={MARKET}")
    out.append(f"-- {len(trade_inserts)} trades, {len(state)} holdings")
    out.append("")
    out.append("BEGIN;")
    out.append("")
    out.append("-- 1) 기존 정리")
    out.append(f"DELETE FROM real_trades  WHERE account_id = {ACCOUNT_ID} AND ticker IN ({ticker_list_sql});")
    out.append(f"DELETE FROM real_holdings WHERE account_id = {ACCOUNT_ID} AND ticker IN ({ticker_list_sql});")
    out.append("")

    out.append("-- 2) real_trades INSERT")
    out.append("INSERT INTO real_trades")
    out.append("  (account_id, ticker, ticker_name, market, side, qty, price, fee, tax, realized_pnl, executed_at, memo)")
    out.append("VALUES")
    vals = []
    for t in trade_inserts:
        vals.append(
            f"  ({ACCOUNT_ID}, {sql_str(t['ticker'])}, {sql_str(t['ticker_name'])}, {sql_str(MARKET)}, "
            f"{sql_str(t['side'])}, {t['qty']:.10f}, {t['price']:.4f}, {t['fee']:.4f}, {t['tax']:.4f}, "
            f"{t['realized_pnl']:.4f}, {sql_str(t['executed_at'])}, {sql_str(t['memo'])})"
        )
    out.append(",\n".join(vals) + ";")
    out.append("")

    out.append("-- 3) real_holdings INSERT (종목별 최종 누적)")
    out.append("INSERT INTO real_holdings")
    out.append("  (account_id, ticker, ticker_name, market, qty, avg_cost, realized_pnl, is_hidden, updated_at)")
    out.append("VALUES")
    vals = []
    for tk in tickers:
        st = state[tk]
        vals.append(
            f"  ({ACCOUNT_ID}, {sql_str(tk)}, {sql_str(TICKER_NAME.get(tk, tk))}, {sql_str(MARKET)}, "
            f"{st['qty']:.10f}, {st['avg_cost']:.4f}, {st['realized']:.4f}, false, NOW())"
        )
    out.append(",\n".join(vals) + ";")
    out.append("")
    out.append("COMMIT;")
    out.append("")

    with open(sql_path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(out))

    # 요약은 stdout 으로 (한국어 깨질 수 있으니 ASCII 만)
    print(f"[OK] {sql_path} written: {len(trade_inserts)} trades, {len(state)} holdings")
    print(f"{'ticker':<12}{'qty':>20}{'avg_cost':>16}{'realized':>16}")
    for tk in tickers:
        st = state[tk]
        print(f"{tk:<12}{st['qty']:>20.10f}{st['avg_cost']:>16.4f}{st['realized']:>16.4f}")


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(__doc__)
        sys.exit(1)
    main(sys.argv[1], sys.argv[2])
