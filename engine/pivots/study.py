"""Skill 44 — daily Fibonacci pivots and EMA-9 crossings on k-minute bars."""
from __future__ import annotations

from datetime import time

from engine.emavwap import ema

LEVELS = ("S2", "S1", "P", "R1", "R2"); FIB = {"R1": 0.382, "R2": 0.618, "S1": -0.382, "S2": -0.618}
FIRST, LAST = time(9, 20), time(15, 0)


def daily_pivots(bars_k):
    """Per k-bar: dict of today's Fibonacci pivot levels from the previous session's H/L/C (None on the first session)."""
    out = []; cur = None; H = L = C = None; pH = pL = pC = None
    for b in bars_k:
        if b.timestamp.date() != cur:
            if cur is not None:
                pH, pL, pC = H, L, C
            cur = b.timestamp.date(); H, L = b.high, b.low
        H = max(H, b.high); L = min(L, b.low); C = b.close
        if pH is None:
            out.append(None)
        else:
            P = (pH + pL + pC) / 3.0; rng = pH - pL
            out.append({"P": P, **{k: P + f * rng for k, f in FIB.items()}})
    return out


def pivot_cross_signals(bars_k, once_per_day=True):
    """[(k-index, side, level_name, level, stop)] — EMA9 crossing a pivot level; stop = previous candle's low (long) / high (short).
    once_per_day=True (the tested rule): one signal per level per direction per day — a later re-cross of the same level the same way is ignored."""
    e9 = ema([b.close for b in bars_k], 9); piv = daily_pivots(bars_k); out = []; used = set()
    for i in range(10, len(bars_k)):
        pv = piv[i]; b = bars_k[i]
        if not pv or not (FIRST <= b.timestamp.time() <= LAST):
            continue
        for name in LEVELS:
            lvl = pv[name]
            if e9[i - 1] < lvl <= e9[i]:
                side = 1
            elif e9[i - 1] > lvl >= e9[i]:
                side = -1
            else:
                continue
            key = (b.timestamp.date(), name, side)
            if once_per_day and key in used:
                continue
            used.add(key); stop = bars_k[i - 1].low if side > 0 else bars_k[i - 1].high
            out.append((i, side, name, lvl, stop))
    return out
