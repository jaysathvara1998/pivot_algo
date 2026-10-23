"""Fixed-fractional sizing with lot discretisation and the margin bound (cards 27–28; SOURCE FACT lot sizes, PROJECT DEFINITION margins)."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from datetime import date


@dataclass(frozen=True)
class Contract:
    name: str
    lot: int                       # units per lot
    margin_per_lot_inr: float      # approximate initial margin (SPAN + exposure); PROJECT ESTIMATE from card 27 (≈ 9× leverage)
    freeze_qty: int                # exchange freeze quantity in units
    tick: float


def nifty_lot(d: date) -> int:
    return 65 if d >= date(2025, 12, 31) else 75 if d >= date(2024, 11, 20) else 25      # card 27 / topic 27


CONTRACTS = {
    "NIFTY": Contract("NIFTY", 65, 175_000.0, 1800, 0.05),
    "SENSEX": Contract("SENSEX", 20, 180_000.0, 1000, 0.05),
}


@dataclass
class SizingResult:
    lots: int
    units: int
    risk_inr: float
    risk_pct: float
    margin_inr: float
    binding: str              # RISK | MARGIN | FREEZE | ZERO

    def as_dict(self) -> dict:
        return asdict(self)


def lots_for_risk(capital_inr: float, risk_fraction: float, stop_points: float, contract: Contract, max_margin_fraction: float = 0.5) -> SizingResult:
    """lots = floor(min(risk budget ÷ (stop × lot), margin budget ÷ margin per lot, freeze ÷ lot))."""
    if stop_points <= 0 or capital_inr <= 0:
        return SizingResult(0, 0, 0.0, 0.0, 0.0, "ZERO")
    by_risk = math.floor(capital_inr * risk_fraction / (stop_points * contract.lot))
    by_margin = math.floor(capital_inr * max_margin_fraction / contract.margin_per_lot_inr)
    by_freeze = contract.freeze_qty // contract.lot
    lots = max(0, min(by_risk, by_margin, by_freeze))
    binding = "ZERO" if lots == 0 else "RISK" if lots == by_risk else "MARGIN" if lots == by_margin else "FREEZE"
    risk = lots * contract.lot * stop_points
    return SizingResult(lots, lots * contract.lot, round(risk, 2), round(100 * risk / capital_inr, 4), round(lots * contract.margin_per_lot_inr, 2), binding)
