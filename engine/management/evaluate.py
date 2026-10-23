"""Development evaluation of the management grid, and the single pre-registered VALIDATION read (Skill 19)."""
from __future__ import annotations

import math

from engine.setups import assert_window, bonferroni_z
from engine.setups.registry import REGISTRY
from engine.entries.evaluate import _signals
from engine.entries.simulate import metrics
from .rules import GRID, REFERENCE_RULE, ManagementRule
from .simulate import simulate_managed

DIRECTIONAL = [s for s in REGISTRY if s.interval_minutes == 5 and s.expected in ("UP", "DOWN") and s.horizon_bars == 1 and not s.excludes and len(s.requires) == 1]


def evaluate_management(stream, null_stream, interval_minutes: int, setups=None, grid=GRID, alpha: float = 0.05, window: str = "DEVELOPMENT") -> dict:
    setups = setups or DIRECTIONAL
    sel, forbidden = assert_window([(b["candle"], b) for b in stream], allowed=(window,))
    bars = [b for c, b in sel]
    n_trials = len(setups) * len(grid); zcrit = bonferroni_z(n_trials, alpha)
    out = {"window": window, "interval_minutes": interval_minutes, "bars": len(bars), "windows_present_but_not_evaluated": forbidden,
           "setups": [s.name for s in setups], "rules": len(grid), "n_trials": n_trials, "bonferroni_t_one_sided": round(zcrit, 3), "cells": {}, "by_dimension": {}}
    best = {}
    dims = {"stop": {}, "breakeven": {}, "trail": {}, "exit": {}, "partial": {}}
    for s in setups:
        sig = _signals(bars, s); nsig = _signals(null_stream, s) if null_stream is not None else []
        for rule in grid:
            tr = simulate_managed(bars, sig, s.expected, rule, interval_minutes)
            m = metrics(tr)
            cell = {"real": m}
            if null_stream is not None:
                mn = metrics(simulate_managed(null_stream, nsig, s.expected, rule, interval_minutes))
                cell["null"] = {k: mn.get(k) for k in ("trades", "win_rate", "mean_r_gross", "mean_r_net", "exit_reasons")}
            cell["clears_bonferroni"] = bool(m.get("t_stat_net") is not None and m["t_stat_net"] >= zcrit and m["trades"] >= 100)
            cell["verdict"] = ("NOT_ENOUGH_DATA" if m.get("trades", 0) < 100 else "POSITIVE_AFTER_COST_DEFLATED" if cell["clears_bonferroni"] else
                               "POSITIVE_AFTER_COST_UNDEFLATED" if m["mean_r_net"] > 0 else "NEGATIVE_AFTER_COST")
            out["cells"][f"{s.name}::{rule.name}"] = cell
            if m.get("trades", 0) >= 100 and (s.name not in best or m["mean_r_net"] > best[s.name]["mean_r_net"]):
                best[s.name] = {"rule": rule.name, "mean_r_net": m["mean_r_net"], "mean_r_gross": m["mean_r_gross"], "t_stat": m.get("t_stat_net"), "trades": m["trades"]}
            if m.get("trades", 0) >= 100:
                for d in dims:
                    dims[d].setdefault(getattr(rule, d), []).append((m["mean_r_gross"], m["mean_r_net"]))
    for d, groups in dims.items():
        out["by_dimension"][d] = {k: {"cells": len(v), "mean_r_gross": round(sum(x for x, _ in v) / len(v), 4), "mean_r_net": round(sum(y for _, y in v) / len(v), 4)} for k, v in groups.items()}
    out["best_by_setup"] = best
    out["count_by_verdict"] = {}
    for c in out["cells"].values():
        out["count_by_verdict"][c["verdict"]] = out["count_by_verdict"].get(c["verdict"], 0) + 1
    return out


# ---------------------------------------------------------------------------------------------------------------------------------------
# The VALIDATION read. Cells are fixed by the plan in skills/19_trade_management/VALIDATION_PLAN.md (written 2026-10-04 before any run):
#   per directional setup: (a) REFERENCE_RULE (unmanaged Level 17 neighbour), (b) the development-best management rule for that setup.
# Family = 4 cells. Metrics: mean net R with one-sided Bonferroni t; stability of mean net R vs development; hit rate of the setup's
# next-bar direction vs development and vs the null.
STABILITY_Z = 3.0


def _rule_from_name(name: str) -> ManagementRule:
    s, b, t, e, p = name.split("|")
    return ManagementRule(s, b, t, e, p)


def _hit_rate(bars, setup):
    sgn = 1 if setup.expected == "UP" else -1; hits = n = 0
    for i in _signals(bars, setup):
        if i + 1 < len(bars) and bars[i + 1]["session_index"] == bars[i]["session_index"] + 1:
            nb = bars[i + 1]["candle"]; n += 1
            hits += 1 if (sgn * (nb.close - nb.open) > 0) else 0
    return n, (hits / n if n else None)


def validation_read(stream, null_stream, dev_result: dict, interval_minutes: int = 5, alpha: float = 0.05) -> dict:
    """Runs the pre-registered cells on the VALIDATION window only. Call once; the caller records the date of the read."""
    setups = [s for s in DIRECTIONAL if s.name in dev_result["setups"]]
    val, forbidden = assert_window([(b["candle"], b) for b in stream], allowed=("VALIDATION",)); vbars = [b for c, b in val]
    dev, _ = assert_window([(b["candle"], b) for b in stream], allowed=("DEVELOPMENT",)); dbars = [b for c, b in dev]
    cells = []
    for s in setups:
        cells.append((s, REFERENCE_RULE, "reference"))
        cells.append((s, _rule_from_name(dev_result["best_by_setup"][s.name]["rule"]), "development_best"))
    zcrit = bonferroni_z(len(cells), alpha)
    out = {"window": "VALIDATION", "validation_bars": len(vbars), "windows_present_but_not_evaluated": forbidden, "n_cells": len(cells),
           "bonferroni_t_one_sided": round(zcrit, 3), "cells": {}, "hit_rates": {}}
    for s, rule, role in cells:
        key = f"{s.name}::{rule.name}"
        vm = metrics(simulate_managed(vbars, _signals(vbars, s), s.expected, rule, interval_minutes))
        dm = dev_result["cells"][key]["real"]
        nm = metrics(simulate_managed(null_stream, _signals(null_stream, s), s.expected, rule, interval_minutes))
        if vm.get("trades", 0) >= 30 and dm.get("trades", 0) >= 30 and vm.get("sd_r_net") and dm.get("sd_r_net"):
            se = math.sqrt(vm["sd_r_net"] ** 2 / vm["trades"] + dm["sd_r_net"] ** 2 / dm["trades"])
            shift_z = (vm["mean_r_net"] - dm["mean_r_net"]) / se if se else None
        else:
            shift_z = None
        positive = bool(vm.get("t_stat_net") is not None and vm["t_stat_net"] >= zcrit and vm["trades"] >= 100)
        out["cells"][key] = {"role": role, "validation": vm, "development": {k: dm.get(k) for k in ("trades", "win_rate", "mean_r_gross", "mean_r_net", "t_stat_net")},
                             "null": {k: nm.get(k) for k in ("trades", "win_rate", "mean_r_gross", "mean_r_net")},
                             "shift_z_vs_development": round(shift_z, 2) if shift_z is not None else None,
                             "stability": "INSUFFICIENT" if shift_z is None else "STABLE" if abs(shift_z) < STABILITY_Z else "SHIFT",
                             "verdict": "NOT_ENOUGH_DATA" if vm.get("trades", 0) < 100 else "VALIDATED_POSITIVE_AFTER_COST" if positive else
                                        "POSITIVE_UNDEFLATED" if vm["mean_r_net"] > 0 else "NEGATIVE_AFTER_COST"}
    for s in setups:
        nv, hv = _hit_rate(vbars, s); nd, hd = _hit_rate(dbars, s); nn, hn = _hit_rate(null_stream, s)
        z_vs_null = None
        if hv is not None and hn is not None and nv and nn:
            p = (hv * nv + hn * nn) / (nv + nn); se = math.sqrt(p * (1 - p) * (1 / nv + 1 / nn)); z_vs_null = (hv - hn) / se if se else None
        z_vs_dev = None
        if hv is not None and hd is not None and nv and nd:
            p = (hv * nv + hd * nd) / (nv + nd); se = math.sqrt(p * (1 - p) * (1 / nv + 1 / nd)); z_vs_dev = (hv - hd) / se if se else None
        out["hit_rates"][s.name] = {"validation": {"n": nv, "hit": round(hv, 4) if hv is not None else None}, "development": {"n": nd, "hit": round(hd, 4) if hd is not None else None},
                                    "null": {"n": nn, "hit": round(hn, 4) if hn is not None else None}, "z_vs_null": round(z_vs_null, 2) if z_vs_null is not None else None,
                                    "z_vs_development": round(z_vs_dev, 2) if z_vs_dev is not None else None,
                                    "replicates": bool(z_vs_null is not None and z_vs_null >= 2.0 and (z_vs_dev is None or abs(z_vs_dev) < STABILITY_Z))}
    out["count_by_verdict"] = {}
    for c in out["cells"].values():
        out["count_by_verdict"][c["verdict"]] = out["count_by_verdict"].get(c["verdict"], 0) + 1
    return out
