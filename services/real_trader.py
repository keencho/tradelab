"""실투자 거래 입력 + 잔고/평단/실현손익 자동 계산."""

from datetime import datetime

from sqlalchemy.orm import Session

from config import KST, BROKER_FEES, ACCOUNT_TYPE_MARKET, get_logger
from db.models import RealAccount, RealHolding, RealTrade, Watchlist

logger = get_logger("real_trader")


def _now():
    return datetime.now(KST).replace(tzinfo=None)


def _fee_rule(broker: str, account_type: str) -> dict:
    return BROKER_FEES.get((broker, account_type), {"buy": 0.0, "sell": 0.0, "tax_sell": 0.0})


def _ensure_watchlist(session: Session, ticker: str, market: str, name: str):
    """보유종목을 워치리스트에 자동 추가 — realtime_price 컬렉터가 가격 갱신해줌."""
    if not ticker or market not in ("kr_stock", "us_stock", "crypto"):
        return
    exists = session.query(Watchlist).filter(
        Watchlist.market == market, Watchlist.ticker == ticker
    ).first()
    if exists:
        return
    count = session.query(Watchlist).filter(
        Watchlist.market == market, Watchlist.is_active == True
    ).count()
    session.add(Watchlist(
        market=market, ticker=ticker, name=name or ticker,
        is_active=True, sort_order=count,
    ))


def _get_or_create_holding(session: Session, account_id: int, ticker: str,
                           ticker_name: str, market: str) -> RealHolding:
    h = session.query(RealHolding).filter(
        RealHolding.account_id == account_id,
        RealHolding.ticker == ticker,
    ).first()
    if h:
        return h
    h = RealHolding(
        account_id=account_id, ticker=ticker, ticker_name=ticker_name,
        market=market, qty=0.0, avg_cost=0.0, realized_pnl=0.0,
    )
    session.add(h)
    session.flush()
    return h


def calc_fee_tax(broker: str, account_type: str, side: str,
                 price: float, qty: float) -> tuple[float, float]:
    """수수료/세금 자동 계산 (퍼센트 → 실금액)."""
    rule = _fee_rule(broker, account_type)
    notional = price * qty
    if side == "buy":
        fee = notional * rule["buy"] / 100
        tax = 0.0
    elif side == "sell":
        fee = notional * rule["sell"] / 100
        tax = notional * rule["tax_sell"] / 100
    else:  # dividend
        fee = 0.0
        tax = 0.0
    return round(fee, 2), round(tax, 2)


def add_trade(session: Session, account_id: int, ticker: str, ticker_name: str,
              side: str, qty: float, price: float,
              fee: float | None = None, tax: float | None = None,
              executed_at: datetime | None = None, memo: str = "") -> RealTrade:
    """거래 추가 + 잔고/평단/실현손익 갱신."""
    account = session.query(RealAccount).filter(RealAccount.id == account_id).first()
    if not account:
        raise ValueError("계좌 없음")

    market = ACCOUNT_TYPE_MARKET.get(account.account_type, "kr_stock")

    if fee is None or tax is None:
        auto_fee, auto_tax = calc_fee_tax(account.broker, account.account_type, side, price, qty)
        if fee is None:
            fee = auto_fee
        if tax is None:
            tax = auto_tax

    h = _get_or_create_holding(session, account_id, ticker, ticker_name, market)
    if ticker_name and not h.ticker_name:
        h.ticker_name = ticker_name

    realized_pnl = 0.0

    if side == "buy":
        # 이동평균 (수수료 포함)
        new_qty = h.qty + qty
        if new_qty > 0:
            h.avg_cost = (h.avg_cost * h.qty + price * qty + fee) / new_qty
        h.qty = new_qty

    elif side == "sell":
        if qty > h.qty + 1e-9:
            raise ValueError(f"보유수량 부족: 보유 {h.qty}, 매도 {qty}")
        realized_pnl = (price - h.avg_cost) * qty - fee - tax
        h.qty -= qty
        h.realized_pnl += realized_pnl
        if h.qty < 1e-9:
            h.qty = 0.0
            h.avg_cost = 0.0

    elif side == "dividend":
        # 배당: qty=배당금 KRW, price=1로 입력하거나 자유 형식
        # 단순히 realized_pnl에 누적
        realized_pnl = price * qty - fee - tax
        h.realized_pnl += realized_pnl

    else:
        raise ValueError(f"알 수 없는 side: {side}")

    h.updated_at = _now()

    trade = RealTrade(
        account_id=account_id, ticker=ticker, ticker_name=ticker_name or h.ticker_name,
        market=market, side=side, qty=qty, price=price,
        fee=fee, tax=tax, realized_pnl=round(realized_pnl, 2),
        executed_at=executed_at or _now(),
        memo=memo,
    )
    session.add(trade)

    _ensure_watchlist(session, ticker, market, ticker_name or h.ticker_name)

    return trade


def recompute_holding(session: Session, account_id: int, ticker: str):
    """해당 종목의 모든 거래를 시간순으로 재실행해 잔고를 다시 만듦. (정정/삭제 후 호출)"""
    h = session.query(RealHolding).filter(
        RealHolding.account_id == account_id,
        RealHolding.ticker == ticker,
    ).first()
    if not h:
        return

    trades = (
        session.query(RealTrade)
        .filter(RealTrade.account_id == account_id, RealTrade.ticker == ticker)
        .order_by(RealTrade.executed_at.asc(), RealTrade.id.asc())
        .all()
    )

    qty = 0.0
    avg_cost = 0.0
    realized = 0.0

    for t in trades:
        if t.side == "buy":
            new_qty = qty + t.qty
            if new_qty > 0:
                avg_cost = (avg_cost * qty + t.price * t.qty + t.fee) / new_qty
            qty = new_qty
            t.realized_pnl = 0.0
        elif t.side == "sell":
            if t.qty > qty + 1e-9:
                # 데이터 불일치 — 그래도 계속 진행
                logger.warning(f"recompute: 보유 부족 ticker={ticker} 보유={qty} 매도={t.qty}")
            pnl = (t.price - avg_cost) * t.qty - t.fee - t.tax
            t.realized_pnl = round(pnl, 2)
            realized += pnl
            qty -= t.qty
            if qty < 1e-9:
                qty = 0.0
                avg_cost = 0.0
        elif t.side == "dividend":
            pnl = t.price * t.qty - t.fee - t.tax
            t.realized_pnl = round(pnl, 2)
            realized += pnl

    h.qty = qty
    h.avg_cost = avg_cost
    h.realized_pnl = round(realized, 2)
    h.updated_at = _now()


def delete_trade(session: Session, trade_id: int):
    """거래 삭제 후 해당 종목 재계산."""
    t = session.query(RealTrade).filter(RealTrade.id == trade_id).first()
    if not t:
        return
    account_id = t.account_id
    ticker = t.ticker
    session.delete(t)
    session.flush()
    recompute_holding(session, account_id, ticker)


def update_trade(session: Session, trade_id: int, p: dict):
    """거래 정보 수정 후 영향받은 종목들 재계산.

    p: _parse_trade_body 결과 dict (account_id, ticker, ticker_name, side, qty, price, fee, tax, executed_at, memo)
    수수료/세금이 None 이면 자동 재계산.
    """
    t = session.query(RealTrade).filter(RealTrade.id == trade_id).first()
    if not t:
        raise ValueError("거래 없음")

    new_account = session.query(RealAccount).filter(RealAccount.id == p["account_id"]).first()
    if not new_account:
        raise ValueError("계좌 없음")

    market = ACCOUNT_TYPE_MARKET.get(new_account.account_type, "kr_stock")

    fee = p["fee"]
    tax = p["tax"]
    if fee is None or tax is None:
        auto_fee, auto_tax = calc_fee_tax(new_account.broker, new_account.account_type, p["side"], p["price"], p["qty"])
        if fee is None: fee = auto_fee
        if tax is None: tax = auto_tax

    # 영향받는 (account, ticker) 쌍 — 변경 전/후 모두 재계산
    affected = {(t.account_id, t.ticker)}
    affected.add((p["account_id"], p["ticker"]))

    t.account_id = p["account_id"]
    t.ticker = p["ticker"]
    t.ticker_name = p["ticker_name"] or t.ticker_name
    t.market = market
    t.side = p["side"]
    t.qty = p["qty"]
    t.price = p["price"]
    t.fee = fee
    t.tax = tax
    if p["executed_at"]:
        t.executed_at = p["executed_at"]
    t.memo = p["memo"]
    session.flush()

    # 새 종목/계좌가 아직 holding 없으면 생성
    _get_or_create_holding(session, p["account_id"], p["ticker"], p["ticker_name"], market)
    _ensure_watchlist(session, p["ticker"], market, p["ticker_name"])

    for acc_id, ticker in affected:
        recompute_holding(session, acc_id, ticker)
