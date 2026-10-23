"""Market-state studies: shares, durations, transitions, persistence, next-bar outcomes by state (vs random walk)."""
from __future__ import annotations

from collections import Counter, defaultdict

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .classify import replay_market_state
from .params import MarketStateParams, DEFAULT_MS_PARAMS, STATES


def ms_labels_by_key(items, p: MarketStateParams = DEFAULT_MS_PARAMS):
    """Labels per bar: the state itself (every bar) and STATE_<new> at changes, plus state|vol."""
    out, recs = {}, []
    for i, c, r in replay_market_state(items, p):
        labs = [r.state, f"{r.state}|{r.vol_regime}"] + list(r.events)
        out[(r.timestamp, int(c.interval.total_seconds()))] = labs
        recs.append(r)
    return out, recs


def ms_stats(recs, horizons=(5, 15)) -> dict:
    n = sum(1 for r in recs if r.session_index > 0)
    share = Counter(r.state for r in recs if r.session_index > 0)
    vol = Counter(r.vol_regime for r in recs if r.session_index > 0)
    # durations
    runs = defaultdict(list); cur = None; length = 0
    for r in recs:
        if r.session_index == 0 or r.state != cur:
            if cur is not None and length:
                runs[cur].append(length)
            cur, length = r.state, 1
        else:
            length += 1
    if cur is not None and length:
        runs[cur].append(length)
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    # transition matrix (within session)
    trans = Counter()
    for a, b in zip(recs, recs[1:]):
        if b.session_index > 0 and a.state != b.state:
            trans[(a.state, b.state)] += 1
    tot_from = Counter(); [tot_from.__setitem__(a, tot_from[a] + v) for (a, b), v in trans.items()]
    matrix = {a: {b: round(trans[(a, b)] / tot_from[a], 3) for b in STATES if tot_from[a]} for a in STATES}
    # persistence: P(state unchanged h bars later, same session)
    pers = {}
    for h in horizons:
        k = Counter(); m = Counter()
        for i, r in enumerate(recs):
            j = i + h
            if j < len(recs) and recs[j].session_index == r.session_index + h and r.session_index > 0:
                m[r.state] += 1; k[r.state] += recs[j].state == r.state
        pers[str(h)] = {s: round(k[s] / m[s], 4) for s in STATES if m[s]}
    return {"bars": n, "state_share": {s: round(share.get(s, 0) / n, 4) for s in STATES} if n else {},
            "vol_share": {k_: round(v / n, 4) for k_, v in sorted(vol.items())} if n else {},
            "median_duration_bars": {s: med(runs.get(s, [])) for s in STATES}, "episodes": {s: len(runs.get(s, [])) for s in STATES},
            "transition_matrix": matrix, "persistence": pers}


def ms_study(items, null_items, p: MarketStateParams = DEFAULT_MS_PARAMS) -> dict:
    real_map, real_recs = ms_labels_by_key(items, p)
    null_map, null_recs = ms_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["stats_real"] = ms_stats(real_recs); res["stats_null"] = ms_stats(null_recs)
    # persistence z: real vs null per state and horizon. Consecutive bars of one episode are not independent, so the
    # effective sample size is the number of EPISODES of the state, not the number of bars (lesson 2026-10-04: with bar
    # counts two pure random walks differed by z = 5).
    pz = {}
    for h in ("5", "15"):
        for s in STATES:
            pr = res["stats_real"]["persistence"].get(h, {}).get(s); pn = res["stats_null"]["persistence"].get(h, {}).get(s)
            er = res["stats_real"]["episodes"].get(s, 0); en = res["stats_null"]["episodes"].get(s, 0)
            if pr is not None and pn is not None and er and en:
                pz[f"{s}@{h}"] = {"real": pr, "null": pn, "z": round(two_sample_z(round(pr * er), er, round(pn * en), en), 2), "episodes_real": er, "episodes_null": en}
    res["persistence_vs_null"] = pz
    return res
