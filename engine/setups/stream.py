"""Build the per-bar stream the evaluator consumes from the Level 2–15 engines."""
from __future__ import annotations

from engine.reversals import ReversalEngine, ReversalParams, DEFAULT_REV_PARAMS
from engine.context import ContextEngine


def build_stream(items, interval_minutes: int, rev_params: ReversalParams = DEFAULT_REV_PARAMS, with_context: bool = True) -> list:
    """items: (candle, anatomy record) pairs of one interval. Returns a list of dicts: candle, rec, labels (Level 3 labels + Level 4/7
    events + Level 12 events + context labels), state, vol, session_index, fill (session outcome, filled in afterwards for the first bar)."""
    rev = ReversalEngine(rev_params)
    ctx = ContextEngine() if with_context else None
    out = []
    session_rows = []
    for c, r in items:
        rr = rev.process(c, r)
        pa = rev._pb.last_pa_record; ms = rev._pb.last_ms_record
        labels = list(pa.labels3) + list(pa.events4) + list(pa.events7) + [e.label for e in rr.events]
        row = {"candle": c, "rec": r, "labels": labels, "state": ms.state, "vol": ms.vol_regime, "session_index": pa.session_index, "fill": None}
        if ctx is not None:
            cr = ctx.process(c, r)
            if cr.session_index == 0:
                labels.extend(cr.labels + ["FIRST_BAR"])
                session_rows = [row]
            else:
                session_rows.append(row)
            if cr.gap_class is not None:
                session_rows[0]["_fill_now"] = cr.gap_filled          # the session's running fill; its last value is the outcome
        out.append(row)
    # the FILL outcome of a session is known only at its end: copy the final running value to the first bar
    first = None
    for row in out:
        if row["session_index"] == 0:
            first = row
        if first is not None and "_fill_now" in first:
            first["fill"] = first["_fill_now"]
    for row in out:
        row.pop("_fill_now", None)
    return out
