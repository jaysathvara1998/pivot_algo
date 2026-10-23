"""Price-action detectors on plain OHLC(V) arrays (PROJECT DEFINITIONS, Skill 33 — PRICE_ACTION_CURRICULUM.md §2). Every detector returns a list of
Signal(i, direction, setup, ctx) where i is the bar at whose CLOSE the signal is known; outcomes are measured from bar i onward."""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass, field

LOOK = 20; SWEEP_MARGIN = 0.5; FVG_MIN = 1.5; OB_MIN_MOVE = 2.0; TIGHT = 4.0; FAIL_WITHIN = 5; MM_WITHIN = 20      # TIGHT 2.0 → 4.0 by amendment A1 (2026-10-06)


@dataclass
class Signal:
    i: int
    direction: str          # UP | DOWN
    setup: str
    ctx: dict = field(default_factory=dict)


class Bars:
    """Arrays with per-bar reference range (median range of the prior 20 bars), confirmed fractal swings and a simple structure state."""

    def __init__(self, o, h, l, c, v=None, session=None):
        self.o, self.h, self.l, self.c = o, h, l, c; self.v = v; self.n = len(c)
        self.session = session if session is not None else [0] * self.n
        self.ref = [None] * self.n
        for i in range(LOOK, self.n):
            rs = [h[k] - l[k] for k in range(i - LOOK, i) if h[k] is not None and l[k] is not None]
            self.ref[i] = st.median(rs) if len(rs) >= LOOK // 2 and max(rs) > 0 else None
        self.sw_hi, self.sw_lo = self._swings()
        self.state = self._structure()

    def _swings(self, n=3):
        hi = []; lo = []
        for i in range(n, self.n - n):
            if all(self.h[i] > self.h[k] for k in range(i - n, i)) and all(self.h[i] > self.h[k] for k in range(i + 1, i + n + 1)):
                hi.append((i, i + n, self.h[i]))                      # (pivot bar, confirmation bar, level)
            if all(self.l[i] < self.l[k] for k in range(i - n, i)) and all(self.l[i] < self.l[k] for k in range(i + 1, i + n + 1)):
                lo.append((i, i + n, self.l[i]))
        return hi, lo

    def last_swings(self, i, kind, k=2):
        """The last k confirmed swings (confirmation ≤ i) of kind 'hi' | 'lo'."""
        arr = self.sw_hi if kind == "hi" else self.sw_lo
        out = [s for s in arr if s[1] <= i]
        return out[-k:]

    def _structure(self):
        state = ["UNDEFINED"] * self.n; hi_i = lo_i = 0; lh = []; ll = []
        for i in range(self.n):
            while hi_i < len(self.sw_hi) and self.sw_hi[hi_i][1] <= i:
                lh.append(self.sw_hi[hi_i][2]); hi_i += 1
            while lo_i < len(self.sw_lo) and self.sw_lo[lo_i][1] <= i:
                ll.append(self.sw_lo[lo_i][2]); lo_i += 1
            if len(lh) >= 2 and len(ll) >= 2:
                hh = lh[-1] > lh[-2]; hl = ll[-1] > ll[-2]
                state[i] = "UP" if hh and hl else "DOWN" if (not hh and not hl) else "RANGE"
        return state

    def same_session(self, i, j):
        return 0 <= j < self.n and self.session[j] == self.session[i]

    def prior_extremes(self, i):
        hs = [self.h[k] for k in range(max(0, i - LOOK), i)]; ls = [self.l[k] for k in range(max(0, i - LOOK), i)]
        return (max(hs), min(ls)) if len(hs) == LOOK else (None, None)

    def range_position(self, i, look=50):
        hs = [self.h[k] for k in range(max(0, i - look), i)]; ls = [self.l[k] for k in range(max(0, i - look), i)]
        if len(hs) < look or max(hs) == min(ls):
            return None
        p = (self.c[i] - min(ls)) / (max(hs) - min(ls))
        return "LOWER" if p < 1 / 3 else "UPPER" if p > 2 / 3 else "MIDDLE"

    def levels_support(self, i, pdl=None):
        _, lo20 = self.prior_extremes(i); sw = self.last_swings(i, "lo", 1)
        out = [x for x in (lo20, sw[0][2] if sw else None, pdl) if x is not None]
        return out

    def levels_resistance(self, i, pdh=None):
        hi20, _ = self.prior_extremes(i); sw = self.last_swings(i, "hi", 1)
        return [x for x in (hi20, sw[0][2] if sw else None, pdh) if x is not None]


def _ctx(b, i):
    return {"state": b.state[i], "pos": b.range_position(i)}


def detect_sweep(b: Bars):
    out = []
    for i in range(LOOK, b.n):
        r = b.ref[i]; hi20, lo20 = b.prior_extremes(i)
        if not r or hi20 is None or not b.same_session(i, i - LOOK):
            continue
        if b.h[i] > hi20 and (b.h[i] - hi20) < SWEEP_MARGIN * r and b.c[i] < hi20:
            out.append(Signal(i, "DOWN", "P1_SWEEP_HIGH", _ctx(b, i)))
        if b.l[i] < lo20 and (lo20 - b.l[i]) < SWEEP_MARGIN * r and b.c[i] > lo20:
            out.append(Signal(i, "UP", "P2_SWEEP_LOW", _ctx(b, i)))
    return out


def detect_break_retest(b: Bars):
    out = []
    for kind, setup, direction in (("hi", "P3_BREAK_RETEST_UP", "UP"), ("lo", "P4_BREAK_RETEST_DOWN", "DOWN")):
        for i in range(LOOK, b.n):
            sw = b.last_swings(i - 1, kind, 1)
            if not sw or not b.ref[i]:
                continue
            L = sw[0][2]
            broke = (b.c[i] > L and b.c[i - 1] <= L) if kind == "hi" else (b.c[i] < L and b.c[i - 1] >= L)
            if not broke:
                continue
            for j in range(i + 1, min(b.n, i + 1 + LOOK)):
                if not b.same_session(i, j):
                    break
                if (b.c[j] < L) if kind == "hi" else (b.c[j] > L):
                    break                                                 # failed break: no retest signal
                touched = (b.l[j] <= L) if kind == "hi" else (b.h[j] >= L)
                if touched:
                    out.append(Signal(j, direction, setup, {**_ctx(b, j), "level": L, "bars_since_break": j - i})); break
    return out


def detect_fvg(b: Bars):
    out = []; fills = {"P5_FVG_BULL": [0, 0], "P6_FVG_BEAR": [0, 0]}
    for i in range(LOOK + 1, b.n - 1):
        r = b.ref[i]
        if not r or (b.h[i] - b.l[i]) < FVG_MIN * r or not b.same_session(i, i + 1):
            continue
        if b.l[i + 1] > b.h[i - 1]:                                       # bullish imbalance between h[i-1] and l[i+1]
            top = b.l[i + 1]; fills["P5_FVG_BULL"][1] += 1
            for j in range(i + 2, min(b.n, i + 2 + LOOK)):
                if not b.same_session(i, j):
                    break
                if b.l[j] <= top:
                    fills["P5_FVG_BULL"][0] += 1; out.append(Signal(j, "UP", "P5_FVG_BULL", {**_ctx(b, j), "bars_to_fill": j - i})); break
        if b.h[i + 1] < b.l[i - 1]:
            bot = b.h[i + 1]; fills["P6_FVG_BEAR"][1] += 1
            for j in range(i + 2, min(b.n, i + 2 + LOOK)):
                if not b.same_session(i, j):
                    break
                if b.h[j] >= bot:
                    fills["P6_FVG_BEAR"][0] += 1; out.append(Signal(j, "DOWN", "P6_FVG_BEAR", {**_ctx(b, j), "bars_to_fill": j - i})); break
    return out, fills


def detect_order_block(b: Bars):
    out = []
    for i in range(LOOK, b.n - 4):
        r = b.ref[i]
        if not r or not b.same_session(i, i + 3):
            continue
        if b.c[i] < b.o[i] and all(b.c[k] > b.o[k] for k in (i + 1, i + 2, i + 3)) and (b.c[i + 3] - b.c[i]) >= OB_MIN_MOVE * r:
            lo, hi = b.l[i], b.h[i]
            for j in range(i + 4, min(b.n, i + 4 + 30)):
                if not b.same_session(i, j) or b.c[j] < lo:
                    break
                if b.l[j] <= hi:
                    out.append(Signal(j, "UP", "P7_ORDER_BLOCK_DEMAND", {**_ctx(b, j), "bars_to_return": j - i})); break
        if b.c[i] > b.o[i] and all(b.c[k] < b.o[k] for k in (i + 1, i + 2, i + 3)) and (b.c[i] - b.c[i + 3]) >= OB_MIN_MOVE * r:
            lo, hi = b.l[i], b.h[i]
            for j in range(i + 4, min(b.n, i + 4 + 30)):
                if not b.same_session(i, j) or b.c[j] > hi:
                    break
                if b.h[j] >= lo:
                    out.append(Signal(j, "DOWN", "P8_ORDER_BLOCK_SUPPLY", {**_ctx(b, j), "bars_to_return": j - i})); break
    return out


def _is_pin(b, i, side):
    rng = b.h[i] - b.l[i]; body = abs(b.c[i] - b.o[i])
    if rng <= 0:
        return False
    wick = (min(b.o[i], b.c[i]) - b.l[i]) if side == "lower" else (b.h[i] - max(b.o[i], b.c[i]))
    return wick >= 0.6 * rng and wick >= 2 * body


def detect_pin_engulf(b: Bars, pdh=None, pdl=None):
    out = []
    for i in range(LOOK + 1, b.n):
        if not b.ref[i]:
            continue
        sup = b.levels_support(i, pdl[i] if pdl else None); res = b.levels_resistance(i, pdh[i] if pdh else None)
        at_sup = any(b.l[i] <= L <= b.h[i] for L in sup); at_res = any(b.l[i] <= L <= b.h[i] for L in res)
        if at_sup and _is_pin(b, i, "lower"):
            out.append(Signal(i, "UP", "P9_PIN_AT_SUPPORT", _ctx(b, i)))
        if at_res and _is_pin(b, i, "upper"):
            out.append(Signal(i, "DOWN", "P10_PIN_AT_RESISTANCE", _ctx(b, i)))
        po, pc = b.o[i - 1], b.c[i - 1]
        if at_sup and pc < po and b.c[i] > b.o[i] and b.o[i] <= min(po, pc) and b.c[i] >= max(po, pc):
            out.append(Signal(i, "UP", "P11_ENGULF_AT_SUPPORT", _ctx(b, i)))
        if at_res and pc > po and b.c[i] < b.o[i] and b.o[i] >= max(po, pc) and b.c[i] <= min(po, pc):
            out.append(Signal(i, "DOWN", "P12_ENGULF_AT_RESISTANCE", _ctx(b, i)))
    return out


def detect_inside_break(b: Bars):
    out = []
    for i in range(LOOK + 1, b.n):
        if not b.ref[i] or not (b.h[i] <= b.h[i - 1] and b.l[i] >= b.l[i - 1]):
            continue
        mh, ml = b.h[i - 1], b.l[i - 1]
        for j in range(i + 1, min(b.n, i + 6)):
            if not b.same_session(i, j):
                break
            if b.c[j] > mh:
                out.append(Signal(j, "UP", "P13_INSIDE_BAR_BREAK", _ctx(b, j))); break
            if b.c[j] < ml:
                out.append(Signal(j, "DOWN", "P13_INSIDE_BAR_BREAK", _ctx(b, j))); break
    return out


def detect_tight_range_breakout(b: Bars):
    """Signals carry the breakout direction in ctx; the registered claim is FADE (direction field = opposite). Also failure and measured-move outcomes."""
    out = []
    for i in range(LOOK, b.n):
        r = b.ref[i]; hi20, lo20 = b.prior_extremes(i)
        if not r or hi20 is None or (hi20 - lo20) > TIGHT * r or not b.same_session(i, i - LOOK):
            continue
        up = b.c[i] > hi20; dn = b.c[i] < lo20
        if not (up or dn):
            continue
        height = hi20 - lo20; failed = None; mm = None
        for j in range(i + 1, min(b.n, i + 1 + MM_WITHIN)):
            if not b.same_session(i, j):
                break
            if j <= i + FAIL_WITHIN and failed is None and ((b.c[j] < hi20) if up else (b.c[j] > lo20)):
                failed = True
            if mm is None and ((b.h[j] >= hi20 + height) if up else (b.l[j] <= lo20 - height)):
                mm = True
        out.append(Signal(i, "DOWN" if up else "UP", "P14_TIGHT_RANGE_BREAKOUT", {**_ctx(b, i), "breakout": "UP" if up else "DOWN", "failed_within_5": bool(failed), "measured_move_hit": bool(mm)}))
    return out


def detect_two_leg_pullback(b: Bars):
    out = []
    for conf_i, conf_bar, lvl in b.sw_lo:
        i = conf_bar
        if i >= b.n or not b.ref[i] or b.state[i] != "UP":
            continue
        his = b.last_swings(i, "hi", 1); los = b.last_swings(i, "lo", 3)
        if not his or len(los) < 3:
            continue
        last_hi_bar = his[0][0]
        legs = [s for s in los if s[0] > last_hi_bar]
        if len(legs) >= 2 and legs[-1][2] < legs[-2][2] and legs[-1][2] > los[0][2] and b.c[i] > b.h[i - 1]:
            out.append(Signal(i, "UP", "P15_TWO_LEG_PULLBACK", _ctx(b, i)))
    return out


def detect_trend_bar(b: Bars):
    out = []
    for i in range(LOOK, b.n):
        r = b.ref[i]; rng = b.h[i] - b.l[i]
        if not r or rng < 1.5 * r:
            continue
        if abs(b.c[i] - b.o[i]) >= 0.8 * rng:
            out.append(Signal(i, "UP" if b.c[i] > b.o[i] else "DOWN", "P16_TREND_BAR", _ctx(b, i)))
    return out


def detect_volume_setups(b: Bars):
    """Stocks only: spring, upthrust, selling climax."""
    out = []
    if b.v is None:
        return out
    for i in range(LOOK, b.n - 3):
        vs = [b.v[k] for k in range(i - LOOK, i) if b.v[k] is not None]
        if len(vs) < LOOK // 2 or b.v[i] is None or not b.ref[i]:
            continue
        avg = sum(vs) / len(vs); hi20, lo20 = b.prior_extremes(i)
        if hi20 is None:
            continue
        if b.c[i] < lo20 and avg > 0 and b.v[i] <= 0.7 * avg:
            for j in range(i + 1, i + 4):
                if b.c[j] > lo20:
                    out.append(Signal(j, "UP", "P17_SPRING", _ctx(b, j))); break
        if b.c[i] > hi20 and avg > 0 and b.v[i] >= 1.5 * avg:
            for j in range(i + 1, i + 4):
                if b.c[j] < hi20:
                    out.append(Signal(j, "DOWN", "P18_UPTHRUST", _ctx(b, j))); break
        if (b.h[i] - b.l[i]) >= 2 * b.ref[i] and avg > 0 and b.v[i] >= 3 * avg and b.c[i] <= lo20:
            out.append(Signal(i, "UP", "P19_SELLING_CLIMAX", _ctx(b, i)))
    return out


SETUPS = ("P1_SWEEP_HIGH", "P2_SWEEP_LOW", "P3_BREAK_RETEST_UP", "P4_BREAK_RETEST_DOWN", "P5_FVG_BULL", "P6_FVG_BEAR", "P7_ORDER_BLOCK_DEMAND", "P8_ORDER_BLOCK_SUPPLY",
          "P9_PIN_AT_SUPPORT", "P10_PIN_AT_RESISTANCE", "P11_ENGULF_AT_SUPPORT", "P12_ENGULF_AT_RESISTANCE", "P13_INSIDE_BAR_BREAK", "P14_TIGHT_RANGE_BREAKOUT",
          "P15_TWO_LEG_PULLBACK", "P16_TREND_BAR", "P17_SPRING", "P18_UPTHRUST", "P19_SELLING_CLIMAX")


def detect_all(b: Bars, pdh=None, pdl=None) -> tuple:
    sigs = []; sigs += detect_sweep(b); sigs += detect_break_retest(b); f, fills = detect_fvg(b); sigs += f; sigs += detect_order_block(b)
    sigs += detect_pin_engulf(b, pdh, pdl); sigs += detect_inside_break(b); sigs += detect_tight_range_breakout(b); sigs += detect_two_leg_pullback(b); sigs += detect_trend_bar(b)
    sigs += detect_volume_setups(b)
    return sigs, fills
