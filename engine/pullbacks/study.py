"""Pullback studies: resolution by depth class, depth distribution, next-bar outcomes, vs random walk."""
from __future__ import annotations

from collections import Counter, defaultdict

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .machine import replay_pullbacks
from .params import PullbackParams, DEFAULT_PB_PARAMS, DEPTH_CLASSES


def pb_labels_by_key(items, p: PullbackParams = DEFAULT_PB_PARAMS):
    out, recs = {}, []
    for i, c, r in replay_pullbacks(items, p):
        out[(r.timestamp, int(c.interval.total_seconds()))] = sorted(set(e.label for e in r.events))
        recs.append((c, r))
    return out, recs


def pb_resolution(recs) -> dict:
    """For every pullback: outcome (CONTINUATION / FAILED_CONTINUATION / EXPIRED) by its max-depth class at resolution and by trend."""
    out = defaultdict(Counter)
    starts = Counter(); depth_at_start = Counter(); durations = defaultdict(list)
    for c, r in recs:
        for e in r.events:
            if e.event == "PULLBACK_START":
                starts[e.trend] += 1; depth_at_start[f"{e.trend}|{e.depth_class}"] += 1
            if e.event in ("CONTINUATION", "FAILED_CONTINUATION", "EXPIRED"):
                out[(e.trend, e.depth_class or "?")][e.event] += 1
                out[(e.trend, "ALL")][e.event] += 1
                durations[(e.trend, e.event)].append(e.bars_since_start)
    res = {}
    for (trend, cls), cnt in out.items():
        cont, fail, exp = cnt["CONTINUATION"], cnt["FAILED_CONTINUATION"], cnt["EXPIRED"]
        tot = cont + fail + exp
        res[f"{trend}|{cls}"] = {"n": tot, "continuation": cont, "failed": fail, "expired": exp,
                                 "p_continuation": round(cont / tot, 4) if tot else None,
                                 "p_continuation_given_resolved": round(cont / (cont + fail), 4) if cont + fail else None}
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    return {"starts": dict(starts), "depth_class_at_start": dict(depth_at_start), "by_class": res,
            "median_bars_to": {f"{t}|{e}": med(v) for (t, e), v in durations.items()}}


def pb_study(items, null_items, p: PullbackParams = DEFAULT_PB_PARAMS) -> dict:
    real_map, real_recs = pb_labels_by_key(items, p)
    null_map, null_recs = pb_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    rr, rn = pb_resolution(real_recs), pb_resolution(null_recs)
    res["resolution_real"], res["resolution_null"] = rr, rn
    z = {}
    for key, a in rr["by_class"].items():
        b = rn["by_class"].get(key)
        if b and a["continuation"] + a["failed"] and b["continuation"] + b["failed"]:
            z[key] = {"real": a["p_continuation_given_resolved"], "null": b["p_continuation_given_resolved"], "n_real": a["continuation"] + a["failed"], "n_null": b["continuation"] + b["failed"],
                      "z": round(two_sample_z(a["continuation"], a["continuation"] + a["failed"], b["continuation"], b["continuation"] + b["failed"]), 2)}
    res["continuation_vs_null"] = z
    n_real = sum(1 for c, r in real_recs if r.session_index > 0); n_null = sum(1 for c, r in null_recs if r.session_index > 0)
    er = Counter(e.label for c, r in real_recs for e in r.events); en = Counter(e.label for c, r in null_recs for e in r.events)
    res["events_per_1000_bars_real"] = {k: round(1000 * v / n_real, 2) for k, v in sorted(er.items())} if n_real else {}
    res["events_per_1000_bars_null"] = {k: round(1000 * v / n_null, 2) for k, v in sorted(en.items())} if n_null else {}
    return res
