"""Daily bars, reference range and labels (PROJECT DEFINITION, Skill 27; see skills/27_daily_behaviour/HYPOTHESES.md)."""
from __future__ import annotations

import csv
import statistics as st
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

REF_SESSIONS = 20
GAP_SMALL = (0.10, 0.30); GAP_LARGE = 0.75
VOL_HIGH, VOL_LOW = 1.6, 0.6


@dataclass
class DayBar:
    d: date
    open: float
    high: float
    low: float
    close: float
    ref: float | None = None            # median range of the previous 20 sessions
    rel_range: float | None = None
    labels: list = field(default_factory=list)


def load_daily(path: Path) -> list:
    out = []
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            out.append(DayBar(date.fromisoformat(r["date"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"])))
    out.sort(key=lambda b: b.d)
    return out


def label_bars(bars: list) -> list:
    """Fills ref, rel_range and labels using only data known at each day's close."""
    closes = [b.close for b in bars]
    for i, b in enumerate(bars):
        if i >= REF_SESSIONS:
            b.ref = st.median(x.high - x.low for x in bars[i - REF_SESSIONS:i])
        if not b.ref:
            continue
        b.rel_range = (b.high - b.low) / b.ref
        L = []
        # streaks of closes vs previous close
        k = 0; j = i
        while j >= 1 and closes[j] > closes[j - 1]:
            k += 1; j -= 1
        if k >= 3:
            L.append("RUN_UP_5+" if k >= 5 else f"RUN_UP_{k}")
        k = 0; j = i
        while j >= 1 and closes[j] < closes[j - 1]:
            k += 1; j -= 1
        if k >= 3:
            L.append("RUN_DOWN_5+" if k >= 5 else f"RUN_DOWN_{k}")
        prev = bars[i - 1]
        if b.rel_range >= 2.0 and prev.rel_range and b.rel_range >= 1.5 * prev.rel_range:
            L.append("RANGE_EXPANSION")
        if b.rel_range <= 0.5:
            L.append("RANGE_COMPRESSION")
        gap = (b.open - prev.close) / prev.close * 100
        if GAP_SMALL[0] <= abs(gap) < GAP_SMALL[1]:
            L.append("GAP_SMALL_UP" if gap > 0 else "GAP_SMALL_DOWN")
        if abs(gap) >= GAP_LARGE:
            L.append("GAP_LARGE_UP" if gap > 0 else "GAP_LARGE_DOWN")
        if b.low <= prev.close <= b.high:
            L.append("GAP_FILLED_TODAY")
        window = closes[i - 20:i]
        if b.close > max(window):
            L.append("NEW_20D_HIGH")
        if b.close < min(window):
            L.append("NEW_20D_LOW")
        rng = b.high - b.low
        if rng > 0:
            loc = (b.close - b.low) / rng
            if loc >= 0.8:
                L.append("CLOSE_TOP_FIFTH")
            elif loc <= 0.2:
                L.append("CLOSE_BOTTOM_FIFTH")
        rr = [x.rel_range for x in bars[i - 10:i] if x.rel_range is not None]
        if len(rr) == 10:
            m = sum(rr) / 10
            if m > VOL_HIGH:
                L.append("VOL_HIGH")
            elif m < VOL_LOW:
                L.append("VOL_LOW")
        b.labels = L
    return bars


LABELS = ("RUN_UP_3", "RUN_UP_4", "RUN_UP_5+", "RUN_DOWN_3", "RUN_DOWN_4", "RUN_DOWN_5+", "RANGE_EXPANSION", "RANGE_COMPRESSION", "GAP_SMALL_UP", "GAP_SMALL_DOWN",
          "GAP_LARGE_UP", "GAP_LARGE_DOWN", "GAP_FILLED_TODAY", "NEW_20D_HIGH", "NEW_20D_LOW", "CLOSE_TOP_FIFTH", "CLOSE_BOTTOM_FIFTH", "VOL_HIGH", "VOL_LOW")
EXPECTED_DIRECTION = {"RUN_DOWN_3": "UP", "RUN_DOWN_4": "UP", "RUN_DOWN_5+": "UP", "RUN_UP_3": "DOWN", "RUN_UP_4": "DOWN", "RUN_UP_5+": "DOWN",
                      "GAP_SMALL_UP": "DOWN", "GAP_LARGE_UP": "DOWN", "GAP_SMALL_DOWN": "UP", "GAP_LARGE_DOWN": "UP", "NEW_20D_HIGH": "UP", "NEW_20D_LOW": "DOWN",
                      "CLOSE_TOP_FIFTH": "UP", "CLOSE_BOTTOM_FIFTH": "DOWN"}
