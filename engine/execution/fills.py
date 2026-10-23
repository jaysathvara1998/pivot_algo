"""Conservative paper fill model on bars (card 30 rule 3: paper is an upper bound, so every ambiguity resolves against the system).
Conventions match the Level 17/19 simulators: a bar that touches both a stop and a favourable level fills the stop first; a bar opening
beyond a trigger fills at the open; a limit fills only when price trades strictly through it (a touch is not a fill)."""
from __future__ import annotations

from datetime import datetime
from typing import Optional

from .config import ExecConfig
from .orders import Order


def _adverse(side: str, px: float, pts: float) -> float:
    return px + pts if side == "BUY" else px - pts


def fill_on_bar(o: Order, bar, cfg: ExecConfig, at: Optional[datetime] = None, partial_fraction: float = 1.0) -> Optional[float]:
    """Try to fill `o` on `bar` (Candle). Returns the fill price or None. Mutates the order state. `partial_fraction` < 1 simulates a partial
    fill of that fraction of the remaining quantity (rounded down to lots)."""
    if o.is_terminal or o.remaining <= 0:
        return None
    px = None
    if o.order_type == "MARKET":
        if o.state in ("SUBMITTED", "OPEN", "UNKNOWN", "PARTIALLY_FILLED"):
            px = _adverse(o.side, bar.open, cfg.market_slippage_points)
    elif o.order_type == "LIMIT":
        if o.state == "SUBMITTED":
            o.transition("OPEN", at)
        if o.state in ("OPEN", "PARTIALLY_FILLED"):
            if o.side == "BUY" and bar.low < o.price:
                px = min(o.price, bar.open)
            elif o.side == "SELL" and bar.high > o.price:
                px = max(o.price, bar.open)
    else:                                               # SL / SL-M
        if o.state == "SUBMITTED":
            o.transition("PENDING_TRIGGER", at)
        if o.state == "PENDING_TRIGGER":
            hit = (bar.high >= o.trigger) if o.side == "BUY" else (bar.low <= o.trigger)
            if hit:
                o.transition("TRIGGERED", at)
        if o.state in ("TRIGGERED", "TRIGGERED_UNFILLED", "PARTIALLY_FILLED"):
            ref = max(o.trigger, bar.open) if o.side == "BUY" else min(o.trigger, bar.open)   # gap through the trigger fills at the open
            if o.order_type == "SL-M":
                px = _adverse(o.side, ref, cfg.market_slippage_points)
            else:                                                                             # SL with limit: fill only if the limit is reachable after the trigger
                reachable = (ref <= o.price) if o.side == "BUY" else (ref >= o.price)
                if reachable:
                    px = ref
                elif o.state == "TRIGGERED":
                    o.transition("TRIGGERED_UNFILLED", at, "limit not reachable after trigger")
    if px is None:
        return None
    px = round(round(px / cfg.tick) * cfg.tick, 2)
    qty = o.remaining if partial_fraction >= 1.0 else max(cfg.lot, (int(o.remaining * partial_fraction) // cfg.lot) * cfg.lot)
    qty = min(qty, o.remaining)
    if o.state in ("SUBMITTED", "UNKNOWN"):
        o.transition("OPEN", at)
    o.record_fill(qty, px, at)
    return px
