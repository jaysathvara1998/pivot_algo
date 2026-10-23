"""Skill 37 — directional option buying with index-based stops/targets (HYPOTHESES.md)."""
from __future__ import annotations

import math
import random
import statistics as st
from collections import defaultdict
from datetime import time

from engine.optbuy import price_at, STEP
from engine.premium.ironfly import lot_on
from engine.session.labels import expiry_flags

CAPITAL = 50_000.0; TP_MULT = 2.0; LAST_ENTRY = time(15, 0); TIME_EXIT = time(15, 15); PREM_STOP = 0.30
STRIKES = ("ATM", "OTM1"); STOPS = (1.0, 2.0)


def leg_cost(lot, p_in, p_out):
    return 2 * 20 * 1.18 / lot + 0.0015 * p_out + 0.000353 * 1.18 * (p_in + p_out) + 1.0


def strike_for(idx, atm_row, leg, rule):
    K0 = atm_row["strike"]
    return K0 if rule == "ATM" else (K0 + STEP[idx] if leg == "CE" else K0 - STEP[idx])


def simulate(idx, sess_bars, sess_refs, i_entry, leg, rule, stop_ref, atm, wings, stop_mode="INDEX"):
    """One trade from bar i_entry (close) to exit. Returns dict or 'UNMEASURABLE' or None."""
    bars = sess_bars; c0 = bars[i_entry]; ref = sess_refs[i_entry]
    if not ref or c0.timestamp.time() > LAST_ENTRY:
        return None
    k16 = c0.timestamp.strftime("%Y-%m-%d %H:%M"); row = atm.get(k16)
    if not row:
        return None
    K = strike_for(idx, row, leg, rule); p = price_at(idx, k16, K, leg, atm, wings)
    if p is None or p[1] == "BS":
        return "UNMEASURABLE"
    p_in = p[0] + 0.5; sgn = 1 if leg == "CE" else -1; e = c0.close
    stop_lvl = e - sgn * stop_ref * ref; tp_lvl = e + sgn * TP_MULT * stop_ref * ref
    for j in range(i_entry + 1, len(bars)):
        b = bars[j]; kk = b.timestamp.strftime("%Y-%m-%d %H:%M"); q = price_at(idx, kk, K, leg, atm, wings)
        if q is None or q[1] == "BS":
            return "UNMEASURABLE"
        p_now = q[0] - 0.5
        if stop_mode == "INDEX":
            hit_stop = (b.close <= stop_lvl) if sgn > 0 else (b.close >= stop_lvl)
        else:
            hit_stop = p_now <= (1 - PREM_STOP) * p_in
        hit_tp = (b.close >= tp_lvl) if sgn > 0 else (b.close <= tp_lvl)
        why = "STOP" if hit_stop else "TARGET" if hit_tp else ("TIME" if b.timestamp.time() >= TIME_EXIT else None)
        if why:
            lot = lot_on(idx, c0.timestamp.date()); cost = leg_cost(lot, p_in, p_now); net = p_now - p_in - cost
            return {"date": c0.timestamp.date().isoformat(), "entry": c0.timestamp.strftime("%H:%M"), "exit": b.timestamp.strftime("%H:%M"), "leg": leg, "K": K, "p_in": round(p_in, 2), "p_out": round(p_now, 2),
                    "why": why, "index_move_pts": round(sgn * (b.close - e), 2), "stop_pts": round(stop_ref * ref, 2), "net_unit": round(net, 2), "ret_on_premium": round((p_now - p_in) / p_in, 4),
                    "lot": lot, "lots": 1 if p_in * lot <= 0.25 * CAPITAL else 0, "pnl_inr": round(net * lot * (1 if p_in * lot <= 0.25 * CAPITAL else 0), 0),
                    "expiry_day": bool(expiry_flags(c0.timestamp.date()).get(f"{idx.lower()}_weekly_expiry")), "hit": net > 0}
    return "UNMEASURABLE"


def summarise(trades, baseline, zcrit, min_n=100):
    xs = [t["net_unit"] for t in trades]; n = len(xs)
    if n == 0:
        return {"n": 0, "verdict": "NOT_ENOUGH_DATA"}
    m = sum(xs) / n; sd = st.pstdev(xs) if n > 1 else 0.0; t_stat = m / (sd / math.sqrt(n)) if sd else None
    bx = [t["net_unit"] for t in baseline]; nb = len(bx); mb = sum(bx) / nb if nb else None; sb = st.pstdev(bx) if nb > 1 else None
    z = ((m - mb) / math.sqrt(sd * sd / n + sb * sb / nb)) if (mb is not None and sb and sd) else None
    taken = [t for t in trades if t["lots"]]; eq = CAPITAL; peak = eq; dd = 0.0
    for t in taken:
        eq += t["pnl_inr"]; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    why = defaultdict(int)
    for t in trades:
        why[t["why"]] += 1
    exp = [t for t in trades if t["expiry_day"]]; nexp = [t for t in trades if not t["expiry_day"]]
    verdict = ("NOT_ENOUGH_DATA" if n < min_n else "PROFITABLE_AFTER_COST_DEFLATED" if m > 0 and t_stat is not None and t_stat >= zcrit and z is not None and z >= zcrit
               else "BEATS_BASELINE_NOT_COST" if z is not None and z >= zcrit else "NEGATIVE_AFTER_COST" if m <= 0 else "POSITIVE_UNDEFLATED")
    return {"n": n, "taken": len(taken), "hit_rate": round(sum(1 for t in trades if t["hit"]) / n, 3), "direction_hit_rate": round(sum(1 for t in trades if t["index_move_pts"] > 0) / n, 3),
            "mean_net_unit": round(m, 2), "t": round(t_stat, 2) if t_stat is not None else None, "mean_ret_on_premium_pct": round(100 * sum(t["ret_on_premium"] for t in trades) / n, 2),
            "baseline_mean_net_unit": round(mb, 2) if mb is not None else None, "baseline_n": nb, "z_vs_baseline": round(z, 2) if z is not None else None, "exits": dict(why),
            "mean_p_in": round(sum(t["p_in"] for t in trades) / n, 2), "mean_index_move_pts": round(sum(t["index_move_pts"] for t in trades) / n, 2), "mean_stop_pts": round(sum(t["stop_pts"] for t in trades) / n, 2),
            "net_inr": round(sum(t["pnl_inr"] for t in taken), 0), "final_equity": round(eq, 0), "max_drawdown_pct": round(100 * dd, 1),
            "premium_at_risk_pct_of_capital": round(100 * sum(t["p_in"] * t["lot"] for t in taken) / len(taken) / CAPITAL, 1) if taken else None,
            "expiry_split": {"expiry_day": {"n": len(exp), "mean_net_unit": round(sum(t["net_unit"] for t in exp) / len(exp), 2) if exp else None},
                             "other_days": {"n": len(nexp), "mean_net_unit": round(sum(t["net_unit"] for t in nexp) / len(nexp), 2) if nexp else None}},
            "verdict": verdict}


def simulate_gap(idx, bars, i_entry, leg, rule, width, gap_pts, prev_close, atm, wings, follow=False, no_stop=False):
    """DOB-2 (SETUP_DOB2.md): fade (default) or mirrored follow trade. Target = previous close touched (fade) / entry + gap (follow); stop = entry ± width × gap (fade) /
    previous close touched (follow); time exit 15:15. Returns dict, 'UNMEASURABLE' or None."""
    c0 = bars[i_entry]
    if c0.timestamp.time() > LAST_ENTRY or gap_pts <= 0:
        return None
    k16 = c0.timestamp.strftime("%Y-%m-%d %H:%M"); row = atm.get(k16)
    if not row:
        return None
    K = strike_for(idx, row, leg, rule); p = price_at(idx, k16, K, leg, atm, wings)
    if p is None or p[1] == "BS":
        return "UNMEASURABLE"
    p_in = p[0] + 0.5; sgn = 1 if leg == "CE" else -1; e = c0.close
    for j in range(i_entry + 1, len(bars)):
        b = bars[j]; kk = b.timestamp.strftime("%Y-%m-%d %H:%M"); q = price_at(idx, kk, K, leg, atm, wings)
        if q is None or q[1] == "BS":
            return "UNMEASURABLE"
        p_now = q[0] - 0.5; touched = b.low <= prev_close <= b.high
        if not follow:
            hit_tp = touched; hit_stop = (not no_stop) and ((b.close <= e - width * gap_pts) if sgn > 0 else (b.close >= e + width * gap_pts))
        else:
            hit_tp = (b.close >= e + gap_pts) if sgn > 0 else (b.close <= e - gap_pts); hit_stop = (not no_stop) and touched
        why = "STOP" if hit_stop else "TARGET" if hit_tp else ("TIME" if b.timestamp.time() >= TIME_EXIT else None)
        if why:
            lot = lot_on(idx, c0.timestamp.date()); cost = leg_cost(lot, p_in, p_now); net = p_now - p_in - cost; lots = 1 if p_in * lot <= 0.25 * CAPITAL else 0
            return {"date": c0.timestamp.date().isoformat(), "entry": c0.timestamp.strftime("%H:%M"), "exit": b.timestamp.strftime("%H:%M"), "leg": leg, "K": K, "p_in": round(p_in, 2), "p_out": round(p_now, 2),
                    "why": why, "index_move_pts": round(sgn * (b.close - e), 2), "stop_pts": round(width * gap_pts, 2), "gap_pts": round(gap_pts, 2), "net_unit": round(net, 2),
                    "ret_on_premium": round((p_now - p_in) / p_in, 4), "lot": lot, "lots": lots, "pnl_inr": round(net * lot * lots, 0),
                    "expiry_day": bool(expiry_flags(c0.timestamp.date()).get(f"{idx.lower()}_weekly_expiry")), "hit": net > 0}
    return "UNMEASURABLE"
