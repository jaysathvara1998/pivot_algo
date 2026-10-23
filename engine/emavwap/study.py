"""Skill 38 — fast EMA crossover with a VWAP (time-weighted proxy) filter on 1m / 2m index bars; index-side trades and the weekly-option expression."""
from __future__ import annotations

import math
import statistics as st
from collections import defaultdict
from datetime import time, timedelta

from engine.ohlc.aggregate import aggregate
from engine.optbuy import price_at
from engine.premium.ironfly import lot_on

FAST, SLOW, WARM = 9, 21, 60
FIRST_ENTRY, LAST_ENTRY, TIME_EXIT = time(9, 20), time(15, 0), time(15, 15)
CAPITAL = 50_000.0


def ema(xs, n):
    k = 2.0 / (n + 1); out = []; e = None
    for x in xs:
        e = x if e is None else e + k * (x - e); out.append(e)
    return out


def session_twap(bars):
    """Cumulative mean of the typical price within each session (VWAP proxy; resets on date change)."""
    out = []; cur = None; s = 0.0; k = 0
    for b in bars:
        if b.timestamp.date() != cur:
            cur = b.timestamp.date(); s = 0.0; k = 0
        s += (b.high + b.low + b.close) / 3.0; k += 1; out.append(s / k)
    return out


def crossovers(bars_k, vwap_filter: bool, time_filter: bool = False):
    """[(k-bar index, +1/-1, vwap_ok, raw)] for every raw crossover; filtered flags are attached so one pass serves both variants."""
    closes = [b.close for b in bars_k]; ef = ema(closes, FAST); es = ema(closes, SLOW); tw = session_twap(bars_k); out = []
    for i in range(max(WARM, 1), len(bars_k)):
        up = ef[i - 1] <= es[i - 1] and ef[i] > es[i]; dn = ef[i - 1] >= es[i - 1] and ef[i] < es[i]
        if not (up or dn):
            continue
        d = 1 if up else -1; b = bars_k[i]
        ok_v = (b.close > tw[i]) if d > 0 else (b.close < tw[i])
        t = b.timestamp.time(); ok_t = (time(9, 30) <= t < time(11, 30)) or (time(13, 0) <= t < time(15, 0))
        if vwap_filter and not ok_v:
            continue
        if time_filter and not ok_t:
            continue
        out.append((i, d))
    return out


def kbars(bars1m, k):
    return bars1m if k == 1 else list(aggregate(bars1m, k))


def entry_index(pos, bars_k, i, k):
    """1m bar whose close is the k-bar's close."""
    b = bars_k[i]; t = b.timestamp + timedelta(minutes=k - 1)
    return pos.get((t.date(), t.strftime("%H:%M")))


def index_trade(bars1m, refs, i, d, exit_rule, stop_hi_lo, next_opp_minute):
    """Walk the index from the entry bar close. exit_rule 'X' (opposite raw crossover at minute next_opp_minute or 15:15) or 'B' (stop at the crossover bar's extreme ≥ 1 R, target 2× stop, 15:15)."""
    c0 = bars1m[i]; t0 = c0.timestamp.time()
    if not (FIRST_ENTRY <= t0 <= LAST_ENTRY) or not refs[i]:
        return None
    e = c0.close; R = refs[i]
    if exit_rule == "B":
        stop_d = max(R, (e - stop_hi_lo) if d > 0 else (stop_hi_lo - e)); tgt_d = 2 * stop_d
    d0 = c0.timestamp.date()
    for j in range(i + 1, len(bars1m)):
        b = bars1m[j]
        if b.timestamp.date() != d0:
            break
        mv = d * (b.close - e); why = None
        if exit_rule == "X":
            if next_opp_minute is not None and b.timestamp >= next_opp_minute:
                why = "OPPOSITE_CROSS"
        else:
            if mv <= -stop_d:
                why = "STOP"
            elif mv >= tgt_d:
                why = "TARGET"
        if why is None and b.timestamp.time() >= TIME_EXIT:
            why = "TIME"
        if why:
            return {"date": d0.isoformat(), "entry": c0.timestamp.strftime("%H:%M"), "exit": b.timestamp.strftime("%H:%M"), "dir": d, "move_pts": round(mv, 2), "move_R": round(mv / R, 3), "why": why,
                    "hold_min": j - i, "stop_pts": round(stop_d, 2) if exit_rule == "B" else None, "exit_j": j}
    return None


def forward_moves(bars1m, refs, i, d, horizons=(5, 15, 30, 60)):
    out = {}; e = bars1m[i].close; R = refs[i]; d0 = bars1m[i].timestamp.date()
    for h in horizons:
        j = i + h
        if j < len(bars1m) and bars1m[j].timestamp.date() == d0 and R:
            out[h] = d * (bars1m[j].close - e) / R
    return out


def leg_cost(lot, p_in, p_out):
    return 2 * 20 * 1.18 / lot + 0.0015 * p_out + 0.000353 * 1.18 * (p_in + p_out) + 1.0


def option_trade(idx, bars1m, i, d, exit_j, why, atm, wings):
    """Price the index trade's option expression: ATM CE (long) / PE (short) bought at bar i's minute, sold at bar exit_j's minute."""
    leg = "CE" if d > 0 else "PE"; k_in = bars1m[i].timestamp.strftime("%Y-%m-%d %H:%M"); row = atm.get(k_in)
    if not row:
        return None
    K = row["strike"]; p = price_at(idx, k_in, K, leg, atm, wings)
    if p is None or p[1] == "BS":
        return "UNMEASURABLE"
    k_out = bars1m[exit_j].timestamp.strftime("%Y-%m-%d %H:%M"); q = price_at(idx, k_out, K, leg, atm, wings)
    if q is None or q[1] == "BS":
        return "UNMEASURABLE"
    p_in = p[0] + 0.5; p_out = q[0] - 0.5; lot = lot_on(idx, bars1m[i].timestamp.date()); cost = leg_cost(lot, p_in, p_out); net = p_out - p_in - cost
    lots = 1 if p_in * lot <= 0.25 * CAPITAL else 0
    return {"date": bars1m[i].timestamp.date().isoformat(), "entry": bars1m[i].timestamp.strftime("%H:%M"), "exit": bars1m[exit_j].timestamp.strftime("%H:%M"), "leg": leg, "K": K, "p_in": round(p_in, 2), "p_out": round(p_out, 2),
            "why": why, "net_unit": round(net, 2), "lot": lot, "lots": lots, "pnl_inr": round(net * lot * lots, 0), "hit": net > 0}


def index_summary(trades, null_trades, zcrit, horizons=(5, 15, 30, 60), fwd=None):
    n = len(trades)
    if n == 0:
        return {"n": 0, "status": "NOT_ENOUGH_DATA"}
    mv = [t["move_R"] for t in trades]; m = st.mean(mv); sd = st.pstdev(mv) if n > 1 else 0.0; se = sd / math.sqrt(n) if n > 1 else None
    n0 = len(null_trades); m0 = st.mean([t["move_R"] for t in null_trades]) if n0 else None; sd0 = st.pstdev([t["move_R"] for t in null_trades]) if n0 > 1 else None
    z = ((m - m0) / math.sqrt(sd * sd / n + sd0 * sd0 / n0)) if (m0 is not None and sd0 and sd) else None
    ex = defaultdict(int)
    for t in trades:
        ex[t["why"]] += 1
    out = {"n": n, "per_session": None, "direction_hit_rate": round(sum(1 for t in trades if t["move_pts"] > 0) / n, 3), "mean_move_R": round(m, 3), "ci95_move_R": [round(m - 1.96 * se, 3), round(m + 1.96 * se, 3)] if se else None,
           "mean_move_pts": round(st.mean(t["move_pts"] for t in trades), 2), "median_hold_min": st.median(t["hold_min"] for t in trades), "exits": dict(ex),
           "null_n": n0, "null_mean_move_R": round(m0, 3) if m0 is not None else None, "null_direction_hit_rate": round(sum(1 for t in null_trades if t["move_pts"] > 0) / n0, 3) if n0 else None, "z_vs_null": round(z, 2) if z is not None else None}
    if fwd:
        out["forward"] = {str(h): {"n": len(v), "hit": round(sum(1 for x in v if x > 0) / len(v), 3), "mean_R": round(st.mean(v), 3)} for h, v in fwd.items() if v}
    out["status"] = "SUPPORTED" if (z is not None and z >= zcrit and m > 0 and se and m - 1.96 * se > 0) else "NOT_SUPPORTED"
    return out


def option_summary(trades, baseline, zcrit, min_n=100):
    xs = [t["net_unit"] for t in trades]; n = len(xs)
    if n == 0:
        return {"n": 0, "verdict": "NOT_ENOUGH_DATA"}
    m = st.mean(xs); sd = st.pstdev(xs) if n > 1 else 0.0; t_stat = m / (sd / math.sqrt(n)) if sd else None
    bx = [t["net_unit"] for t in baseline]; nb = len(bx); mb = st.mean(bx) if nb else None; sb = st.pstdev(bx) if nb > 1 else None
    z = ((m - mb) / math.sqrt(sd * sd / n + sb * sb / nb)) if (mb is not None and sb and sd) else None
    taken = [t for t in trades if t["lots"]]; eq = CAPITAL; peak = eq; dd = 0.0
    for t in taken:
        eq += t["pnl_inr"]; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    ex = defaultdict(int)
    for t in trades:
        ex[t["why"]] += 1
    verdict = ("NOT_ENOUGH_DATA" if n < min_n else "PROFITABLE_AFTER_COST_DEFLATED" if (m > 0 and t_stat is not None and t_stat >= zcrit and z is not None and z >= zcrit)
               else "BEATS_BASELINE_NOT_COST" if (z is not None and z >= zcrit) else "NEGATIVE_AFTER_COST" if m <= 0 else "POSITIVE_UNDEFLATED")
    return {"n": n, "taken": len(taken), "hit_rate": round(sum(t["hit"] for t in trades) / n, 3), "mean_net_unit": round(m, 2), "t": round(t_stat, 2) if t_stat is not None else None, "mean_p_in": round(st.mean(t["p_in"] for t in trades), 2),
            "baseline_n": nb, "baseline_mean_net_unit": round(mb, 2) if mb is not None else None, "z_vs_baseline": round(z, 2) if z is not None else None, "exits": dict(ex),
            "net_inr": round(sum(t["pnl_inr"] for t in taken), 0), "final_equity": round(eq, 0), "max_drawdown_pct": round(100 * dd, 1), "floor_priced": sum(1 for t in trades if t.get("floor_priced")), "verdict": verdict}
