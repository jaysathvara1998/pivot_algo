"""Reversal studies: resolution of violations, weakening as a forecaster of violation, persistence, next-bar outcomes."""
from __future__ import annotations

from collections import Counter, defaultdict

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .machine import replay_reversals
from .params import ReversalParams, DEFAULT_REV_PARAMS


def rev_labels_by_key(items, p: ReversalParams = DEFAULT_REV_PARAMS):
    out, recs = {}, []
    for i, c, r in replay_reversals(items, p):
        out[(r.timestamp, int(c.interval.total_seconds()))] = sorted(set(e.label for e in r.events))
        recs.append((c, r))
    return out, recs


def rev_resolution(recs, persist_h=(15, 30)) -> dict:
    """How violations resolve (confirmed / failed / expired) by old trend and by whether weakening preceded them; time to
    confirmation; persistence after confirmation: share still not back in the old trending state h bars later."""
    res = defaultdict(Counter); weak_split = defaultdict(Counter); t_conf = []
    pers = {h: Counter() for h in persist_h}
    n = len(recs)
    for i, (c, r) in enumerate(recs):
        for e in r.events:
            if e.event == "VIOLATION":
                res[e.old_trend]["violations"] += 1
                weak_split[(e.old_trend, "weak" if "weakening seen before: True" in e.observations[1] else "noweak")]["violations"] += 1
            elif e.event in ("REVERSAL_CONFIRMED", "REVERSAL_FAILED", "REVERSAL_EXPIRED"):
                res[e.old_trend][e.event] += 1
                if e.event == "REVERSAL_CONFIRMED":
                    t_conf.append(e.bars_since_violation)
                    for h in persist_h:
                        j = i + h
                        if j < n and recs[j][1].session_index == r.session_index + h:
                            pers[h]["n"] += 1
                            pers[h]["held"] += recs[j][1].market_state != f"TRENDING_{e.old_trend}"
                            pers[h]["new_trend"] += recs[j][1].market_state == f"TRENDING_{'DOWN' if e.old_trend == 'UP' else 'UP'}"
    out = {}
    for o, cnt in res.items():
        v = cnt["violations"]; cf, ff, ex = cnt["REVERSAL_CONFIRMED"], cnt["REVERSAL_FAILED"], cnt["REVERSAL_EXPIRED"]
        out[o] = {"violations": v, "confirmed": cf, "failed": ff, "expired": ex, "p_confirmed": round(cf / v, 4) if v else None,
                  "p_confirmed_given_resolved": round(cf / (cf + ff), 4) if cf + ff else None}
    med = sorted(t_conf)[len(t_conf) // 2] if t_conf else None
    return {"by_old_trend": out, "median_bars_to_confirmation": med,
            "persistence_after_confirmation": {str(h): {"n": pers[h]["n"], "p_not_back_in_old_trend": round(pers[h]["held"] / pers[h]["n"], 4) if pers[h]["n"] else None,
                                                         "p_in_new_trend": round(pers[h]["new_trend"] / pers[h]["n"], 4) if pers[h]["n"] else None} for h in persist_h}}


def weakening_study(recs, horizon) -> dict:
    """Among bars in an ARMED trending state: P(violation within horizon bars | weakening flag) vs P(... | no weakening)."""
    n = len(recs)
    cnt = Counter()
    viol_idx = [i for i, (c, r) in enumerate(recs) if any(e.event == "VIOLATION" for e in r.events)]
    import bisect
    for i, (c, r) in enumerate(recs):
        if r.stage != "ARMED" or r.market_state not in ("TRENDING_UP", "TRENDING_DOWN"):
            continue
        k = bisect.bisect_right(viol_idx, i)
        hit = k < len(viol_idx) and viol_idx[k] <= i + horizon and recs[viol_idx[k]][1].session_index == r.session_index + (viol_idx[k] - i)
        key = "weak" if r.weakening else "noweak"
        cnt[(key, "n")] += 1; cnt[(key, "hit")] += hit
    out = {}
    for key in ("weak", "noweak"):
        nn = cnt[(key, "n")]; out[key] = {"n": nn, "p_violation_within_h": round(cnt[(key, "hit")] / nn, 4) if nn else None}
    if out["weak"]["n"] and out["noweak"]["n"]:
        out["z_weak_vs_noweak"] = round(two_sample_z(cnt[("weak", "hit")], out["weak"]["n"], cnt[("noweak", "hit")], out["noweak"]["n"]), 2)
        out["lift"] = round(out["weak"]["p_violation_within_h"] - out["noweak"]["p_violation_within_h"], 4)
    return out


def rev_study(items, null_items, p: ReversalParams = DEFAULT_REV_PARAMS) -> dict:
    real_map, real_recs = rev_labels_by_key(items, p)
    null_map, null_recs = rev_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["resolution_real"], res["resolution_null"] = rev_resolution(real_recs), rev_resolution(null_recs)
    res["weakening_real"], res["weakening_null"] = weakening_study(real_recs, p.weak_horizon), weakening_study(null_recs, p.weak_horizon)
    z = {}
    for o in ("UP", "DOWN"):
        a = res["resolution_real"]["by_old_trend"].get(o); b = res["resolution_null"]["by_old_trend"].get(o)
        if a and b and a["confirmed"] + a["failed"] and b["confirmed"] + b["failed"]:
            z[o] = {"real": a["p_confirmed_given_resolved"], "null": b["p_confirmed_given_resolved"], "n_real": a["confirmed"] + a["failed"], "n_null": b["confirmed"] + b["failed"],
                    "z": round(two_sample_z(a["confirmed"], a["confirmed"] + a["failed"], b["confirmed"], b["confirmed"] + b["failed"]), 2)}
    res["confirmation_vs_null"] = z
    pz = {}
    for h, a in res["resolution_real"]["persistence_after_confirmation"].items():
        b = res["resolution_null"]["persistence_after_confirmation"][h]
        if a["n"] and b["n"]:
            pz[h] = {"real": a["p_in_new_trend"], "null": b["p_in_new_trend"], "n_real": a["n"], "n_null": b["n"],
                     "z": round(two_sample_z(round(a["p_in_new_trend"] * a["n"]), a["n"], round(b["p_in_new_trend"] * b["n"]), b["n"]), 2)}
    res["persistence_vs_null"] = pz
    n_real = sum(1 for c, r in real_recs if r.session_index > 0); n_null = sum(1 for c, r in null_recs if r.session_index > 0)
    er = Counter(e.label for c, r in real_recs for e in r.events); en = Counter(e.label for c, r in null_recs for e in r.events)
    res["events_per_1000_bars_real"] = {k: round(1000 * v / n_real, 2) for k, v in sorted(er.items())} if n_real else {}
    res["events_per_1000_bars_null"] = {k: round(1000 * v / n_null, 2) for k, v in sorted(en.items())} if n_null else {}
    return res
