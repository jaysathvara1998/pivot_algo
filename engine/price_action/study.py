"""Price-location study: next-bar outcomes after location / leg / session-extreme labels, real vs random walk."""
from __future__ import annotations

from collections import Counter

from engine.behaviour.study import OUTCOMES, conditional_study
from .describe import replay_price_action
from .params import PriceActionParams, DEFAULT_PA_PARAMS


def pa_labels_by_key(items, p: PriceActionParams = DEFAULT_PA_PARAMS):
    out, recs = {}, []
    for i, c, r in replay_price_action(items, p):
        out[(r.timestamp, int(c.interval.total_seconds()))] = list(r.events7)
        recs.append(r)
    return out, recs


def coverage_stats(recs) -> dict:
    n = sum(1 for r in recs if r.session_index > 0)
    zones = Counter(r.struct_zone for r in recs if r.session_index > 0)
    legs = Counter(r.leg_type for r in recs if r.session_index > 0)
    ev = Counter(e for r in recs for e in r.events7)
    return {"bars": n, "struct_zone_share": {k: round(v / n, 4) for k, v in sorted(zones.items())} if n else {},
            "leg_type_share": {k: round(v / n, 4) for k, v in sorted(legs.items())} if n else {},
            "events_per_1000_bars": {k: round(1000 * v / n, 2) for k, v in sorted(ev.items())} if n else {}}


def pa_study(items, null_items, p: PriceActionParams = DEFAULT_PA_PARAMS) -> dict:
    real_map, real_recs = pa_labels_by_key(items, p)
    null_map, null_recs = pa_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["coverage_real"] = coverage_stats(real_recs)
    res["coverage_null"] = coverage_stats(null_recs)
    return res
