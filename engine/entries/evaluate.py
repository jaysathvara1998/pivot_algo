"""Development-window evaluation of the entry-rule grid for the directional setups (family = setups × rules)."""
from __future__ import annotations

from engine.setups import assert_window, bonferroni_z
from engine.setups.registry import REGISTRY
from engine.setups.evaluate import _labels_at
from .rules import GRID
from .simulate import simulate, metrics


def _signals(seq, setup):
    out = []
    for i, b in enumerate(seq):
        labs = _labels_at(b["labels"], b["state"], b["vol"])
        if all(r in labs for r in setup.requires) and not any(x in labs for x in setup.excludes):
            out.append(i)
    return out


def evaluate_grid(stream, null_stream, interval_minutes: int, setups=None, grid=GRID, alpha: float = 0.05) -> dict:
    setups = setups or [s for s in REGISTRY if s.interval_minutes == interval_minutes and s.expected in ("UP", "DOWN") and s.horizon_bars == 1 and not s.excludes and len(s.requires) == 1]
    dev, forbidden = assert_window([(b["candle"], b) for b in stream])
    bars = [b for c, b in dev]
    n_trials = len(setups) * len(grid)
    zcrit = bonferroni_z(n_trials, alpha)
    out = {"interval_minutes": interval_minutes, "development_bars": len(bars), "windows_present_but_not_evaluated": forbidden,
           "setups": [s.name for s in setups], "rules": len(grid), "n_trials": n_trials, "bonferroni_t_one_sided": round(zcrit, 3), "cells": {}}
    best = None
    for s in setups:
        sig = _signals(bars, s); nsig = _signals(null_stream, s)
        for rule in grid:
            tr = simulate(bars, sig, s.expected, rule, interval_minutes); ntr = simulate(null_stream, nsig, s.expected, rule, interval_minutes)
            m = metrics(tr); mn = metrics(ntr)
            cell = {"real": m, "null": {k: mn.get(k) for k in ("trades", "win_rate", "mean_r_gross", "mean_r_net", "exit_reasons")}}
            cell["clears_bonferroni"] = bool(m.get("t_stat_net") is not None and m["t_stat_net"] >= zcrit and m["trades"] >= 100)
            cell["verdict"] = ("NOT_ENOUGH_DATA" if m.get("trades", 0) < 100 else "POSITIVE_AFTER_COST_DEFLATED" if cell["clears_bonferroni"] else
                               "POSITIVE_AFTER_COST_UNDEFLATED" if m["mean_r_net"] > 0 else "NEGATIVE_AFTER_COST")
            out["cells"][f"{s.name}::{rule.name}"] = cell
            if m.get("trades", 0) >= 100 and (best is None or m["mean_r_net"] > best[1]):
                best = (f"{s.name}::{rule.name}", m["mean_r_net"], m.get("t_stat_net"))
    out["best_cell_mean_r_net"] = {"cell": best[0], "mean_r_net": best[1], "t_stat": best[2]} if best else None
    out["count_by_verdict"] = {}
    for c in out["cells"].values():
        out["count_by_verdict"][c["verdict"]] = out["count_by_verdict"].get(c["verdict"], 0) + 1
    return out
