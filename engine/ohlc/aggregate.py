"""Exact upward aggregation of completed 1-minute candles (Foundations card 17).

PROJECT DEFINITION: a k-minute bar anchored at the session open (09:15 IST) covers
[anchor + n*k, anchor + (n+1)*k). open = first open, high = max high, low = min low,
close = last close, in sub-bar order. A bar is emitted only when its last sub-bar has
ended or when the session changes / the input ends (``keep_partial`` controls the tail).

This is representation, not analysis: it is lossless upward and impossible downward.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta
from typing import Iterable, Iterator, Optional

from .candle import Candle
from .session import NSE_BSE_REGULAR, SessionSpec, session_date


def anchor_start(ts: datetime, k_minutes: int, spec: SessionSpec = NSE_BSE_REGULAR) -> datetime:
    """Start of the k-minute bucket containing ``ts`` (bucket grid anchored at session open)."""
    local = ts.astimezone(spec.tz)
    anchor = datetime.combine(local.date(), spec.open, tzinfo=spec.tz)
    n = int((local - anchor).total_seconds() // (60 * k_minutes))
    return anchor + timedelta(minutes=k_minutes * n)


def aggregate(candles: Iterable[Candle], k_minutes: int, keep_partial: bool = False,
              spec: SessionSpec = NSE_BSE_REGULAR) -> Iterator[Candle]:
    """Yield completed k-minute candles from an ordered stream of 1-minute candles.

    A bucket is complete when the next 1m candle starts at or after the bucket end, when the
    session date changes, or when the input ends with a sub-bar whose end equals the bucket end.
    ``keep_partial=True`` also flushes a final bucket that is still unfinished at end of input
    (it is then a FORMING bar and the caller must treat it as such).
    """
    k = timedelta(minutes=k_minutes)
    cur: Optional[list] = None   # [start, o, h, l, c, count]
    cur_date = None
    last_end = None
    for c in candles:
        if c.interval != timedelta(minutes=1):
            raise ValueError("aggregate() consumes 1-minute candles only")
        start = anchor_start(c.timestamp, k_minutes, spec)
        d = session_date(c.timestamp, spec)
        if cur is not None and (c.timestamp >= cur[0] + k or d != cur_date):
            yield Candle(cur[0], cur[1], cur[2], cur[3], cur[4], k)
            cur = None
        if cur is None:
            cur = [start, c.open, c.high, c.low, c.close, 1]
            cur_date = d
        else:
            cur[2] = max(cur[2], c.high)
            cur[3] = min(cur[3], c.low)
            cur[4] = c.close
            cur[5] += 1
        last_end = c.end
    if cur is not None and (keep_partial or last_end == cur[0] + k):
        yield Candle(cur[0], cur[1], cur[2], cur[3], cur[4], k)
