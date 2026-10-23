"""Skill 32 — decay curve, staggered / diversified / liquidity-weighted expressions of the one-week reversal, costed at several capital sizes."""
from __future__ import annotations

import math
import random
import statistics as st
from collections import defaultdict

from .portfolio import _snapshot, _mom, clear_cache
from .signals import traded_value

SIG = "STR_1W"; WEEK = 5
DP_PER_SALE = 15.93 * 1.18


def _members(panel, t, max_k):
    """Universe rows at t with forward weekly closes available up to t+5*max_k: list of (signal, stock)."""
    rows = []
    for sg, _, s in _snapshot(panel, t, WEEK, "close"):
        if sg.get(SIG) is None or t + WEEK * max_k >= len(s.c) or any(s.c[t + WEEK * k] is None for k in range(0, max_k + 1)):
            continue
        rows.append((sg[SIG], s))
    return rows


def _wret(s, t, k):
    return s.c[t + WEEK * k] / s.c[t + WEEK * (k - 1)] - 1


def _cret(s, t, k):
    return s.c[t + WEEK * k] / s.c[t] - 1


def decay_curve(panel, cal, dates, n_perm: int = 100, seed: int = 20261101, max_k: int = 4) -> dict:
    per_date = []
    for t in dates:
        rows = _members(panel, t, max_k)
        if len(rows) < 50:
            continue
        per_date.append(rows)
    def stats_for(rows_fn):
        wk = {k: {"q1": [], "q5": [], "ew": []} for k in range(1, max_k + 1)}; cum = {k: {"q1": [], "ew": []} for k in range(1, max_k + 1)}
        for rows in per_date:
            r = rows_fn(rows); r.sort(key=lambda x: x[0]); q = len(r) // 5; q1 = r[:q]; q5 = r[-q:]
            for k in range(1, max_k + 1):
                wk[k]["q1"].append(sum(_wret(s, t_, k) for _, s, t_ in q1) / q); wk[k]["q5"].append(sum(_wret(s, t_, k) for _, s, t_ in q5) / q)
                wk[k]["ew"].append(sum(_wret(s, t_, k) for _, s, t_ in r) / len(r))
                cum[k]["q1"].append(sum(_cret(s, t_, k) for _, s, t_ in q1) / q); cum[k]["ew"].append(sum(_cret(s, t_, k) for _, s, t_ in r) / len(r))
        return wk, cum
    # attach t to rows
    dated = []
    for t, rows in zip([t for t in dates if len(_members(panel, t, max_k)) >= 50], per_date):
        dated.append([(v, s, t) for v, s in rows])
    per_date = dated
    wk, cum = stats_for(lambda rows: list(rows))
    rnd = random.Random(seed); null_cum = {k: [] for k in range(1, max_k + 1)}; null_wk1 = []
    for _ in range(n_perm):
        def shuffled(rows):
            vals = [v for v, _, _ in rows]; rnd.shuffle(vals); return [(vals[i], s, t) for i, (_, s, t) in enumerate(rows)]
        nwk, ncum = stats_for(shuffled)
        for k in range(1, max_k + 1):
            null_cum[k].append(sum(a - b for a, b in zip(ncum[k]["q1"], ncum[k]["ew"])) / len(ncum[k]["q1"]))
        null_wk1.append(sum(a - b for a, b in zip(nwk[1]["q1"], nwk[1]["ew"])) / len(nwk[1]["q1"]))
    out = {"weeks": len(per_date), "by_week": {}, "cumulative": {}}
    for k in range(1, max_k + 1):
        sp = [a - b for a, b in zip(wk[k]["q1"], wk[k]["q5"])]; ex = [a - b for a, b in zip(wk[k]["q1"], wk[k]["ew"])]
        n, m, sd = _mom(sp); _, me, sde = _mom(ex)
        out["by_week"][k] = {"q1_minus_q5_pct": round(100 * m, 3), "t_spread": round(m / (sd / math.sqrt(n)), 2) if sd else None, "q1_minus_ew_pct": round(100 * me, 3), "t_excess": round(me / (sde / math.sqrt(n)), 2) if sde else None}
        cx = [a - b for a, b in zip(cum[k]["q1"], cum[k]["ew"])]; n, mc, sdc = _mom(cx); nm = sum(null_cum[k]) / len(null_cum[k]); nsd = st.pstdev(null_cum[k])
        z = (mc - nm) / nsd if nsd else None; t_stat = mc / (sdc / math.sqrt(n)) if sdc else None
        out["cumulative"][k] = {"q1_minus_ew_pct": round(100 * mc, 3), "t": round(t_stat, 2) if t_stat else None, "null_sd_pct": round(100 * nsd, 4), "z": round(z, 2) if z is not None else None,
                                "status": ("SUPPORTED" if k in (2, 4) and z is not None and z >= 3 and t_stat is not None and t_stat >= 3 else "NOT_SUPPORTED" if k in (2, 4) else "DESCRIPTIVE")}
    return out


def _weights(members, t, scheme, n):
    if scheme == "EW":
        return [1.0 / n] * n
    tv = [max(1.0, traded_value(s, t) or 1.0) for s in members]; raw = [x / sum(tv) for x in tv]; cap = 3.0 / n
    w = [min(x, cap) for x in raw]; rem = 1.0 - sum(w); uncapped = [i for i, x in enumerate(raw) if x < cap]
    for _ in range(5):
        if rem <= 1e-12 or not uncapped:
            break
        add = rem / len(uncapped)
        for i in uncapped:
            w[i] += add
        over = [i for i in uncapped if w[i] > cap]
        rem = sum(w[i] - cap for i in over)
        for i in over:
            w[i] = cap
        uncapped = [i for i in uncapped if i not in over]
    s = sum(w); return [x / s for x in w]


def staggered_basket(panel, cal, dates, n_names: int, hold_weeks: int, weighting: str, capital: float, impact: float = 0.001) -> dict:
    """Each week a tranche of capital/K buys the N lowest-STR_1W names (weights EW or LW, whole shares), held K weeks, then sold. Weekly portfolio return vs EW universe after costs."""
    K = hold_weeks; equity = capital; tranches = []; rows = []
    for t in dates:
        mem = _members(panel, t, 1)
        if len(mem) < 50:
            continue
        mem.sort(key=lambda x: x[0]); picks = [s for _, s in mem[:n_names]]
        ew_ret = sum(_wret(s, t, 1) for _, s in mem) / len(mem)
        # 1. sell matured tranches at t (held K weeks): their last week's P&L was already marked; charge sell costs now
        cost = 0.0
        for tr in [x for x in tranches if x["exit"] == t]:
            for s, sh in tr["pos"]:
                if s.c[t]:
                    v = sh * s.c[t]; cost += v * (0.001 + 0.0000297 * 1.18 + impact) + DP_PER_SALE
            tranches.remove(tr)
        # 2. new tranche — sized from equity / K but never beyond the cash actually free (no leverage): cash = equity − value of open tranches at t
        open_value = sum(sh * s.c[t] for tr in tranches for s, sh in tr["pos"] if s.c[t])
        budget = max(0.0, min(equity / K, equity - cost - open_value)); w = _weights(picks, t, weighting, len(picks)); pos = []
        for s, wi in zip(picks, w):
            if s.c[t]:
                sh = int((budget * wi) // s.c[t])
                if sh > 0:
                    v = sh * s.c[t]; cost += v * (0.001 + 0.0000297 * 1.18 + 0.00015 + impact); pos.append((s, sh))
        tranches.append({"entry": t, "exit": t + WEEK * K, "pos": pos})
        # 3. mark all open tranches over the coming week
        pnl = 0.0; invested = 0.0
        for tr in tranches:
            for s, sh in tr["pos"]:
                if s.c[t] and s.c[t + WEEK]:
                    pnl += sh * (s.c[t + WEEK] - s.c[t]); invested += sh * s.c[t]
        equity += pnl - cost
        eq_before = equity - pnl + cost
        rows.append({"t": t, "ret": (pnl - cost) / eq_before if eq_before > 0 else 0.0, "gross_ret": pnl / invested if invested else 0.0, "ew": ew_ret, "cost": cost, "invested_share": (invested / eq_before) if eq_before > 0 else 0.0, "equity": equity})
        if equity <= 0:
            break
    if not rows:
        return {"weeks": 0}
    ex = [r["ret"] - r["ew"] for r in rows]; gx = [r["gross_ret"] - r["ew"] for r in rows]
    n, m, sd = _mom(ex); _, mg, sdg = _mom(gx)
    peak = capital; dd = 0.0
    for r in rows:
        peak = max(peak, r["equity"]); dd = min(dd, r["equity"] / peak - 1)
    yrs = n * WEEK / 252
    return {"weeks": n, "final_equity": round(rows[-1]["equity"], 0), "cagr_pct": round(100 * ((max(rows[-1]["equity"], 1e-9) / capital) ** (1 / yrs) - 1), 2) if yrs > 0 else None,
            "ew_universe_cagr_pct": round(100 * (math.prod(1 + r["ew"] for r in rows) ** (1 / yrs) - 1), 2), "total_cost": round(sum(r["cost"] for r in rows), 0),
            "cost_pct_of_capital_per_year": round(100 * sum(r["cost"] for r in rows) / capital / yrs, 2), "mean_invested_pct": round(100 * sum(r["invested_share"] for r in rows) / n, 1),
            "gross_excess_vs_ew_per_week_pct": round(100 * mg, 4), "t_gross_excess": round(mg / (sdg / math.sqrt(n)), 2) if sdg else None,
            "net_excess_vs_ew_per_week_pct": round(100 * m, 4), "t_net_excess": round(m / (sd / math.sqrt(n)), 2) if sd else None, "max_drawdown_pct": round(100 * dd, 1),
            "weekly_excess_series": [round(x, 6) for x in ex]}


CAPITALS = (50_000.0, 200_000.0, 500_000.0, 1_000_000.0)
GRID_N = (10, 30, 50); GRID_K = (1, 2, 4); GRID_W = ("EW", "LW")
PRIMARY = {"universe": "FNO", "N": 30, "K": 2, "W": "LW"}
