"""Price a specific strike at a minute from the rolling ATM / wing rows: observed when ATM or OTM (≤ 3 strikes), put–call parity when ITM, Black–Scholes fallback (flagged)."""
from __future__ import annotations

import math
from datetime import datetime

from engine.options.bs import bs_price, years_to_expiry, next_weekly_expiry, RISK_FREE

STEP = {"NIFTY": 50.0, "SENSEX": 100.0}


def price_at(index: str, ts16: str, K: float, leg: str, atm: dict, wings: dict) -> tuple | None:
    """Returns (price, method) with method in {'OBS', 'PARITY', 'BS'} or None when no row exists at that minute."""
    a = atm.get(ts16)
    if a is None:
        return None
    off = round((K - a["strike"]) / STEP[index])
    other = "PE" if leg == "CE" else "CE"
    if off == 0:
        return (a["ce"] if leg == "CE" else a["pe"], "OBS")
    otm_leg_offset = off if leg == "CE" else -off           # a call above ATM / a put below ATM is OTM at this positive distance
    if otm_leg_offset > 0:
        w = wings.get((ts16, leg, f"{'+' if leg == 'CE' else '-'}{abs(off)}"))
        if w:
            return (w["close"], "OBS")
    else:                                                     # ITM: price the OTM other leg at the same strike and apply parity
        w = wings.get((ts16, other, f"{'+' if other == 'CE' else '-'}{abs(off)}"))
        if w:
            ts = datetime.fromisoformat(ts16.replace(" ", "T") + ":00+05:30")
            T = years_to_expiry(ts, next_weekly_expiry(ts.date(), index.lower())); disc = K * math.exp(-RISK_FREE * T); S = a["spot"]
            px = (w["close"] + S - disc) if leg == "CE" else (w["close"] - S + disc)
            return (max(0.05, px), "PARITY")
    ts = datetime.fromisoformat(ts16.replace(" ", "T") + ":00+05:30")
    T = years_to_expiry(ts, next_weekly_expiry(ts.date(), index.lower()))
    return (max(0.05, bs_price(a["spot"], K, T, a["iv"] / 100, "C" if leg == "CE" else "P")), "BS")
