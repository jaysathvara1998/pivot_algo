"""Pre-registered daily study: behaviour cells vs a vol-matched daily random walk, and open-based trade cells with a cost gate (Skill 27)."""
from __future__ import annotations

import math
import random
from collections import defaultdict
from datetime import date, timedelta

from engine.setups import bonferroni_z
from .bars import DayBar, label_bars, LABELS, EXPECTED_DIRECTION
from .split import daily_window_of, HALVES

OUTCOMES = ("next_up_close", "next_up_open", "next5_up_close", "next5_up_open", "next_large")
COST_FRACTION = {"current": 0.0006, "development_era": 0.0003}       # of index level per round trip (card 26: ≈ ₹966 on ≈ ₹16 lakh notional)
MIN_N = 100
STATUS_RULE = {"min_abs_z": 3.0, "min_abs_effect": 0.02}


def _outcome(bars, i, key):
    n = len(bars)
    if key == "next_up_close":
        return (bars[i + 1].close > bars[i].close) if i + 1 < n else None
    if key == "next_up_open":
        return (bars[i + 2].open > bars[i + 1].open) if i + 2 < n else None
    if key == "next5_up_close":
        return (bars[i + 5].close > bars[i].close) if i + 5 < n else None
    if key == "next5_up_open":
        return (bars[i + 6].open > bars[i + 1].open) if i + 6 < n else None
    if key == "next_large":
        return (bars[i + 1].rel_range is not None and bars[i + 1].rel_range >= 2.0) if i + 1 < n else None
    return None


def _move(bars, i, horizon, direction):
    """Open-based executable move in points in `direction`: enter open_{i+1}, exit open_{i+1+horizon}."""
    if i + 1 + horizon >= len(bars):
        return None, None
    e = bars[i + 1].open; x = bars[i + 1 + horizon].open
    return ((x - e) if direction == "UP" else (e - x)), e


def _rates(bars, idx_list, key, stride):
    k = n = 0; last = -10 ** 9
    for i in idx_list:
        if i <= last + stride - 1:
            continue
        o = _outcome(bars, i, key)
        if o is None:
            continue
        n += 1; k += bool(o); last = i
    return k, n


def _binom_z(k, n, p0):
    if n == 0 or p0 <= 0 or p0 >= 1:
        return None
    return (k / n - p0) / math.sqrt(p0 * (1 - p0) / n)


def random_walk_daily(bars, n_series: int = 200, seed: int = 20261027) -> list:
    """Driftless log random walks with the real series' per-calendar-year daily sigma (vol-matched, no direction), one DayBar list per series,
    with intraday high/low from 20 sub-steps and an overnight gap drawn from the real per-year gap sigma."""
    rnd = random.Random(seed); out = []
    by_year = defaultdict(list); gaps = defaultdict(list)
    for a, b in zip(bars, bars[1:]):
        by_year[b.d.year].append(math.log(b.close / a.close)); gaps[b.d.year].append(math.log(b.open / a.close))
    sig = {y: (math.sqrt(sum(x * x for x in v) / len(v)) if v else 0.01) for y, v in by_year.items()}
    gsig = {y: (math.sqrt(sum(x * x for x in v) / len(v)) if v else 0.003) for y, v in gaps.items()}
    for s in range(n_series):
        px = bars[0].close; series = []
        for b in bars:
            y = b.d.year; o = px * math.exp(rnd.gauss(0, gsig.get(y, 0.003)))
            step = sig.get(y, 0.01) * math.sqrt(1 - min(0.9, (gsig.get(y, 0.003) / max(sig.get(y, 0.01), 1e-9)) ** 2)) / math.sqrt(20)
            x = o; hi = lo = o
            for _ in range(20):
                x *= math.exp(rnd.gauss(0, step)); hi = max(hi, x); lo = min(lo, x)
            series.append(DayBar(b.d, o, hi, lo, x)); px = x
        out.append(label_bars(series))
    return out


def daily_study(bars: list, index: str, window: str = "DEVELOPMENT", n_null: int = 200, alpha: float = 0.05, seed: int = 20261027) -> dict:
    bars = label_bars(bars)
    sel = [i for i, b in enumerate(bars) if daily_window_of(b.d) == window and b.ref]
    forbidden = sorted({daily_window_of(b.d) for b in bars} - {window, "OUTSIDE"})
    nulls = random_walk_daily([bars[i] for i in sel], n_null, seed)
    n_cells = len(LABELS) * len(OUTCOMES) + len(EXPECTED_DIRECTION) * 2; zcrit = bonferroni_z(n_cells, alpha)
    out = {"index": index, "window": window, "sessions": len(sel), "windows_present_but_not_evaluated": forbidden, "family": n_cells, "bonferroni_z_one_sided": round(zcrit, 3),
           "labels": {}, "behaviour_cells": {}, "trade_cells": {}}
    sub = [bars[i] for i in sel]; local = list(range(len(sub)))
    base_large = sum(1 for b in sub if b.rel_range is not None and b.rel_range >= 2.0) / len(sub)
    for lab in LABELS:
        idx = [i for i in local if lab in sub[i].labels]
        out["labels"][lab] = {"n": len(idx), "share": round(len(idx) / len(sub), 4)}
        for key in OUTCOMES:
            stride = 6 if "next5" in key else 1
            k, n = _rates(sub, idx, key, stride)
            if key == "next_large":
                p0 = base_large; z = _binom_z(k, n, p0); null_sd = None
            else:
                rates = []
                for ns in nulls:
                    nidx = [i for i in range(len(ns)) if lab in ns[i].labels]
                    kk, nn = _rates(ns, nidx, key, stride)
                    if nn >= 20:
                        rates.append(kk / nn)
                p0 = sum(rates) / len(rates) if rates else None
                null_sd = (math.sqrt(sum((r - p0) ** 2 for r in rates) / (len(rates) - 1)) if rates and len(rates) > 1 else None)
                z = _binom_z(k, n, p0) if p0 is not None else None
            # halves
            halves = {}
            for hname, (a, b) in HALVES.items():
                hidx = [i for i in idx if a <= sub[i].d <= b]
                kh, nh = _rates(sub, hidx, key, stride)
                halves[hname] = {"n": nh, "rate": round(kh / nh, 4) if nh else None}
            rate = k / n if n else None; eff = (rate - p0) if (rate is not None and p0 is not None) else None
            same_sign = all(h["rate"] is not None and (h["rate"] - p0 > 0) == (eff > 0) for h in halves.values()) if eff is not None and p0 is not None else False
            status = ("UNTESTED" if n < MIN_N or z is None else "SUPPORTED_DESCRIPTIVE" if abs(z) >= STATUS_RULE["min_abs_z"] and abs(eff) >= STATUS_RULE["min_abs_effect"] and same_sign
                      else "STATISTICAL_ONLY" if abs(z) >= STATUS_RULE["min_abs_z"] else "NOT_SUPPORTED")
            out["behaviour_cells"][f"{lab}|{key}"] = {"n": n, "rate": round(rate, 4) if rate is not None else None, "null_rate": round(p0, 4) if p0 is not None else None,
                                                    "null_sd_across_series": round(null_sd, 4) if null_sd else None, "effect": round(eff, 4) if eff is not None else None,
                                                    "z": round(z, 2) if z is not None else None, "halves": halves, "same_sign_halves": same_sign, "status": status}
    for lab, direction in EXPECTED_DIRECTION.items():
        idx = [i for i in local if lab in sub[i].labels]
        for horizon in (1, 5):
            stride = horizon + 1; moves = []; levels = []; last = -10 ** 9
            for i in idx:
                if i <= last + stride - 1:
                    continue
                mv, e = _move(sub, i, horizon, direction)
                if mv is None:
                    continue
                moves.append(mv); levels.append(e); last = i
            n = len(moves)
            # null: same label on the random walks, same direction
            nm = []
            for ns in nulls:
                nidx = [i for i in range(len(ns)) if lab in ns[i].labels]; last = -10 ** 9
                for i in nidx:
                    if i <= last + stride - 1:
                        continue
                    mv, e = _move(ns, i, horizon, direction)
                    if mv is not None:
                        nm.append(mv / e); last = i
            rel = [m / e for m, e in zip(moves, levels)]
            def mom(xs):
                if len(xs) < 2:
                    return (len(xs), None, None)
                m = sum(xs) / len(xs); sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (len(xs) - 1)); return (len(xs), m, sd)
            n1, m1, s1 = mom(rel); n0, m0, s0 = mom(nm)
            z_null = ((m1 - m0) / math.sqrt(s1 * s1 / n1 + s0 * s0 / n0)) if n1 > 1 and n0 > 1 and s1 and s0 else None
            net = [r - COST_FRACTION["current"] for r in rel]; net_dev = [r - COST_FRACTION["development_era"] for r in rel]
            _, mn, sn = mom(net); _, mnd, _ = mom(net_dev)
            t_net = (mn / (sn / math.sqrt(n1))) if n1 > 1 and sn else None
            verdict = ("NOT_ENOUGH_DATA" if n < MIN_N else "BEATS_NOISE_AND_COST" if z_null is not None and z_null >= zcrit and mn > 0 and t_net is not None and t_net >= zcrit
                       else "BEATS_NOISE_NOT_COST" if z_null is not None and z_null >= zcrit else "INSIDE_NOISE")
            out["trade_cells"][f"{lab}|{direction}|h{horizon}"] = {"n": n, "mean_move_pct": round(100 * m1, 4) if m1 is not None else None, "mean_move_points": round(sum(moves) / n, 2) if n else None,
                                                                  "null_mean_move_pct": round(100 * m0, 4) if m0 is not None else None, "z_vs_null": round(z_null, 2) if z_null is not None else None,
                                                                  "cost_pct_current": 100 * COST_FRACTION["current"], "mean_net_pct_current": round(100 * mn, 4) if mn is not None else None,
                                                                  "mean_net_pct_dev_era": round(100 * mnd, 4) if mnd is not None else None, "t_net_current": round(t_net, 2) if t_net is not None else None, "verdict": verdict}
    out["count_behaviour"] = dict(defaultdict(int, {s: sum(1 for c in out["behaviour_cells"].values() if c["status"] == s) for s in {c["status"] for c in out["behaviour_cells"].values()}}))
    out["count_trade"] = dict(defaultdict(int, {s: sum(1 for c in out["trade_cells"].values() if c["verdict"] == s) for s in {c["verdict"] for c in out["trade_cells"].values()}}))
    out["outcome"] = "STRATEGY_QUESTION_OPEN" if out["count_trade"].get("BEATS_NOISE_AND_COST") else "NO_DAILY_STRATEGY"
    return out
