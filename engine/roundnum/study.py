"""Skill 43 — round-number sweeps and breaks (Osler mechanism) on k-minute bars; the SWING control uses arbitrary confirmed fractal levels away from round numbers."""
from __future__ import annotations

from datetime import time

from engine.pa2 import Bars

ROUND_GAP = 15.0; LOOKBACK = 150
FIRST, LAST = time(9, 20), time(15, 0)


def levels_crossed(lo, hi, step):
    """Multiples of step strictly inside (lo, hi)."""
    k = int(lo // step) + 1; out = []
    while k * step < hi:
        out.append(k * step); k += 1
    return out


def sweep_signals_k(bars_k, kind: str):
    """[(k-index, side, level, extreme)] reversal sweeps. kind in {'R100','R500','SWING'}."""
    n = len(bars_k); out = []; used = set()
    B = Bars([b.open for b in bars_k], [b.high for b in bars_k], [b.low for b in bars_k], [b.close for b in bars_k]) if kind == "SWING" else None
    for s in range(25, n):
        b = bars_k[s]; prev = bars_k[s - 1].close; d = b.timestamp.date()
        if not (FIRST <= b.timestamp.time() <= LAST):
            continue
        if kind in ("R100", "R500"):
            step = 100.0 if kind == "R100" else 500.0
            longs = [L for L in levels_crossed(b.low, b.close, step) if prev > L]            # wick below L, close back above, came from above
            shorts = [L for L in levels_crossed(b.close, b.high, step) if prev < L]
        else:
            lows = [lvl for pi, ci, lvl in B.sw_lo if ci <= s - 1 and s - pi <= LOOKBACK and min(abs(lvl % 100), 100 - abs(lvl % 100)) >= ROUND_GAP]
            highs = [lvl for pi, ci, lvl in B.sw_hi if ci <= s - 1 and s - pi <= LOOKBACK and min(abs(lvl % 100), 100 - abs(lvl % 100)) >= ROUND_GAP]
            longs = [L for L in lows if b.low < L < b.close and prev > L]; shorts = [L for L in highs if b.close < L < b.high and prev < L]
        for L in longs:
            if (d, L, 1) not in used:
                used.add((d, L, 1)); out.append((s, 1, L, b.low))
        for L in shorts:
            if (d, L, -1) not in used:
                used.add((d, L, -1)); out.append((s, -1, L, b.high))
    return sorted(out)


def break_signals_k(bars_k, step=100.0):
    """[(k-index, side, level)] closes through a round level: side = direction of continuation (−1 after closing below)."""
    out = []; used = set()
    for s in range(1, len(bars_k)):
        b = bars_k[s]; prev = bars_k[s - 1].close; d = b.timestamp.date()
        if not (FIRST <= b.timestamp.time() <= LAST):
            continue
        for L in levels_crossed(min(prev, b.close), max(prev, b.close), step):
            side = -1 if b.close < L else 1
            if (d, L, side) not in used:
                used.add((d, L, side)); out.append((s, side, L))
    return out
