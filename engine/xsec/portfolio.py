"""Quintile portfolios, permutation null, costs and the ₹50,000 basket (Skill 31)."""
from __future__ import annotations

import math
import random
import statistics as st

from .signals import universe_at, signals_at, HORIZON, EXPECTED

CAPITAL = 50_000.0
# cash-equity delivery costs (NSE schedule / card 26): brokerage 0; STT 0.1 % each side; exchange 0.00297 % each side; SEBI ₹10/crore; stamp 0.015 % buy; GST 18 % on exchange+SEBI; DP ₹15.93 + GST per sell per scrip; impact 0.10 % per side
def round_trip_cost_fraction(value: float, impact: float = 0.001) -> float:
    stt = 0.001 * 2; exch = 0.0000297 * 2 * 1.18; sebi = 10 / 1e7 * 2 * 1.18; stamp = 0.00015; dp = 15.93 * 1.18 / value if value > 0 else 0.0
    return stt + exch + sebi + stamp + dp + 2 * impact


def rebalance_dates(cal: list, start_i: int, end_i: int, step: int) -> list:
    """Every `step` sessions from start_i (non-overlapping holding periods)."""
    return list(range(start_i, end_i - step, step))


def period_return(s, t: int, h: int, form: str) -> float | None:
    if form == "close":
        a, b = s.c[t], s.c[t + h]
    else:
        a, b = s.o[t + 1], s.o[t + 1 + h] if t + 1 + h < len(s.o) else None
    return (b / a - 1) if a and b else None


_SNAP = {}


def clear_cache():
    _SNAP.clear()


def bad_print(s, t: int, h: int) -> bool:
    """Data-quality exclusion (HYPOTHESES.md amendment 2026-10-05): a |daily return| > 50 % on a day whose volume is below 20 % of the trailing 60-day median
    volume, anywhere in the past 252 sessions or inside the holding period, marks the stock-period as a probable bad print or unadjusted corporate action."""
    lo = max(1, t - 252); hi = min(len(s.c) - 1, t + h)
    vols = [v for v in s.v[max(0, t - 60):t + 1] if v is not None]; med = st.median(vols) if vols else 0.0
    for i in range(lo, hi + 1):
        a, b = s.c[i - 1], s.c[i]
        if a and b and abs(b / a - 1) > 0.5 and (s.v[i] is None or s.v[i] < 0.2 * med):
            return True
    return False


def _snapshot(panel, t: int, h: int, form: str):
    key = (id(panel), t, h, form)
    if key not in _SNAP:
        rows = []
        for s in universe_at(panel, t):
            r = period_return(s, t, h, form)
            if r is None or bad_print(s, t, h):
                continue
            rows.append((signals_at(s, t), r, s))
        _SNAP[key] = rows
    return _SNAP[key]


def quintile_spread(panel, cal, t: int, sig: str, form: str = "close", rnd: random.Random | None = None):
    """Returns (q5_mean, q1_mean, universe_mean, names_q5) at date t for signal sig; rnd → permute signals across stocks (null)."""
    h = HORIZON[sig]
    rows = [(sg[sig], r, s) for sg, r, s in _snapshot(panel, t, h, form) if sg.get(sig) is not None]
    if len(rows) < 50:
        return None
    if rnd is not None:
        vals = [v for v, _, _ in rows]; rnd.shuffle(vals); rows = [(vals[i], r, s) for i, (_, r, s) in enumerate(rows)]
    rows.sort(key=lambda x: x[0]); n = len(rows); q = n // 5
    q1 = rows[:q]; q5 = rows[-q:]
    return (sum(r for _, r, _ in q5) / q, sum(r for _, r, _ in q1) / q, sum(r for _, r, _ in rows) / n, [s for _, _, s in q5[::-1]])


def _mom(xs):
    n = len(xs)
    if n < 2:
        return n, (xs[0] if xs else None), None
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)); return n, m, sd


def cell(panel, cal, dates, sig, form, halves, n_perm: int = 200, seed: int = 20261031) -> dict:
    """Spread (Q5−Q1) and long-only excess (Q5−EW) series over `dates`, with permutation null for the spread mean."""
    spreads = []; excess = []; used = []
    for t in dates:
        r = quintile_spread(panel, cal, t, sig, form)
        if r is None:
            continue
        spreads.append(r[0] - r[1]); excess.append(r[0] - r[2]); used.append(t)
    n, m, sd = _mom(spreads); _, me, sde = _mom(excess)
    rnd = random.Random(seed); null_means = []
    for k in range(n_perm):                                                    # null over ALL used dates (snapshots are cached, so permutations are cheap)
        vals = []
        for t in used:
            r = quintile_spread(panel, cal, t, sig, form, rnd)
            if r is not None:
                vals.append(r[0] - r[1])
        if vals:
            null_means.append(sum(vals) / len(vals))
    nm = sum(null_means) / len(null_means) if null_means else 0.0; nsd = st.pstdev(null_means) if len(null_means) > 1 else None
    direction = EXPECTED[sig]
    z = ((m - nm) / nsd) if (m is not None and nsd) else None
    t_stat = (m / (sd / math.sqrt(n))) if n > 1 and sd else None
    hv = {}
    for name, (a, b) in halves.items():
        xs = [x for x, t in zip(spreads, used) if a <= cal[t] <= b]
        hv[name] = {"n": len(xs), "mean": round(sum(xs) / len(xs), 5) if xs else None}
    signed = (lambda x: x * direction) if direction else (lambda x: x)
    same_sign = all(h["mean"] is not None and (h["mean"] > 0) == (m > 0) for h in hv.values()) if m is not None else False
    if direction == 0 or n < 24 or z is None:
        status = "DESCRIPTIVE" if direction == 0 else "UNTESTED"
    elif signed(z) >= 3.0 and signed(t_stat or 0) >= 3.0 and same_sign:
        status = "SUPPORTED_DESCRIPTIVE"
    elif signed(z) >= 3.0:
        status = "STATISTICAL_ONLY"
    else:
        status = "NOT_SUPPORTED"
    return {"signal": sig, "form": form, "periods": n, "mean_spread": round(m, 5) if m is not None else None, "t_spread": round(t_stat, 2) if t_stat else None,
            "null_mean": round(nm, 5), "null_sd": round(nsd, 5) if nsd else None, "z_vs_null": round(z, 2) if z is not None else None, "mean_long_only_excess": round(me, 5) if me is not None else None,
            "t_long_only_excess": round(me / (sde / math.sqrt(n)), 2) if n > 1 and sde else None, "halves": hv, "same_sign_halves": same_sign, "expected_direction": direction, "status": status,
            "annualised_spread_pct": round(100 * m * (252 / HORIZON[sig]), 2) if m is not None else None}


def basket_backtest(panel, cal, dates, sig, n_names: int, impact: float = 0.001) -> dict:
    """₹50,000 long-only basket: the n_names most extreme names on the signal's EXPECTED side (Q5 for positive-direction signals, Q1 for reversal signals) at each
    rebalance, whole shares, equal rupee targets; costs per round trip; period P&L vs the EW universe."""
    h = HORIZON[sig]; equity = CAPITAL; holdings = {}; rows = []; direction = EXPECTED[sig]
    for t in dates:
        r = quintile_spread(panel, cal, t, sig, "close")
        if r is None:
            continue
        if direction >= 0:
            target = [s for s in r[3][:n_names]]
        else:
            rows_t = sorted([(sg[sig], s) for sg, _, s in _snapshot(panel, t, h, "close") if sg.get(sig) is not None], key=lambda x: x[0])
            target = [s for _, s in rows_t[:n_names]]
        # sells: names not in target
        cost = 0.0; turnover_value = 0.0
        for sid, (s, sh) in list(holdings.items()):
            if s not in target and s.c[t]:
                value = sh * s.c[t]; cost += value * round_trip_cost_fraction(value, impact) / 2 + 15.93 * 1.18; equity += 0.0; del holdings[sid]; turnover_value += value
        per = equity / n_names if target else 0.0
        for s in target:
            if s.sid not in holdings and s.c[t]:
                sh = int(per // s.c[t])
                if sh > 0:
                    value = sh * s.c[t]; cost += value * (0.001 + 0.0000297 * 1.18 + 0.00015 + impact); holdings[s.sid] = (s, sh); turnover_value += value
        # period P&L
        pnl = 0.0; invested = 0.0
        for sid, (s, sh) in holdings.items():
            if s.c[t] and s.c[t + h]:
                pnl += sh * (s.c[t + h] - s.c[t]); invested += sh * s.c[t]
        equity += pnl - cost
        rows.append({"date": cal[t].isoformat(), "equity": round(equity, 0), "pnl": round(pnl, 0), "cost": round(cost, 0), "invested": round(invested, 0), "ew_universe_ret": r[2], "basket_ret": (pnl / invested) if invested else 0.0})
    if not rows:
        return {"periods": 0}
    ex = [x["basket_ret"] - x["ew_universe_ret"] for x in rows]; n, m, sd = _mom(ex)
    peak = CAPITAL; dd = 0.0
    for x in rows:
        peak = max(peak, x["equity"]); dd = min(dd, x["equity"] / peak - 1)
    return {"periods": n, "final_equity": rows[-1]["equity"], "total_cost": round(sum(x["cost"] for x in rows), 0), "mean_excess_vs_ew_per_period": round(m, 5) if m is not None else None,
            "t_excess": round(m / (sd / math.sqrt(n)), 2) if n > 1 and sd else None, "max_drawdown_pct": round(100 * dd, 1), "cagr_pct": round(100 * ((rows[-1]["equity"] / CAPITAL) ** (252 / (h * n)) - 1), 2) if n else None,
            "cost_pct_of_capital_per_year": round(100 * sum(x["cost"] for x in rows) / CAPITAL / (h * n / 252), 2), "equity_path": rows[::max(1, len(rows) // 24)]}
