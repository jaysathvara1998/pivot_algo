"""Reconciliation and slippage measurement (card 30 rules 2–3)."""
from __future__ import annotations

import math


def reconcile(internal: dict, broker: dict) -> dict:
    keys = set(internal) | set(broker)
    mism = {k: {"internal": internal.get(k, 0), "broker": broker.get(k, 0)} for k in keys if internal.get(k, 0) != broker.get(k, 0)}
    return {"ok": not mism, "mismatches": mism}


class SlippageLog:
    """Realised slippage per unit vs the signal-time reference price (adverse = positive)."""

    def __init__(self):
        self.rows = []

    def add(self, client_id: str, side: str, reference: float, fill: float, qty: int, order_type: str) -> float:
        s = (fill - reference) if side == "BUY" else (reference - fill)
        self.rows.append({"client_id": client_id, "side": side, "order_type": order_type, "reference": reference, "fill": fill, "qty": qty, "slippage_points": round(s, 2)})
        return s

    def summary(self) -> dict:
        xs = [r["slippage_points"] for r in self.rows]
        if not xs:
            return {"n": 0}
        n = len(xs); m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0; s = sorted(xs)
        by_type = {}
        for r in self.rows:
            by_type.setdefault(r["order_type"], []).append(r["slippage_points"])
        return {"n": n, "mean": round(m, 3), "sd": round(sd, 3), "se": round(sd / math.sqrt(n), 3) if n > 1 else None, "q50": s[n // 2], "q90": s[min(n - 1, int(0.9 * n))],
                "by_order_type": {k: round(sum(v) / len(v), 3) for k, v in by_type.items()},
                "n_for_se_0_5pt": (math.ceil((sd / 0.5) ** 2) if sd else 1)}      # pilot size needed to pin the mean to ±0.5 points (1 SE)
