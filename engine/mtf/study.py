"""Multi-timeframe studies: alignment shares, 1m next-bar and horizon outcomes by alignment, 1m trend-episode duration by HTF context."""
from __future__ import annotations

from collections import Counter, defaultdict

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .engine import replay_mtf
from .params import MTFParams, DEFAULT_MTF_PARAMS, ALIGNMENTS


def mtf_labels_by_key(items, p: MTFParams = DEFAULT_MTF_PARAMS):
    out, recs = {}, []
    for i, c, r in replay_mtf(items, p):
        out[(r.timestamp, 60)] = list(r.labels)
        recs.append((c, r))
    return out, recs


def alignment_stats(recs, horizon: int) -> dict:
    n = sum(1 for c, r in recs if r.session_index > 0)
    share = {k: Counter() for k in recs[0][1].htf} if recs else {}
    for c, r in recs:
        if r.session_index > 0:
            for k, v in r.htf.items():
                share[k][v.alignment] += 1
    # horizon: close_{t+h} > close_t by label (same session), on NON-OVERLAPPING windows (every h-th bar) so that the
    # binomial counts are not inflated by overlapping horizons (Level 10 lesson on autocorrelated labels)
    hz = defaultdict(lambda: [0, 0])
    for i, (c, r) in enumerate(recs):
        j = i + horizon
        if r.session_index % horizon == 0 and j < len(recs) and recs[j][1].session_index == r.session_index + horizon:
            up = recs[j][0].close > c.close
            for lab in r.labels:
                hz[lab][0] += up; hz[lab][1] += 1
    # 1m trending episodes: duration by HTF alignment at the episode start
    ep = defaultdict(list); cur = None
    for i, (c, r) in enumerate(recs):
        if r.session_index == 0 or r.ltf_state != (cur[0] if cur else None):
            if cur and cur[0] in ("TRENDING_UP", "TRENDING_DOWN"):
                ep[cur[1]].append(cur[2])
            cur = [r.ltf_state, {k: v.alignment for k, v in r.htf.items()}.get(min(r.htf)), 1] if r.htf else None
        elif cur:
            cur[2] += 1
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    return {"bars": n, "alignment_share": {str(k): {a: round(v[a] / n, 4) for a in ALIGNMENTS if v[a]} for k, v in share.items()} if n else {},
            "horizon_up_share": {lab: {"n": v[1], "p": round(v[0] / v[1], 4)} for lab, v in sorted(hz.items()) if v[1]},
            "ltf_trend_episode_median_bars_by_htf_alignment": {a: {"episodes": len(v), "median": med(v)} for a, v in sorted(ep.items())}}


def mtf_study(items, null_items, p: MTFParams = DEFAULT_MTF_PARAMS) -> dict:
    real_map, real_recs = mtf_labels_by_key(items, p)
    null_map, null_recs = mtf_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["stats_real"] = alignment_stats(real_recs, p.horizon_bars); res["stats_null"] = alignment_stats(null_recs, p.horizon_bars)
    hz = {}
    for lab, a in res["stats_real"]["horizon_up_share"].items():
        b = res["stats_null"]["horizon_up_share"].get(lab)
        if b and a["n"] >= 100 and b["n"] >= 100:
            hz[lab] = {"real": a["p"], "null": b["p"], "n_real": a["n"], "n_null": b["n"],
                       "z": round(two_sample_z(round(a["p"] * a["n"]), a["n"], round(b["p"] * b["n"]), b["n"]), 2)}
    res["horizon_vs_null"] = hz
    return res
