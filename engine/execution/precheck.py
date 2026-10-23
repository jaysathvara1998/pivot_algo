"""Pre-submission checks (card 30 rule 1): freeze, lot multiple, order-type restrictions, margin, session time, daily limits."""
from __future__ import annotations

from datetime import datetime

from .config import ExecConfig, SESSION_OPEN, SESSION_CLOSE
from .orders import Order, ORDER_TYPES, VALIDITY


def precheck(o: Order, cfg: ExecConfig, now: datetime, available_margin_inr: float, is_index_option: bool = False, open_positions: int = 0,
             max_concurrent: int = 1, day_trades: int = 0, max_trades: int = 5, day_loss_r: float = 0.0, max_loss_r: float = 3.0) -> list:
    """Returns the list of rejection reasons (empty = passes)."""
    r = []
    if o.order_type not in ORDER_TYPES:
        r.append("BAD_ORDER_TYPE")
    if o.validity not in VALIDITY:
        r.append("BAD_VALIDITY")
    if o.qty <= 0 or o.qty % cfg.lot:
        r.append("NOT_LOT_MULTIPLE")
    if o.qty > cfg.freeze_units:
        r.append("ABOVE_FREEZE")
    if o.order_type in ("LIMIT", "SL") and o.price is None:
        r.append("LIMIT_PRICE_MISSING")
    if o.order_type in ("SL", "SL-M") and o.trigger is None:
        r.append("TRIGGER_MISSING")
    if o.order_type == "SL-M" and is_index_option:
        r.append("SLM_BLOCKED_ON_INDEX_OPTIONS")           # SOURCE FACT (Kite)
    if o.order_type == "SL" and o.price is not None and o.trigger is not None:
        if (o.side == "BUY" and o.price < o.trigger) or (o.side == "SELL" and o.price > o.trigger):
            r.append("SL_LIMIT_ON_WRONG_SIDE_OF_TRIGGER")
    t = now.time()
    if not (SESSION_OPEN <= t < SESSION_CLOSE):
        r.append("OUTSIDE_SESSION")
    if cfg.static_after is not None and t >= cfg.static_after:
        r.append("STATIC_WINDOW")
    if o.role == "ENTRY":
        lots = o.qty // cfg.lot if cfg.lot else 0
        if lots * cfg.margin_per_lot_inr > available_margin_inr:
            r.append("INSUFFICIENT_MARGIN")
        if open_positions >= max_concurrent:
            r.append("MAX_CONCURRENT")
        if day_trades >= max_trades:
            r.append("MAX_TRADES")
        if day_loss_r <= -abs(max_loss_r):
            r.append("MAX_LOSS")
    return r
