"""Development-window evaluation of the GAP_FADE family against the gap-aware null (Skill 20)."""
from __future__ import annotations

import math

from engine.setups import bonferroni_z
from .rules import GRID
from .simulate import run_rule


def _stats(trades, attr):
    xs = [getattr(t, attr) for t in trades]; n = len(xs)
    if n == 0:
        return {"n": 0}
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)) if n > 1 else 0.0
    return {"n": n, "mean": round(m, 4), "sd": round(sd, 4), "t": round(m / (sd / math.sqrt(n)), 2) if sd else None}


def _two_sample_z(a, b):
    if a.get("n", 0) < 2 or b.get("n", 0) < 2 or a["sd"] is None or b["sd"] is None:
        return None
    se = math.sqrt(a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"])
    return round((a["mean"] - b["mean"]) / se, 2) if se else None


def evaluate_gap_fade(sessions, null_sessions, grid=GRID, alpha: float = 0.05, window: str = "DEVELOPMENT") -> dict:
    dev = [s for s in sessions if s.window == window]
    forbidden = sorted({s.window for s in sessions} - {window, "OUTSIDE"})
    null = [s for s in null_sessions if s.window == window]
    zcrit = bonferroni_z(len(grid), alpha)
    out = {"window": window, "sessions": len(dev), "null_sessions": len(null), "windows_present_but_not_evaluated": forbidden, "n_trials": len(grid),
           "bonferroni_t_one_sided": round(zcrit, 3), "small_gap_sessions": {"UP": sum(1 for s in dev if s.gap_class == "SMALL" and s.gap_sign == "UP"),
                                                                              "DOWN": sum(1 for s in dev if s.gap_class == "SMALL" and s.gap_sign == "DOWN")}, "cells": {}}
    for rule in grid:
        tr = run_rule(dev, rule); nt = run_rule(null, rule)
        g = _stats(tr, "r_gross"); ng = _stats(nt, "r_gross")
        n1 = _stats(tr, "r_net"); n2 = _stats(tr, "r_net_stressed")
        reasons = {}
        for t in tr:
            reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
        nreasons = {}
        for t in nt:
            nreasons[t.exit_reason] = nreasons.get(t.exit_reason, 0) + 1
        hit = round(sum(1 for t in tr if t.exit_reason == "TARGET") / len(tr), 4) if tr else None
        nhit = round(sum(1 for t in nt if t.exit_reason == "TARGET") / len(nt), 4) if nt else None
        z_vs_null = _two_sample_z(g, ng)
        cell = {"real": {"trades": len(tr), "target_rate": hit, "gross": g, "net_development_era": n1, "net_stressed": n2, "exit_reasons": reasons,
                         "median_stop_ref": sorted(t.stop_ref for t in tr)[len(tr) // 2] if tr else None,
                         "median_mfe_r": sorted(t.mfe_r for t in tr)[len(tr) // 2] if tr else None},
                "null": {"trades": len(nt), "target_rate": nhit, "gross": ng, "exit_reasons": nreasons}, "z_gross_vs_null": z_vs_null}
        n = len(tr)
        if n < 100:
            v = "NOT_ENOUGH_DATA"
        elif n2["mean"] > 0 and n2["t"] is not None and n2["t"] >= zcrit and z_vs_null is not None and z_vs_null >= 2.0:
            v = "STRATEGY_CANDIDATE"
        elif n2["mean"] > 0:
            v = "POSITIVE_UNDEFLATED"
        else:
            v = "NEGATIVE_AFTER_COST"
        cell["verdict"] = v
        out["cells"][rule.name] = cell
    out["count_by_verdict"] = {}
    for c in out["cells"].values():
        out["count_by_verdict"][c["verdict"]] = out["count_by_verdict"].get(c["verdict"], 0) + 1
    out["outcome"] = "STRATEGY_CANDIDATE_FOUND" if out["count_by_verdict"].get("STRATEGY_CANDIDATE") else "NO_STRATEGY"
    return out
