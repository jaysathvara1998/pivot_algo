"""Skill 40 — user's VWAP / 9-EMA pullback with rejection-wick filter on 2m bars (SETUP_CHECK.md)."""
from __future__ import annotations

from datetime import time

from engine.emavwap import ema, session_twap

WICK_MULT = 1.5


def pin_signals(bars_k, wick_filter: bool):
    """[(k-index, side, candle_extreme, vwap)] per the supplied rules on k-minute bars."""
    closes = [b.close for b in bars_k]; e9 = ema(closes, 9); tw = session_twap(bars_k); out = []
    for i in range(3, len(bars_k)):
        b = bars_k[i]
        if b.timestamp.time() < time(9, 21) or b.timestamp.time() > time(15, 0):
            continue
        body = abs(b.close - b.open)
        if b.close > tw[i] and b.low <= e9[i]:
            if not wick_filter or ((min(b.open, b.close) - b.low) > WICK_MULT * body and b.close > b.open):
                out.append((i, 1, b.low, tw[i]))
        elif b.close < tw[i] and b.high >= e9[i]:
            if not wick_filter or ((b.high - max(b.open, b.close)) > WICK_MULT * body and b.close < b.open):
                out.append((i, -1, b.high, tw[i]))
    return out
