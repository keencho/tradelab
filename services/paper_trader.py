"""가상매매 — 단일 계좌, 시작자본 KRW. 가격은 네이티브로 저장 + fx_rate 동봉."""

from datetime import datetime

from sqlalchemy.orm import Session

from config import KST, BROKER_FEES, get_logger
from db.models import Trade, PaperHolding

logger = get_logger("paper_trader")


# 시장 → BROKER_FEES 룩업용 account_type
MARKET_ACCOUNT_TYPE = {
    "kr_stock": "regular_kr",
    "us_stock": "regular_oversea",
    "crypto":   "crypto",
}

# 시장별 사용 가능한 가상 브로커
BROKERS_BY_MARKET = {
    "kr_stock": ["toss", "samsung", "kis"],
    "us_stock": ["toss", "samsung", "kis"],
    "crypto":   ["upbit", "bithumb", "binance", "bybit"],
}


def _now():
    return datetime.now(KST).replace(tzinfo=None)


def calc_fee_tax(broker: str, market: str, side: str,
                 price: float, qty: float) -> tuple[float, float]:
    """수수료/세금 자동 계산 (네이티브 통화 기준)."""
    acc_type = MARKET_ACCOUNT_TYPE.get(market)
    if not acc_type:
        return 0.0, 0.0
    rule = BROKER_FEES.get((broker, acc_type), {"buy": 0.0, "sell": 0.0, "tax_sell": 0.0})
    notional = price * qty
    if side == "buy":
        fee = notional * rule["buy"] / 100
        tax = 0.0
    else:  # sell
        fee = notional * rule["sell"] / 100
        tax = notional * rule["tax_sell"] / 100
    return round(fee, 4), round(tax, 4)


def _get_or_create_holding(session: Session, owner: str, ticker: str, market: str,
                           ticker_name: str = "") -> PaperHolding:
    h = session.query(PaperHolding).filter(
        PaperHolding.owner == owner,
        PaperHolding.ticker == ticker, PaperHolding.market == market,
    ).first()
    if h:
        return h
    h = PaperHolding(
        owner=owner, ticker=ticker, ticker_name=ticker_name, market=market,
        qty=0.0, avg_cost=0.0, avg_cost_krw=0.0,
        realized_pnl=0.0, realized_pnl_krw=0.0,
    )
    session.add(h)
    session.flush()
    return h


def add_trade(session: Session, owner: str, ticker: str, ticker_name: str, market: str,
              broker: str, side: str, qty: float, price: float, fx_rate: float,
              fee: float | None = None, tax: float | None = None,
              executed_at: datetime | None = None, memo: str = "") -> Trade:
    """매매 추가 + 잔고/평단/실현손익 갱신.

    price/fee/tax 는 네이티브 통화. fx_rate 는 KRW per native.
    매수: cash 차감 = (qty*price + fee) * fx_rate
    매도: cash 증가 = (qty*price - fee - tax) * fx_rate
    """
    if market not in MARKET_ACCOUNT_TYPE:
        raise ValueError(f"지원하지 않는 시장: {market}")
    if side not in ("buy", "sell"):
        raise ValueError(f"side: buy|sell")
    if broker not in BROKERS_BY_MARKET.get(market, []):
        raise ValueError(f"{market} 에서 사용 불가한 브로커: {broker}")
    if qty <= 0 or price <= 0 or fx_rate <= 0:
        raise ValueError("qty/price/fx_rate 는 0보다 커야 함")

    if fee is None or tax is None:
        auto_fee, auto_tax = calc_fee_tax(broker, market, side, price, qty)
        if fee is None: fee = auto_fee
        if tax is None: tax = auto_tax

    h = _get_or_create_holding(session, owner, ticker, market, ticker_name)
    if ticker_name and not h.ticker_name:
        h.ticker_name = ticker_name

    realized = 0.0
    realized_krw = 0.0

    if side == "buy":
        new_qty = h.qty + qty
        if new_qty > 0:
            # 평단 갱신 (수수료 포함)
            h.avg_cost = (h.avg_cost * h.qty + price * qty + fee) / new_qty
            h.avg_cost_krw = (h.avg_cost_krw * h.qty + (price * qty + fee) * fx_rate) / new_qty
        h.qty = new_qty

    else:  # sell
        if qty > h.qty + 1e-9:
            raise ValueError(f"보유 부족: {h.qty} 보유, {qty} 매도")
        realized = (price - h.avg_cost) * qty - fee - tax
        # KRW 실현손익: 매도 대금(현재 fx) - 원가(평단 fx 가중평균)
        proceeds_krw = (price * qty - fee - tax) * fx_rate
        cost_krw = h.avg_cost_krw * qty
        realized_krw = proceeds_krw - cost_krw
        h.qty -= qty
        h.realized_pnl += realized
        h.realized_pnl_krw += realized_krw
        if h.qty < 1e-9:
            h.qty = 0.0
            h.avg_cost = 0.0
            h.avg_cost_krw = 0.0

    h.updated_at = _now()

    trade = Trade(
        owner=owner,
        ticker=ticker, ticker_name=ticker_name or h.ticker_name,
        market=market, broker=broker, side=side,
        qty=qty, price=price, fee=fee, tax=tax, fx_rate=fx_rate,
        realized_pnl=round(realized, 4),
        executed_at=executed_at or _now(),
        memo=memo,
    )
    session.add(trade)
    return trade


def recompute_holding(session: Session, owner: str, ticker: str, market: str):
    """해당 종목의 모든 거래를 시간순으로 재실행해 잔고를 다시 만듦."""
    h = session.query(PaperHolding).filter(
        PaperHolding.owner == owner,
        PaperHolding.ticker == ticker, PaperHolding.market == market,
    ).first()
    if not h:
        return

    trades = (
        session.query(Trade)
        .filter(Trade.owner == owner, Trade.ticker == ticker, Trade.market == market)
        .order_by(Trade.executed_at.asc(), Trade.id.asc())
        .all()
    )

    qty = 0.0
    avg = 0.0
    avg_krw = 0.0
    realized = 0.0
    realized_krw = 0.0

    for t in trades:
        if t.side == "buy":
            new_qty = qty + t.qty
            if new_qty > 0:
                avg = (avg * qty + t.price * t.qty + t.fee) / new_qty
                avg_krw = (avg_krw * qty + (t.price * t.qty + t.fee) * t.fx_rate) / new_qty
            qty = new_qty
            t.realized_pnl = 0.0
        else:  # sell
            if t.qty > qty + 1e-9:
                logger.warning(f"recompute: 보유 부족 {ticker}/{market} {qty} < {t.qty}")
            pnl = (t.price - avg) * t.qty - t.fee - t.tax
            t.realized_pnl = round(pnl, 4)
            realized += pnl
            proceeds_krw = (t.price * t.qty - t.fee - t.tax) * t.fx_rate
            cost_krw = avg_krw * t.qty
            realized_krw += proceeds_krw - cost_krw
            qty -= t.qty
            if qty < 1e-9:
                qty = 0.0
                avg = 0.0
                avg_krw = 0.0

    h.qty = qty
    h.avg_cost = avg
    h.avg_cost_krw = avg_krw
    h.realized_pnl = round(realized, 4)
    h.realized_pnl_krw = round(realized_krw, 4)
    h.updated_at = _now()


def delete_trade(session: Session, owner: str, trade_id: int):
    """거래 삭제 후 해당 종목 재계산."""
    t = session.query(Trade).filter(Trade.id == trade_id, Trade.owner == owner).first()
    if not t:
        return
    ticker = t.ticker
    market = t.market
    session.delete(t)
    session.flush()
    recompute_holding(session, owner, ticker, market)


def cash_balance_krw(session: Session, owner: str, initial_capital: float) -> float:
    """가용 현금 = 시작자본 - 매수합계(KRW) + 매도수익(KRW)."""
    trades = session.query(Trade).filter(Trade.owner == owner).all()
    cash = initial_capital
    for t in trades:
        if t.side == "buy":
            cash -= (t.price * t.qty + t.fee) * t.fx_rate
        else:  # sell
            cash += (t.price * t.qty - t.fee - t.tax) * t.fx_rate
    return cash


def reset_all(session: Session, owner: str):
    """해당 user 거래/잔고 삭제. 시작자본 설정은 유지."""
    session.query(Trade).filter(Trade.owner == owner).delete()
    session.query(PaperHolding).filter(PaperHolding.owner == owner).delete()
