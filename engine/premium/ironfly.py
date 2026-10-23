"""Expiry-day iron fly (PROJECT DEFINITION, Skill 29; see skills/29_expiry_premium_strategy/HYPOTHESES.md)."""
from __future__ import annotations

import csv
from dataclasses import dataclass, asdict
from datetime import date, datetime
from pathlib import Path

from engine.backtest import BtTrade
from engine.session.labels import expiry_flags, regime_of, phase_of

LOT = {"NIFTY": 65, "SENSEX": 20}        # 2026 lots; historical NIFTY lots differ (25/75) — sizing uses the lot in force on the trade date via lot_on
ENTRY_TIMES = ("09:20", "10:15")
WINGS = (2, 3)
CAPITAL = 50_000.0
MAX_LOSS_FRACTION = 0.10
SLIPPAGE_PER_LEG = 0.5
MARGIN_FACTOR = 1.3


def lot_on(index: str, d: date) -> int:
    if index == "SENSEX":
        return 20 if d >= date(2024, 11, 20) else 10
    return 65 if d >= date(2025, 12, 31) else 75 if d >= date(2024, 11, 20) else 25


def load_wings(path: Path) -> dict:
    """{(ts16, leg, offset): row} for the wing file."""
    out = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            try:
                out[(r["ts"][:16], r["leg"], r["offset"])] = {"close": float(r["close"]), "strike": float(r["strike"]), "iv": float(r["iv"])}
            except ValueError:
                continue
    return out


def load_settlement(path: Path) -> dict:
    return {date.fromisoformat(r["date"]): float(r["close"]) for r in csv.DictReader(open(path, newline=""))}


def structure_cost_per_unit(d: date, index: str, sold_premium: float, all_premium: float, itm_wing_intrinsic: float, lot: int) -> float:
    brok = 4 * 20.0 * 1.18 / lot
    stt_sell = (0.0015 if d >= date(2024, 10, 1) else 0.0010) * sold_premium
    exch = 0.000353 * all_premium * 1.18
    stt_ex = 0.00125 * itm_wing_intrinsic
    return brok + stt_sell + exch + stt_ex


@dataclass
class FlyTrade:
    date: str
    index: str
    entry_time: str
    wing: int
    K: float
    K_call: float
    K_put: float
    credit: float
    settlement: float
    pnl_unit: float
    max_loss_unit: float
    r_gross: float
    cost_unit: float
    r_net: float
    lot: int
    lots: int
    pnl_inr: float
    margin_inr: float
    window: str

    def as_dict(self):
        return asdict(self)


def simulate_flies(index: str, atm: dict, wings: dict, settle: dict, dates, entry_time: str, wing: int, window_of) -> list:
    """atm: {ts16: {ce, pe, strike, spot, iv}} (engine.options.data.load_rolling); wings: load_wings; settle: load_settlement."""
    out = []
    for d in dates:
        if not expiry_flags(d).get(f"{index.lower()}_weekly_expiry"):
            continue
        k16 = f"{d.isoformat()} {entry_time}"
        a = atm.get(k16); c = wings.get((k16, "CE", f"+{wing}")); p = wings.get((k16, "PE", f"-{wing}")); s = settle.get(d)
        if not a or not c or not p or s is None:
            continue
        K = a["strike"]; Kc = c["strike"]; Kp = p["strike"]
        if not (Kc > K > Kp):
            continue
        credit = (a["ce"] + a["pe"]) - (c["close"] + p["close"]) - 4 * SLIPPAGE_PER_LEG
        Wc, Wp = Kc - K, K - Kp
        loss = min(s - K, Wc) if s > K else min(K - s, Wp)
        pnl = credit - loss; max_loss = max(Wc, Wp) - credit
        if max_loss <= 0:
            continue
        itm = max(0.0, s - Kc) + max(0.0, Kp - s)
        lot = lot_on(index, d)
        cost = structure_cost_per_unit(d, index, a["ce"] + a["pe"], a["ce"] + a["pe"] + c["close"] + p["close"], itm, lot)
        lots = 1 if max_loss * lot <= MAX_LOSS_FRACTION * CAPITAL else 0
        out.append(FlyTrade(d.isoformat(), index, entry_time, wing, K, Kc, Kp, round(credit, 2), s, round(pnl, 2), round(max_loss, 2), round(pnl / max_loss, 4), round(cost, 3),
                            round((pnl - cost) / max_loss, 4), lot, lots, round((pnl - cost) * lot * lots, 2), round(MARGIN_FACTOR * max_loss * lot, 0), window_of(d)))
    return out


def to_bt(trades: list) -> list:
    out = []
    for t in trades:
        e = datetime.fromisoformat(f"{t.date}T{t.entry_time}:00+05:30"); x = datetime.fromisoformat(f"{t.date}T15:30:00+05:30"); d = e.date()
        out.append(BtTrade(e.isoformat(), x.isoformat(), "NEUTRAL", t.r_gross, t.cost_unit / t.max_loss_unit, min(0.0, t.r_gross), max(0.0, t.r_gross), 1, "EXPIRY", round(t.max_loss_unit, 2),
                           regime_of(d), phase_of(e.time(), d), f"{d.year}-{d.month:02d}"))
    return out


def inr_summary(trades: list) -> dict:
    tr = [t for t in trades if t.lots > 0]
    if not tr:
        return {"trades_taken": 0, "trades_skipped_sizing": len(trades)}
    eq = CAPITAL; peak = eq; dd = 0.0; path = []
    for t in tr:
        eq += t.pnl_inr; peak = max(peak, eq); dd = min(dd, (eq - peak) / peak); path.append(round(eq, 0))
    return {"trades_taken": len(tr), "trades_skipped_sizing": len(trades) - len(tr), "net_inr": round(sum(t.pnl_inr for t in tr), 2), "final_equity": round(eq, 0),
            "max_drawdown_pct": round(100 * dd, 2), "worst_trade_inr": round(min(t.pnl_inr for t in tr), 2), "best_trade_inr": round(max(t.pnl_inr for t in tr), 2),
            "mean_credit_unit": round(sum(t.credit for t in tr) / len(tr), 2), "mean_max_loss_unit": round(sum(t.max_loss_unit for t in tr) / len(tr), 2),
            "mean_margin_inr": round(sum(t.margin_inr for t in tr) / len(tr), 0), "win_rate": round(sum(1 for t in tr if t.pnl_inr > 0) / len(tr), 4),
            "risk_per_trade_pct_of_capital": round(100 * sum(t.max_loss_unit * t.lot for t in tr) / len(tr) / CAPITAL, 2)}
