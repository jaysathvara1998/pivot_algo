"""Card 29 robustness protocol as code (PROJECT DEFINITION, Skill 22): walk-forward with WFE, PBO via CSCV, parameter plateaus, regime /
instrument / direction stability, and the hold-out gate. Everything operates on cell × period matrices or on per-cell summaries."""
from __future__ import annotations

import math
from itertools import combinations

from engine.backtest.metrics import moments


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


# ---------------------------------------------------------------------------------------------- walk-forward
def walk_forward(pm: dict, is_periods: int = 6, oos_periods: int = 1, anchored: bool = False) -> dict:
    """Rolling (or anchored) walk-forward over the matrix: pick the best cell in-sample by mean R per period, record its out-of-sample R.
    WFE = mean OOS R per period of the chosen cells ÷ their mean IS R per period (card 29). Also the OOS rank of the chosen cell among all cells."""
    cells = pm["cells"]; M = pm["matrix"]; T = len(pm["periods"]); steps = []
    start = 0
    while start + is_periods + oos_periods <= T:
        a = 0 if anchored else start; b = start + is_periods; c = b + oos_periods
        is_mean = {k: _mean(M[k][a:b]) for k in cells}
        best = max(cells, key=lambda k: is_mean[k])
        oos = {k: _mean(M[k][b:c]) for k in cells}
        rank = sorted(cells, key=lambda k: oos[k], reverse=True).index(best) + 1
        steps.append({"is": [pm["periods"][a], pm["periods"][b - 1]], "oos": [pm["periods"][b], pm["periods"][c - 1]], "chosen": best,
                      "is_mean_r": round(is_mean[best], 4), "oos_mean_r": round(oos[best], 4), "oos_rank": rank, "n_cells": len(cells),
                      "oos_median_all_cells": round(sorted(oos.values())[len(cells) // 2], 4)})
        start += oos_periods
    if not steps:
        return {"steps": [], "wfe": None, "oos_total_r": None, "share_oos_positive": None, "share_oos_above_median": None}
    is_m = _mean([s["is_mean_r"] for s in steps]); oos_m = _mean([s["oos_mean_r"] for s in steps])
    return {"is_periods": is_periods, "oos_periods": oos_periods, "anchored": anchored, "steps": steps, "n_steps": len(steps),
            "mean_is_r_per_period": round(is_m, 4), "mean_oos_r_per_period": round(oos_m, 4),
            "wfe": round(oos_m / is_m, 3) if is_m > 0 else None, "oos_total_r": round(sum(s["oos_mean_r"] * oos_periods for s in steps), 2),
            "share_oos_positive": round(sum(1 for s in steps if s["oos_mean_r"] > 0) / len(steps), 3),
            "share_oos_above_median": round(sum(1 for s in steps if s["oos_mean_r"] > s["oos_median_all_cells"]) / len(steps), 3)}


# ---------------------------------------------------------------------------------------------- PBO / CSCV
def pbo_cscv(pm: dict, S: int = 8) -> dict:
    """Probability of backtest overfitting (Bailey, Borwein, López de Prado & Zhu): split periods into S blocks, for every half/half
    combination select the IS-best cell and record its OOS relative rank ω; λ = logit(ω); PBO = share of λ ≤ 0."""
    cells = pm["cells"]; M = pm["matrix"]; T = len(pm["periods"]); N = len(cells)
    if T < S or N < 2:
        return {"pbo": None, "combinations": 0, "reason": "too few periods or cells"}
    blocks = [list(range(i * T // S, (i + 1) * T // S)) for i in range(S)]
    lambdas = []; oos_of_best = []; is_of_best = []
    for is_blocks in combinations(range(S), S // 2):
        is_idx = [i for b in is_blocks for i in blocks[b]]; oos_idx = [i for b in range(S) if b not in is_blocks for i in blocks[b]]
        is_perf = {k: _mean([M[k][i] for i in is_idx]) for k in cells}; oos_perf = {k: _mean([M[k][i] for i in oos_idx]) for k in cells}
        best = max(cells, key=lambda k: is_perf[k])
        rank = sorted(cells, key=lambda k: oos_perf[k]).index(best) + 1          # 1 = worst OOS
        w = rank / (N + 1); lambdas.append(math.log(w / (1 - w)))
        oos_of_best.append(oos_perf[best]); is_of_best.append(is_perf[best])
    return {"pbo": round(sum(1 for l in lambdas if l <= 0) / len(lambdas), 3), "combinations": len(lambdas), "S": S,
            "mean_logit": round(_mean(lambdas), 3), "mean_is_r_of_best": round(_mean(is_of_best), 4), "mean_oos_r_of_best": round(_mean(oos_of_best), 4),
            "share_oos_of_best_positive": round(sum(1 for x in oos_of_best if x > 0) / len(oos_of_best), 3)}


# ---------------------------------------------------------------------------------------------- plateaus
def parameter_plateau(cell_means: dict, best: str, sep: str = "|", skip_fields: tuple = ()) -> dict:
    """Neighbours differ from `best` in exactly one `sep`-separated field. Plateau: all neighbours share the sign of the best cell and their
    mean is at least half of it (for a positive best) — a spike is a best cell whose neighbours do not."""
    parts = best.split(sep); neigh = []
    for k in cell_means:
        if k == best:
            continue
        q = k.split(sep)
        if len(q) == len(parts) and sum(1 for a, b in zip(parts, q) if a != b) == 1:
            neigh.append(k)
    if not neigh:
        return {"best": best, "neighbours": 0, "plateau": None}
    bm = cell_means[best]; nm = [cell_means[k] for k in neigh]
    same_sign = all((x > 0) == (bm > 0) for x in nm)
    return {"best": best, "best_mean": round(bm, 4), "neighbours": len(neigh), "neighbour_mean": round(_mean(nm), 4), "neighbour_min": round(min(nm), 4),
            "neighbour_max": round(max(nm), 4), "same_sign": same_sign,
            "plateau": bool(same_sign and (bm <= 0 or _mean(nm) >= 0.5 * bm))}


# ---------------------------------------------------------------------------------------------- stability
def split_stability(groups: dict, min_n: int = 30, z_crit: float = 3.0) -> dict:
    """groups: {name: [r values]}. Reports mean / t per group, sign agreement, and the max |z| between any two groups."""
    out = {}; means = {}
    for k, xs in groups.items():
        mo = moments(xs); means[k] = mo
        out[k] = {"n": mo["n"], "mean": round(mo["mean"], 4) if mo["mean"] is not None else None,
                  "t": round(mo["mean"] / (mo["sd"] / math.sqrt(mo["n"])), 2) if mo.get("sd") else None}
    usable = [k for k, mo in means.items() if mo["n"] >= min_n and mo.get("sd")]
    zmax = 0.0; pair = None
    for a, b in combinations(usable, 2):
        se = math.sqrt(means[a]["sd"] ** 2 / means[a]["n"] + means[b]["sd"] ** 2 / means[b]["n"])
        z = abs(means[a]["mean"] - means[b]["mean"]) / se if se else 0.0
        if z > zmax:
            zmax, pair = z, (a, b)
    signs = {(means[k]["mean"] > 0) for k in usable}
    verdict = "INSUFFICIENT" if len(usable) < 2 else "HETEROGENEOUS" if zmax >= z_crit else "SIGN_FLIP" if len(signs) > 1 else "STABLE"
    return {"groups": out, "usable_groups": usable, "max_abs_z": round(zmax, 2), "max_pair": pair, "same_sign": len(signs) <= 1, "verdict": verdict}


def cost_sensitivity(trades, multipliers=(0.5, 1.0, 1.5, 2.0, 3.0)) -> dict:
    n = len(trades)
    if n == 0:
        return {}
    out = {f"{m:g}x": round(sum(t.r_net_at(m) for t in trades) / n, 4) for m in multipliers}
    g = sum(t.r_gross for t in trades) / n; c = sum(t.cost_r for t in trades) / n
    out["breakeven_multiplier"] = round(g / c, 3) if c > 0 else None
    return out


# ---------------------------------------------------------------------------------------------- hold-out gate
def holdout_gate(backtest_verdicts: dict) -> dict:
    """The HOLDOUT may be read once, for a candidate that is VIABLE_ON_DEVELOPMENT at Level 21. Returns the permission and the reason."""
    viable = [k for k, v in backtest_verdicts.items() if v == "VIABLE_ON_DEVELOPMENT"]
    return {"permitted": bool(viable), "candidates": viable,
            "reason": "no candidate is VIABLE_ON_DEVELOPMENT; the hold-out stays unread" if not viable else "read once for the listed candidates"}
