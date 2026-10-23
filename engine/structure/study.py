"""Structure studies: state shares and durations, swing-relation persistence, next-bar outcomes after events."""
from __future__ import annotations

from collections import Counter, defaultdict
from typing import Sequence

from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .state import replay_structure, STATES
from .params import StructureParams, DEFAULT_STRUCTURE_PARAMS


def structure_labels_by_key(items, p: StructureParams = DEFAULT_STRUCTURE_PARAMS):
    out, recs = {}, []
    for i, c, s in replay_structure(items, p):
        out[(s.timestamp, int(c.interval.total_seconds()))] = [e.label for e in s.events]
        recs.append(s)
    return out, recs


def state_stats(recs) -> dict:
    n = sum(1 for r in recs if r.session_index > 0)
    share = Counter(r.trend_state for r in recs if r.session_index > 0)
    # durations: count bars per uninterrupted state run (within session)
    runs = defaultdict(list); cur = None; length = 0
    for r in recs:
        if r.session_index == 0 or r.trend_state != cur:
            if cur is not None and length:
                runs[cur].append(length)
            cur, length = r.trend_state, 1
        else:
            length += 1
    if cur is not None and length:
        runs[cur].append(length)
    ev = Counter(e.label for r in recs for e in r.events)
    def med(xs):
        s = sorted(xs); return s[len(s) // 2] if s else None
    return {"bars": n, "state_share": {s: round(share.get(s, 0) / n, 4) for s in STATES} if n else {},
            "median_state_duration_bars": {s: med(runs.get(s, [])) for s in STATES},
            "events_per_1000_bars": {k: round(1000 * v / n, 2) for k, v in sorted(ev.items())} if n else {}}


def swing_relation_study(recs_real, recs_null) -> dict:
    """Persistence: given the trend state when a new swing high (low) is confirmed, how often is it HH (HL)?
    Also: in UP, is the next structure break a BOS (continuation) or a CHOCH (reversal)?"""
    def persist(recs):
        c = Counter()
        prev_state = None; prev_h = None; prev_l = None
        for r in recs:
            if r.session_index == 0:
                prev_state, prev_h, prev_l = None, None, None
            if prev_state in ("UP", "DOWN"):
                if r.last_high_relation is not None and (prev_h is None or r.last_high != prev_h):
                    c[(prev_state, "H", r.last_high_relation)] += 1
                if r.last_low_relation is not None and (prev_l is None or r.last_low != prev_l):
                    c[(prev_state, "L", r.last_low_relation)] += 1
            prev_state, prev_h, prev_l = r.trend_state, r.last_high, r.last_low
        return c

    def breaks(recs):
        c = Counter(e.label for r in recs for e in r.events if e.label.startswith(("BOS_", "CHOCH_")))
        return c
    out = {}
    pr, pn = persist(recs_real), persist(recs_null)
    for state, kind, cont in (("UP", "H", "HH"), ("UP", "L", "HL"), ("DOWN", "H", "LH"), ("DOWN", "L", "LL")):
        kr = pr[(state, kind, cont)]; nr = sum(v for (s, k, rel), v in pr.items() if s == state and k == kind and rel not in ("FIRST", "EQUAL"))
        kn = pn[(state, kind, cont)]; nn = sum(v for (s, k, rel), v in pn.items() if s == state and k == kind and rel not in ("FIRST", "EQUAL"))
        out[f"P({cont} | {state}, new {'high' if kind == 'H' else 'low'})"] = {"real": round(kr / nr, 4) if nr else None, "n_real": nr, "null": round(kn / nn, 4) if nn else None, "n_null": nn,
                                                                            "z": round(two_sample_z(kr, nr, kn, nn), 2)}
    br, bn = breaks(recs_real), breaks(recs_null)
    for side in ("UP", "DOWN"):
        bos, ch = br[f"BOS_{side}"], br[f"CHOCH_{'DOWN' if side == 'UP' else 'UP'}"]
        bos_n, ch_n = bn[f"BOS_{side}"], bn[f"CHOCH_{'DOWN' if side == 'UP' else 'UP'}"]
        out[f"P(BOS | trend {side}, first break)"] = {"real": round(bos / (bos + ch), 4) if bos + ch else None, "n_real": bos + ch,
                                                      "null": round(bos_n / (bos_n + ch_n), 4) if bos_n + ch_n else None, "n_null": bos_n + ch_n,
                                                      "z": round(two_sample_z(bos, bos + ch, bos_n, bos_n + ch_n), 2)}
    return out


def structure_study(items, null_items, p: StructureParams = DEFAULT_STRUCTURE_PARAMS) -> dict:
    real_map, real_recs = structure_labels_by_key(items, p)
    null_map, null_recs = structure_labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    res = conditional_study(items, lambda r: merged.get((r.timestamp, r.interval_seconds), []), OUTCOMES, null_items=null_items)
    res["state_stats_real"] = state_stats(real_recs)
    res["state_stats_null"] = state_stats(null_recs)
    res["persistence"] = swing_relation_study(real_recs, null_recs)
    return res
