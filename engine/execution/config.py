"""Regime-dated execution configuration and the cost calculator (card 26, card 30). SOURCE FACT dates; rates labelled in-line."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time

from engine.risk.sizing import CONTRACTS, nifty_lot

REGIME_DATES = {                       # SOURCE FACT (cards 26, 27, 30; topic 19)
    "stt_0_02pct": date(2024, 10, 1), "stt_0_05pct": date(2026, 4, 1), "nifty_lot_75": date(2024, 11, 20), "nifty_lot_65": date(2025, 12, 31),
    "one_weekly_expiry": date(2024, 11, 20), "tue_thu_expiry": date(2025, 9, 1), "freeze_1800": date(2026, 3, 2), "cas_static_1515": date(2026, 8, 3),
    "pre_open_change": date(2026, 9, 7),
}
SESSION_OPEN = time(9, 15); SESSION_CLOSE = time(15, 30); STATIC_FROM = time(15, 15)


def stt_sell_rate(d: date) -> float:
    """Futures STT on sell-side notional: 0.0125 % → 0.02 % (2024-10-01) → 0.05 % (2026-04-01). SOURCE FACT."""
    return 0.0005 if d >= REGIME_DATES["stt_0_05pct"] else 0.0002 if d >= REGIME_DATES["stt_0_02pct"] else 0.000125


@dataclass(frozen=True)
class CostRates:
    stt_sell: float
    exchange_txn: float = 0.0000173        # per side on notional — PROJECT ESTIMATE (NSE futures, card 26 table)
    sebi_per_crore: float = 10.0           # per side — SOURCE FACT
    stamp_buy: float = 0.00002             # buy side — SOURCE FACT
    brokerage_per_order: float = 20.0      # SOURCE FACT (discount broker)
    gst: float = 0.18                      # on brokerage + exchange + SEBI — SOURCE FACT


def round_trip_cost_inr(d: date, price: float, units: int, orders: int = 2) -> dict:
    """Explicit cost of one round trip at `price` for `units` (lots × lot size). Returns the breakdown and the cost in index points per unit."""
    r = CostRates(stt_sell_rate(d)); notional = price * units
    stt = r.stt_sell * notional; exch = r.exchange_txn * notional * 2; sebi = r.sebi_per_crore * notional / 1e7 * 2
    stamp = r.stamp_buy * notional; brok = r.brokerage_per_order * orders; gst = r.gst * (brok + exch + sebi)
    total = stt + exch + sebi + stamp + brok + gst
    return {"stt": round(stt, 2), "exchange": round(exch, 2), "sebi": round(sebi, 2), "stamp": round(stamp, 2), "brokerage": round(brok, 2), "gst": round(gst, 2),
            "total_inr": round(total, 2), "points_per_unit": round(total / units, 3) if units else None, "stt_rate": r.stt_sell}


@dataclass(frozen=True)
class ExecConfig:
    instrument: str
    d: date
    lot: int
    freeze_units: int
    margin_per_lot_inr: float
    static_after: time | None          # no new orders / fills after this time when the CAS static window applies
    tick: float = 0.05
    latency_bars: int = 1              # orders act on the next bar (conservative)
    market_slippage_points: float = 0.0   # extra adverse points on market / SL-M fills until a pilot measures it (card 30)


def exec_config(instrument: str, d: date) -> ExecConfig:
    c = CONTRACTS[instrument]
    lot = nifty_lot(d) if instrument == "NIFTY" else c.lot
    return ExecConfig(instrument, d, lot, c.freeze_qty, c.margin_per_lot_inr, STATIC_FROM if d >= REGIME_DATES["cas_static_1515"] else None)
