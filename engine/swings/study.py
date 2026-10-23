"""Swing studies: descriptive distributions (real vs random walk) and next-bar outcomes after confirmations."""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from engine.behaviour.study import OUTCOMES, conditional_study
from .detect import replay_swings
from .params import SwingParams, DEFAULT_SWING_PARAMS


def swing_labels_by_key(items, p: SwingParams = DEFAULT_SWING_PARAMS):
    """(timestamp, interval seconds) -> labels confirmed at that bar; also returns all swings."""
    out, swings = {}, []
    for i, c, s in replay_swings(items, p):
        labs = []
        for e in s.events:
            labs.append(e.label)                                     # e.g. ZZ_HIGH_HH
            labs.append(f"{'FR' if e.detector == 'FRACTAL' else 'ZZ'}_{e.kind}")   # pooled by kind
            swings.append(e)
        out[(s.timestamp, int(c.interval.total_seconds()))] = labs
    return out, swings


def _pct(xs, q):
    if not xs:
        return None
    s = sorted(xs); k = max(0, min(len(s) - 1, int(round(q * (len(s) - 1)))))
    return s[k]


def swing_stats(swings, bars: int) -> dict:
    out = {}
    for det in ("FRACTAL", "ZIGZAG"):
        sw = [s for s in swings if s.detector == det]
        sizes = [s.size_ref for s in sw if s.size_ref is not None]
        durs = [s.duration_bars for s in sw if s.duration_bars is not None]
        lags = [s.lag_bars for s in sw]
        rel = Counter(s.relation for s in sw)
        out[det] = {"count": len(sw), "per_1000_bars": round(1000 * len(sw) / bars, 2) if bars else None,
                    "size_ref_p25_p50_p75": [round(_pct(sizes, q), 2) if sizes else None for q in (0.25, 0.5, 0.75)],
                    "share_size_below_1_ref": round(sum(1 for x in sizes if x < 1) / len(sizes), 4) if sizes else None,
                    "duration_p25_p50_p75": [_pct(durs, q) for q in (0.25, 0.5, 0.75)],
                    "lag_p50_p90": [_pct(lags, 0.5), _pct(lags, 0.9)],
                    "relation_shares": {k: round(v / len(sw), 4) for k, v in sorted(rel.items())} if sw else {}}
    return out


def swing_study(items, null_items, p: SwingParams = DEFAULT_SWING_PARAMS) -> dict:
    real_map, real_sw = swing_labels_by_key(items, p)
    null_map, null_sw = swing_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    bars_real = sum(1 for c, r in items if r.session_index > 0); bars_null = sum(1 for c, r in null_items if r.session_index > 0)
    res["swing_stats_real"] = swing_stats(real_sw, bars_real)
    res["swing_stats_null"] = swing_stats(null_sw, bars_null)
    return res
