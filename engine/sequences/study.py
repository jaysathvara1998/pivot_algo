"""Sequence studies reusing the Level 3 null machinery."""
from __future__ import annotations

from collections import Counter
from typing import Sequence

from engine.ohlc import Candle
from engine.anatomy import AnatomyRecord
from engine.behaviour.study import OUTCOMES, conditional_study, two_sample_z
from .state import SequenceRecord, replay_sequences
from .params import SequenceParams, DEFAULT_SEQUENCE_PARAMS


def labels_by_key(items: Sequence[tuple[Candle, AnatomyRecord]], p: SequenceParams = DEFAULT_SEQUENCE_PARAMS) -> tuple[dict, list[SequenceRecord]]:
    """Map (timestamp iso, interval seconds) -> list of event labels confirmed at that bar."""
    out = {}
    seqs = []
    for i, c, s in replay_sequences(items, p):
        out[(s.timestamp, int(c.interval.total_seconds()))] = [e.label for e in s.events]
        seqs.append(s)
    return out, seqs


def _labels_fn(mapping: dict):
    return lambda r: mapping.get((r.timestamp, r.interval_seconds), [])


def streak_study(items, null_items, p: SequenceParams = DEFAULT_SEQUENCE_PARAMS) -> dict:
    """Next-bar outcomes after each confirmed sequence event, against the Level 3 nulls.

    conditional_study applies one labels_of to both populations; the label maps are keyed by
    (timestamp, interval) and the synthetic population lives on 2020 dates, so one merged map serves both."""
    real_map, _ = labels_by_key(items, p)
    null_map, _ = labels_by_key(null_items, p)
    merged = dict(null_map); merged.update(real_map)
    return conditional_study(items, _labels_fn(merged), OUTCOMES, null_items=null_items)


def event_shares(seqs: Sequence[SequenceRecord]) -> dict:
    n = sum(1 for s in seqs if s.session_index > 0)
    cnt = Counter(e.label for s in seqs for e in s.events)
    return {"bars": n, "events_per_1000_bars": {k: round(1000 * v / n, 2) for k, v in sorted(cnt.items())} if n else {}}


def pullback_study(seqs_real: Sequence[SequenceRecord], seqs_null: Sequence[SequenceRecord]) -> dict:
    """How impulses resolve after a pullback starts: CONTINUATION / FAILED / EXPIRED, real vs random walk."""
    def counts(seqs):
        c = Counter()
        for s in seqs:
            for e in s.events:
                if e.label.startswith(("CONTINUATION_", "FAILED_MOVEMENT_", "PULLBACK_EXPIRED_", "PULLBACK_START_", "IMPULSE_")):
                    c[e.label.rsplit("_", 1)[0]] += 1
        return c
    out = {}
    cr, cn = counts(seqs_real), counts(seqs_null)

    def block(c):
        imp = c["IMPULSE"]; start = c["PULLBACK_START"]
        cont, fail, exp = c["CONTINUATION"], c["FAILED_MOVEMENT"], c["PULLBACK_EXPIRED"]
        res = cont + fail
        return {"impulses": imp, "pullbacks_started": start, "continuation": cont, "failed": fail, "expired": exp,
                "p_resolved": round(res / start, 4) if start else None, "p_continuation_given_resolved": round(cont / res, 4) if res else None,
                "p_continuation_given_pullback": round(cont / start, 4) if start else None}
    out["real"], out["null"] = block(cr), block(cn)
    r, n = out["real"], out["null"]
    out["z_continuation_given_resolved"] = round(two_sample_z(r["continuation"], r["continuation"] + r["failed"], n["continuation"], n["continuation"] + n["failed"]), 2)
    out["z_resolved"] = round(two_sample_z(r["continuation"] + r["failed"], r["pullbacks_started"], n["continuation"] + n["failed"], n["pullbacks_started"]), 2)
    out["diff_continuation_given_resolved"] = round((r["p_continuation_given_resolved"] or 0) - (n["p_continuation_given_resolved"] or 0), 4)
    return out
