"""Level studies: touch frequencies per session, round-number clustering, next-bar outcomes near/crossing levels."""
from __future__ import annotations

import math
from collections import Counter, defaultdict

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .registry import replay_levels, FAMILIES
from .params import LevelParams, DEFAULT_LEVEL_PARAMS


def level_labels_by_key(items, p: LevelParams = DEFAULT_LEVEL_PARAMS):
    out, recs = {}, []
    for i, c, r in replay_levels(items, p):
        out[(r.timestamp, int(c.interval.total_seconds()))] = list(r.labels)
        recs.append((c, r))
    return out, recs


def touch_stats(recs) -> dict:
    """Per family: share of sessions in which a level of the family was touched (bar range spans it) after it became
    known, mean touch-bars per session, and the share of sessions in which a close went beyond PDH / PDL."""
    sessions = defaultdict(list)
    for c, r in recs:
        sessions[c.timestamp.date()].append((c, r))
    have, touched, touches, beyond = Counter(), Counter(), Counter(), Counter()
    for d, bars in sessions.items():
        for f in set(f for c, r in bars for f in r.levels):
            have[f] += 1
        t_here, b_here = set(), set()
        for c, r in bars:
            for f in r.touches:
                touches[f] += 1; t_here.add(f)
            if "PDH" in r.levels and c.close > r.levels["PDH"][0]:
                b_here.add("PDH")
            if "PDL" in r.levels and c.close < r.levels["PDL"][0]:
                b_here.add("PDL")
        for f in t_here:
            touched[f] += 1
        for f in b_here:
            beyond[f] += 1
    return {"sessions": len(sessions),
            "share_sessions_touched": {f: round(touched[f] / have[f], 4) for f in FAMILIES if have[f]},
            "mean_touch_bars_per_session": {f: round(touches[f] / have[f], 2) for f in FAMILIES if have[f]},
            "share_sessions_close_beyond": {f: round(beyond[f] / have[f], 4) for f in ("PDH", "PDL") if have[f]}}


def round_number_stats(recs, grid: float, bins: int = 10) -> dict:
    """Distribution of (close mod grid)/grid in equal bins; uniform under a random walk. Also share of closes exactly on grid."""
    cnt = [0] * bins; n = 0; on = 0
    for c, r in recs:
        if r.session_index == 0:
            continue
        frac = (c.close % grid) / grid
        cnt[min(bins - 1, int(frac * bins))] += 1; n += 1
        if abs(frac) < 1e-9 or abs(frac - 1) < 1e-9:
            on += 1
    exp = n / bins if n else 0
    chi2 = sum((x - exp) ** 2 / exp for x in cnt) if exp else 0.0
    return {"n": n, "bin_shares": [round(x / n, 4) for x in cnt] if n else [], "chi2_vs_uniform_df9": round(chi2, 1),
            "max_abs_dev_from_uniform": round(max(abs(x / n - 1 / bins) for x in cnt), 4) if n else None, "share_exactly_on_grid": round(on / n, 5) if n else None}


def level_study(items, null_items, p: LevelParams = DEFAULT_LEVEL_PARAMS, null_p: LevelParams = None) -> dict:
    real_map, real_recs = level_labels_by_key(items, p)
    null_map, null_recs = level_labels_by_key(null_items, null_p or p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["touch_real"] = touch_stats(real_recs); res["touch_null"] = touch_stats(null_recs)
    res["round_real"] = round_number_stats(real_recs, p.round_grid); res["round_null"] = round_number_stats(null_recs, (null_p or p).round_grid)
    n_real = sum(1 for c, r in real_recs if r.session_index > 0); n_null = sum(1 for c, r in null_recs if r.session_index > 0)
    lab_real = Counter(l for c, r in real_recs for l in r.labels); lab_null = Counter(l for c, r in null_recs for l in r.labels)
    res["labels_per_1000_bars_real"] = {k: round(1000 * v / n_real, 2) for k, v in sorted(lab_real.items())} if n_real else {}
    res["labels_per_1000_bars_null"] = {k: round(1000 * v / n_null, 2) for k, v in sorted(lab_null.items())} if n_null else {}
    return res
