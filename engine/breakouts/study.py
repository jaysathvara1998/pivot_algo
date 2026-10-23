"""Breakout studies: next-bar outcomes after events (Level 3 nulls) and horizon follow-through after breaks and tests."""
from __future__ import annotations

from collections import Counter, defaultdict

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .machine import replay_breakouts
from .params import BreakoutParams, DEFAULT_BREAKOUT_PARAMS


def breakout_labels_by_key(items, p: BreakoutParams = DEFAULT_BREAKOUT_PARAMS):
    out, recs = {}, []
    for i, c, r in replay_breakouts(items, p):
        out[(r.timestamp, int(c.interval.total_seconds()))] = sorted(set(e.label for e in r.events))
        recs.append((c, r))
    return out, recs


def _horizon_counts(recs, horizons):
    """For each BREAK event: is the close still beyond the level h bars later (same session)? For each TEST event: is the
    level crossed by a close within h bars? Returns {label: {h: [k_half0, n_half0, k_half1, n_half1]}}."""
    out = defaultdict(lambda: defaultdict(lambda: [0, 0, 0, 0]))
    n_total = len(recs); half = n_total // 2
    for i, (c, r) in enumerate(recs):
        hh = 0 if i < half else 1
        for e in r.events:
            if e.event not in ("BREAK", "TEST"):
                continue
            for h in horizons:
                j = i + h
                if j >= n_total or recs[j][0].timestamp.date() != c.timestamp.date():
                    continue
                if e.event == "BREAK":
                    cj = recs[j][0].close
                    hit = (cj > e.level) if e.direction == "UP" else (cj < e.level)
                else:
                    hit = any((recs[m][0].close > e.level) if e.direction == "UP" else (recs[m][0].close < e.level) for m in range(i + 1, j + 1))
                for key in (f"{e.event}_{e.direction}|{e.group}", f"{e.event}_{e.direction}|{e.family}"):   # pooled by group and per family
                    cell = out[key][h]
                    cell[2 * hh] += hit; cell[2 * hh + 1] += 1
    return out


def horizon_study(recs_real, recs_null, horizons) -> dict:
    hr, hn = _horizon_counts(recs_real, horizons), _horizon_counts(recs_null, horizons)
    out = {}
    for lab in sorted(set(hr) | set(hn)):
        out[lab] = {}
        for h in horizons:
            k0, n0, k1, n1 = hr[lab][h]; nk0, nn0, nk1, nn1 = hn[lab][h]
            n, k, nn, nk = n0 + n1, k0 + k1, nn0 + nn1, nk0 + nk1
            p = k / n if n else None; pn = nk / nn if nn else None
            out[lab][str(h)] = {"n": n, "p": round(p, 4) if p is not None else None, "n_null": nn, "p_null": round(pn, 4) if pn is not None else None,
                                "diff": round(p - pn, 4) if (p is not None and pn is not None) else None, "z": round(two_sample_z(k, n, nk, nn), 2),
                                "halves": [{"n": n0, "diff": round(k0 / n0 - pn, 4) if (n0 and pn is not None) else None},
                                           {"n": n1, "diff": round(k1 / n1 - pn, 4) if (n1 and pn is not None) else None}]}
    return out


def breakout_study(items, null_items, p: BreakoutParams = DEFAULT_BREAKOUT_PARAMS, null_p: BreakoutParams = None) -> dict:
    real_map, real_recs = breakout_labels_by_key(items, p)
    null_map, null_recs = breakout_labels_by_key(null_items, null_p or p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["horizon"] = horizon_study(real_recs, null_recs, p.horizons)
    n_real = sum(1 for c, r in real_recs if r.session_index > 0); n_null = sum(1 for c, r in null_recs if r.session_index > 0)
    er = Counter(e.label for c, r in real_recs for e in r.events); en = Counter(e.label for c, r in null_recs for e in r.events)
    res["events_per_1000_bars_real"] = {k: round(1000 * v / n_real, 2) for k, v in sorted(er.items())} if n_real else {}
    res["events_per_1000_bars_null"] = {k: round(1000 * v / n_null, 2) for k, v in sorted(en.items())} if n_null else {}
    # resolution of breaks: acceptance vs failed, per group and direction
    def resolution(recs):
        c = Counter()
        for _, r in recs:
            for e in r.events:
                if e.event in ("ACCEPTANCE", "FAILED_BREAK", "BREAK"):
                    c[(e.event, e.direction, e.group)] += 1
        out = {}
        for (ev, d, g), v in c.items():
            if ev == "BREAK":
                a = c[("ACCEPTANCE", d, g)]; f = c[("FAILED_BREAK", d, g)]
                out[f"{d}|{g}"] = {"breaks": v, "accepted": a, "failed": f, "p_accept_given_resolved": round(a / (a + f), 4) if a + f else None}
        return out
    res["resolution_real"] = resolution(real_recs); res["resolution_null"] = resolution(null_recs)
    return res
