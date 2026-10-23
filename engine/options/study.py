"""Pre-registered option study (skills/26_options_volatility/HYPOTHESES.md): realised vs implied range and long-straddle P&L after size signals."""
from __future__ import annotations

import math
from collections import defaultdict

from engine.session.labels import expiry_flags
from engine.setups import assert_window, bonferroni_z
from .bs import straddle_value, next_weekly_expiry, years_to_expiry, implied_range

HORIZONS = (5, 15, 30)
SIGNALS = ("EXPANSION", "VOL_HIGH", "EXPIRY")
COST_PER_UNIT = 2.16            # registered flat figure (NIFTY, card 26: ₹1.08 per leg); superseded 2026-10-05 by straddle_cost_per_unit (per index, per trade)
MIN_N = 200
LOT = {"nifty": 65, "sensex": 20}
BROKERAGE_PER_ORDER = 20.0      # SOURCE FACT (card 26)


def straddle_cost_per_unit(index: str, premium_per_unit: float, d) -> float:
    """Explicit round-trip cost of a long straddle per unit (card 26 rates): 4 orders of brokerage ÷ lot, STT on the sell-side premium
    (0.10 % before 2024-10-01, 0.15 % after), exchange charge on premium both sides (0.03503 % before, 0.03553 % after), GST 18 % on brokerage + exchange.
    `premium_per_unit` is the straddle premium (CE + PE) per unit."""
    from datetime import date as _d
    post = d >= _d(2024, 10, 1)
    stt = (0.0015 if post else 0.0010) * premium_per_unit
    exch = (0.0003553 if post else 0.0003503) * premium_per_unit * 2
    brok = 4 * BROKERAGE_PER_ORDER / LOT[index]
    return stt + exch + brok + 0.18 * (brok + exch)


def _signal_flags(row, index: str, day_flags: dict) -> dict:
    labs = set(row["labels"])
    return {"EXPANSION": "RANGE_EXPANSION" in labs, "VOL_HIGH": row["vol"] == "HIGH", "EXPIRY": bool(day_flags.get(f"{index}_weekly_expiry"))}


def _moments(xs):
    n = len(xs)
    if n < 2:
        return n, (xs[0] if xs else None), None
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))
    return n, m, sd


def _z(a, b):
    na, ma, sa = _moments(a); nb, mb, sb = _moments(b)
    if na < 2 or nb < 2 or sa is None or sb is None:
        return None
    se = math.sqrt(sa * sa / na + sb * sb / nb)
    return (ma - mb) / se if se else None


def _outcomes(bars, t, h, index):
    """Realised range, implied range, straddle P&L (per unit), IV change and mark info over bars t+1..t+h; None if the window leaves the session."""
    if t + h >= len(bars) or bars[t + h]["session_index"] != bars[t]["session_index"] + h:
        return None
    o0 = bars[t]["opt"]; oh = bars[t + h]["opt"]
    if o0 is None or oh is None or o0["iv"] <= 0:
        return None
    hi = max(b["candle"].high for b in bars[t + 1:t + h + 1]); lo = min(b["candle"].low for b in bars[t + 1:t + h + 1])
    rr = hi - lo; ir = implied_range(o0["spot"], o0["iv"], h)
    if ir <= 0:
        return None
    v0 = o0["ce"] + o0["pe"]
    ts0 = bars[t]["candle"].timestamp; tsh = bars[t + h]["candle"].timestamp
    exp = next_weekly_expiry(ts0.date(), index)
    if oh["strike"] == o0["strike"]:
        vh = oh["ce"] + oh["pe"]; marked = False; err = None
    else:
        vh = straddle_value(oh["spot"], o0["strike"], years_to_expiry(tsh, exp), oh["iv"] / 100); marked = True; err = None
    # mark error measured where the strike is unchanged: model at (S_h, K, T_h, iv_h) vs observed
    if not marked:
        model = straddle_value(oh["spot"], o0["strike"], years_to_expiry(tsh, exp), oh["iv"] / 100); err = model - vh
    return {"ratio": rr / ir, "pnl": vh - v0, "div": oh["iv"] - o0["iv"], "marked": marked, "mark_err": err, "v0": v0, "realised": rr, "implied": ir,
            "cost": straddle_cost_per_unit(index, v0, ts0.date())}


def option_study(stream, index: str, horizons=HORIZONS, signals=SIGNALS, alpha: float = 0.05, window: str = "DEVELOPMENT") -> dict:
    sel, forbidden = assert_window([(b["candle"], b) for b in stream], allowed=(window,)); bars = [b for c, b in sel]
    n_cells = 2 * len(signals) * len(horizons); zcrit = bonferroni_z(n_cells, alpha)
    out = {"window": window, "index": index, "bars": len(bars), "bars_with_options": sum(1 for b in bars if b.get("opt")), "windows_present_but_not_evaluated": forbidden,
           "family": n_cells, "bonferroni_z_one_sided": round(zcrit, 3), "cost_model": "straddle_cost_per_unit (card 26 rates, per index, per trade)", "cells": {}, "baseline": {}}
    flags_cache = {}
    for h in horizons:
        # baseline: non-overlapping windows at stride h over all bars with options
        base = []; t = 0
        while t < len(bars):
            o = _outcomes(bars, t, h, index) if bars[t].get("opt") else None
            if o is not None:
                base.append(o); t += h + 1
            else:
                t += 1
        br = [o["ratio"] for o in base]; bp = [o["pnl"] for o in base]
        nb, mr, sr = _moments(br); _, mp, sp = _moments(bp)
        errs = sorted(abs(o["mark_err"]) for o in base if o["mark_err"] is not None)
        out["baseline"][str(h)] = {"n": nb, "mean_ratio": round(mr, 4) if mr is not None else None, "sd_ratio": round(sr, 4) if sr else None, "mean_pnl": round(mp, 4) if mp is not None else None,
                                   "sd_pnl": round(sp, 4) if sp else None, "mean_v0": round(sum(o["v0"] for o in base) / nb, 2) if nb else None,
                                   "share_marked": round(sum(1 for o in base if o["marked"]) / nb, 4) if nb else None,
                                   "model_mark_abs_err_q50_q90": [round(errs[len(errs) // 2], 3), round(errs[int(0.9 * len(errs))], 3)] if errs else None,
                                   "mean_div": round(sum(o["div"] for o in base) / nb, 4) if nb else None,
                                   "mean_cost_per_unit": round(sum(o["cost"] for o in base) / nb, 4) if nb else None,
                                   "signed_mark_err_mean": round(sum(o["mark_err"] for o in base if o["mark_err"] is not None) / max(1, len(errs)), 4) if errs else None,
                                   "mean_pnl_unmarked": round(sum(o["pnl"] for o in base if not o["marked"]) / max(1, sum(1 for o in base if not o["marked"])), 4) if nb else None,
                                   "mean_pnl_marked": round(sum(o["pnl"] for o in base if o["marked"]) / max(1, sum(1 for o in base if o["marked"])), 4) if nb else None}
        for sig in signals:
            rows = []; last_taken = -10 ** 9
            for t, b in enumerate(bars):
                if not b.get("opt"):
                    continue
                d = b["candle"].timestamp.date()
                if d not in flags_cache:
                    flags_cache[d] = expiry_flags(d)
                if not _signal_flags(b, index, flags_cache[d])[sig]:
                    continue
                if t <= last_taken + h:
                    continue
                o = _outcomes(bars, t, h, index)
                if o is None:
                    continue
                rows.append(o); last_taken = t
            cr = [o["ratio"] for o in rows]; cp = [o["pnl"] for o in rows]
            n, mr_c, sr_c = _moments(cr); _, mp_c, sp_c = _moments(cp)
            z_ratio = _z(cr, br); z_pnl = _z(cp, bp)
            cost = (sum(o["cost"] for o in rows) / n) if n else COST_PER_UNIT          # per-index, per-trade explicit cost (mean over the cell)
            net1 = [o["pnl"] - o["cost"] for o in rows]; net2 = [o["pnl"] - 2 * o["cost"] for o in rows]
            _, mn1, sn1 = _moments(net1); _, mn2, sn2 = _moments(net2)
            t_cost = (mn1 / (sn1 / math.sqrt(n))) if n >= 2 and sn1 else None
            t_cost2 = (mn2 / (sn2 / math.sqrt(n))) if n >= 2 and sn2 else None
            pricing = ("NOT_ENOUGH_DATA" if n < MIN_N else "UNDERPRICED" if z_ratio is not None and z_ratio >= zcrit else "OVERPRICED" if z_ratio is not None and z_ratio <= -zcrit else "NOT_DISTINGUISHABLE")
            if n < MIN_N:
                trade = "NOT_ENOUGH_DATA"
            elif mp_c is not None and mp_c - cost > 0 and t_cost is not None and t_cost >= zcrit and z_pnl is not None and z_pnl >= zcrit:
                trade = "POSITIVE_AFTER_COST_DEFLATED"
            elif mp_c is not None and mp_c - cost > 0:
                trade = "POSITIVE_UNDEFLATED"
            else:
                trade = "NEGATIVE_AFTER_COST"
            out["cells"][f"{sig}|h{h}"] = {"n": n, "mean_ratio": round(mr_c, 4) if mr_c is not None else None, "baseline_ratio": round(mr, 4) if mr is not None else None,
                                          "z_ratio_vs_baseline": round(z_ratio, 2) if z_ratio is not None else None, "pricing_verdict": pricing,
                                          "mean_pnl_per_unit": round(mp_c, 4) if mp_c is not None else None, "sd_pnl": round(sp_c, 4) if sp_c else None, "baseline_pnl": round(mp, 4) if mp is not None else None,
                                          "mean_pnl_net_cost": round(mp_c - cost, 4) if mp_c is not None else None, "t_net_cost": round(t_cost, 2) if t_cost is not None else None,
                                          "t_net_stressed_cost": round(t_cost2, 2) if t_cost2 is not None else None, "z_pnl_vs_baseline": round(z_pnl, 2) if z_pnl is not None else None,
                                          "survives_stressed_cost": bool(mp_c is not None and mp_c - 2 * cost > 0), "trade_verdict": trade, "mean_cost_per_unit": round(cost, 4),
                                          "mean_pnl_unmarked": round(sum(o["pnl"] for o in rows if not o["marked"]) / max(1, sum(1 for o in rows if not o["marked"])), 4) if n else None,
                                          "mean_pnl_marked": round(sum(o["pnl"] for o in rows if o["marked"]) / max(1, sum(1 for o in rows if o["marked"])), 4) if n else None,
                                          "baseline_pnl_unmarked": out["baseline"][str(h)]["mean_pnl_unmarked"],
                                          "mean_realised_pts": round(sum(o["realised"] for o in rows) / n, 2) if n else None, "mean_implied_pts": round(sum(o["implied"] for o in rows) / n, 2) if n else None,
                                          "mean_div": round(sum(o["div"] for o in rows) / n, 4) if n else None, "share_marked": round(sum(1 for o in rows if o["marked"]) / n, 4) if n else None}
    out["count_pricing"] = defaultdict(int); out["count_trade"] = defaultdict(int)
    for c in out["cells"].values():
        out["count_pricing"][c["pricing_verdict"]] += 1; out["count_trade"][c["trade_verdict"]] += 1
    out["count_pricing"] = dict(out["count_pricing"]); out["count_trade"] = dict(out["count_trade"])
    out["outcome"] = "STRATEGY_QUESTION_OPEN" if any(c["trade_verdict"] == "POSITIVE_AFTER_COST_DEFLATED" and c["survives_stressed_cost"] for c in out["cells"].values()) else "NO_OPTION_STRATEGY"
    return out
