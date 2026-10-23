"""Cell × period performance matrices from trade lists (Skill 22)."""
from __future__ import annotations

from collections import defaultdict


def period_key(ts_iso: str, granularity: str = "month") -> str:
    y, m = int(ts_iso[:4]), int(ts_iso[5:7])
    return f"{y}-{m:02d}" if granularity == "month" else f"{y}-Q{(m - 1) // 3 + 1}"


def performance_matrix(trades_by_cell: dict, cost_multiplier: float = 2.0, granularity: str = "month", periods: list | None = None) -> dict:
    """{cell: [BtTrade]} → {'cells': [...], 'periods': [...], 'matrix': {cell: [total net R per period]}} ; a period with no trade scores 0."""
    totals = {c: defaultdict(float) for c in trades_by_cell}
    seen = set(periods or [])
    for c, trades in trades_by_cell.items():
        for t in trades:
            k = period_key(t.entry_ts, granularity); totals[c][k] += t.r_net_at(cost_multiplier); seen.add(k)
    ps = sorted(seen)
    return {"cells": list(trades_by_cell), "periods": ps, "granularity": granularity, "cost_multiplier": cost_multiplier,
            "matrix": {c: [round(totals[c].get(k, 0.0), 4) for k in ps] for c in trades_by_cell}}
