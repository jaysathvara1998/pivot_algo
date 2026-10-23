"""Skill 41 — the SMC / ICT-2022 sequence as one mechanical detector: 15m bias → liquidity sweep → displacement with FVG and structure break → FVG retest entry → stop at the sweep extreme → target 2R or the opposing pool."""
from __future__ import annotations

from datetime import time, timedelta

from engine.ohlc.aggregate import aggregate
from engine.pa2 import Bars

DISP_MULT, RETEST_BARS, POOL_LOOKBACK = 1.5, 13, 150
FIRST_ENTRY, LAST_ENTRY, TIME_EXIT = time(9, 30), time(15, 0), time(15, 15)


def bias_series(bars1m, bars_k):
    """15m structure state in force at each k-bar (state of the last completed 15m bar)."""
    c15 = list(aggregate(bars1m, 15)); b15 = Bars([c.open for c in c15], [c.high for c in c15], [c.low for c in c15], [c.close for c in c15])
    ends = [(c.timestamp + timedelta(minutes=15), b15.state[i]) for i, c in enumerate(c15)]; out = []; j = 0; cur = "UNDEFINED"
    for b in bars_k:
        while j < len(ends) and ends[j][0] <= b.timestamp:
            cur = ends[j][1]; j += 1
        out.append(cur)
    return out


def prev_day_levels(bars_k):
    """(prev-day low, prev-day high) in force at each k-bar."""
    out = []; cur = None; lo = hi = None; plo = phi = None
    for b in bars_k:
        if b.timestamp.date() != cur:
            if cur is not None:
                plo, phi = lo, hi
            cur = b.timestamp.date(); lo, hi = b.low, b.high
        lo = min(lo, b.low); hi = max(hi, b.high); out.append((plo, phi))
    return out


def smc_signals_k(bars_k, bias, require_fvg=True, require_bias=True, pd_levels=None):
    """[(k-index of entry, side, stop_level, pool_target, sweep_index, fvg_zone)] on k-bars."""
    B = Bars([b.open for b in bars_k], [b.high for b in bars_k], [b.low for b in bars_k], [b.close for b in bars_k]); n = len(bars_k)
    lows = sorted(B.sw_lo, key=lambda x: x[1]); highs = sorted(B.sw_hi, key=lambda x: x[1]); out = []; used = set(); pd = pd_levels or [(None, None)] * n
    for side in (1, -1):
        pools_src = lows if side > 0 else highs
        for s in range(25, n - 3):
            b = bars_k[s]
            if require_bias and bias[s] != ("UP" if side > 0 else "DOWN"):
                continue
            # candidate pools: confirmed before s, formed within lookback, penetrated by this bar's extreme but not by its close
            cands = [(lvl, ("sw", pi)) for pi, ci, lvl in pools_src if ci <= s - 1 and s - pi <= POOL_LOOKBACK]
            pdl, pdh = pd[s]
            if side > 0 and pdl is not None:
                cands.append((pdl, ("pd", s)))
            if side < 0 and pdh is not None:
                cands.append((pdh, ("pd", s)))
            prev_close = bars_k[s - 1].close
            if side > 0:
                cands = [(lvl, key) for lvl, key in cands if lvl < prev_close and b.low < lvl < b.close and key not in used]
                if not cands:
                    continue
                lvl, key = max(cands)                                   # nearest pool below
            else:
                cands = [(lvl, key) for lvl, key in cands if lvl > prev_close and b.high > lvl > b.close and key not in used]
                if not cands:
                    continue
                lvl, key = min(cands)
            # displacement with FVG + structure break within s..s+3
            local_ext = max(bars_k[x].high for x in range(s - 3, s)) if side > 0 else min(bars_k[x].low for x in range(s - 3, s))
            found = None
            for j in range(s, min(s + 4, n - 1)):
                bj = bars_k[j]; ref = B.ref[j]
                if not ref:
                    continue
                big = (bj.high - bj.low) >= DISP_MULT * ref and ((bj.close > bj.open) if side > 0 else (bj.close < bj.open))
                if side > 0:
                    fvg = bars_k[j + 1].low > bars_k[j - 1].high; bos = bars_k[j + 1].close > local_ext; zone = (bars_k[j - 1].high, bars_k[j + 1].low)
                else:
                    fvg = bars_k[j + 1].high < bars_k[j - 1].low; bos = bars_k[j + 1].close < local_ext; zone = (bars_k[j + 1].high, bars_k[j - 1].low)
                if big and bos and (fvg or not require_fvg):
                    if not fvg:
                        zone = (bj.low, bj.open) if side > 0 else (bj.open, bj.high)       # no-FVG variant: the displacement bar's body as the retest zone
                    found = (j, zone); break
            if not found:
                continue
            j, (zlo, zhi) = found; stop = b.low if side > 0 else b.high; entry = None
            for m in range(j + 2, min(j + 2 + RETEST_BARS, n)):
                bm = bars_k[m]
                if (side > 0 and bm.close < stop) or (side < 0 and bm.close > stop):
                    break
                touch = (bm.low <= zhi and bm.close > zlo) if side > 0 else (bm.high >= zlo and bm.close < zhi)
                if touch:
                    entry = m; break
            if entry is None:
                continue
            t = bars_k[entry].timestamp.time()
            if not (FIRST_ENTRY <= t <= LAST_ENTRY):
                continue
            e = bars_k[entry].close
            if side > 0:
                above = [lvl for pi, ci, lvl in highs if ci <= entry and lvl > e]; pool_t = min(above) if above else None
            else:
                below = [lvl for pi, ci, lvl in lows if ci <= entry and lvl < e]; pool_t = max(below) if below else None
            used.add(key); out.append((entry, side, stop, pool_t, s, (zlo, zhi)))
    return sorted(out)


def level_trade(bars1m, refs, i, side, stop, target):
    """Index trade from bar i's close with fixed stop/target levels (first 1m close beyond), 15:15 time exit."""
    c0 = bars1m[i]; R = refs[i]; e = c0.close; d0 = c0.timestamp.date()
    if not R or side * (e - stop) <= 0:
        return None
    for j in range(i + 1, len(bars1m)):
        b = bars1m[j]
        if b.timestamp.date() != d0:
            break
        mv = side * (b.close - e); why = None
        if side * (b.close - stop) <= 0:
            why = "STOP"
        elif target is not None and side * (b.close - target) >= 0:
            why = "TARGET"
        elif b.timestamp.time() >= TIME_EXIT:
            why = "TIME"
        if why:
            return {"date": d0.isoformat(), "entry": c0.timestamp.strftime("%H:%M"), "exit": b.timestamp.strftime("%H:%M"), "dir": side, "move_pts": round(mv, 2), "move_R": round(mv / R, 3), "why": why,
                    "hold_min": j - i, "risk_pts": round(side * (e - stop), 2), "risk_R": round(side * (e - stop) / R, 2), "exit_j": j}
    return None
