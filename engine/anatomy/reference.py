"""Clock-minute reference for relative size (cards 09, 14, 15).

The reference for a candle at clock minute m is the median range (and body) of the completed
candles at the same clock minute m in the previous N sessions. It is updated only when a
session is closed, so a candle is never compared with anything from its own session or from
the future. The 09:15 bucket is therefore automatically its own distribution, and the static
15:15-15:29 window after 2026-08-03 is excluded from the reference and flagged.
"""
from __future__ import annotations

from collections import deque
from datetime import date, datetime, timedelta
from enum import Enum
from typing import Optional

from engine.ohlc import Candle, body, range_
from .params import AnatomyParams, DEFAULT_PARAMS


class SizeClass(str, Enum):
    ZERO = "ZERO"                       # range == 0
    SMALL = "SMALL"                     # ratio <  small_max_ratio
    NORMAL = "NORMAL"                   # small_max_ratio <= ratio < large_min_ratio
    LARGE = "LARGE"                     # large_min_ratio <= ratio < extreme_min_ratio
    EXTREME = "EXTREME"                 # ratio >= extreme_min_ratio
    REFERENCE_UNAVAILABLE = "REFERENCE_UNAVAILABLE"
    REFERENCE_ZERO = "REFERENCE_ZERO"   # reference median is 0 (grid-saturated minute); ratio undefined


def size_class(ratio: Optional[float], rng: float, p: AnatomyParams = DEFAULT_PARAMS) -> SizeClass:
    if rng == 0:
        return SizeClass.ZERO
    if ratio is None:
        return SizeClass.REFERENCE_UNAVAILABLE
    if ratio < p.small_max_ratio:
        return SizeClass.SMALL
    if ratio < p.large_min_ratio:
        return SizeClass.NORMAL
    if ratio < p.extreme_min_ratio:
        return SizeClass.LARGE
    return SizeClass.EXTREME


def _median(xs) -> float:
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


def clock_key(ts: datetime, interval: timedelta, p: AnatomyParams = DEFAULT_PARAMS) -> tuple[int, int]:
    """(interval seconds, minute of day) — the bucket a candle is compared within."""
    return int(interval.total_seconds()), ts.hour * 60 + ts.minute


class ClockReference:
    """Trailing per-clock-minute reference built from closed sessions only."""

    def __init__(self, p: AnatomyParams = DEFAULT_PARAMS):
        self.p = p
        self._ranges: dict[tuple[int, int], deque] = {}
        self._bodies: dict[tuple[int, int], deque] = {}
        self._pending: dict[tuple[int, int], tuple[float, float]] = {}   # current (open) session, one per key
        self.sessions_closed = 0
        self.current_session: Optional[date] = None

    # -- building ------------------------------------------------------------------------
    def is_static_window(self, c: Candle) -> bool:
        d = c.timestamp.date()
        return d >= self.p.static_window_from and c.timestamp.time() >= self.p.static_window_start

    def observe(self, c: Candle) -> None:
        """Record a completed candle of the *current* session (not yet usable as reference)."""
        if self.is_static_window(c):
            return                                   # CAS static window: never a reference
        self._pending[clock_key(c.timestamp, c.interval, self.p)] = (range_(c), body(c))

    def close_session(self) -> None:
        """Move the current session's candles into the trailing window. Call after the last candle."""
        for key, (r, b) in self._pending.items():
            dq_r = self._ranges.setdefault(key, deque(maxlen=self.p.reference_sessions))
            dq_b = self._bodies.setdefault(key, deque(maxlen=self.p.reference_sessions))
            dq_r.append(r)
            dq_b.append(b)
        self._pending.clear()
        self.sessions_closed += 1

    # -- querying (uses closed sessions only) -------------------------------------------
    def history(self, c: Candle) -> list[float]:
        return list(self._ranges.get(clock_key(c.timestamp, c.interval, self.p), ()))

    def reference_range(self, c: Candle) -> Optional[float]:
        h = self.history(c)
        return _median(h) if len(h) >= self.p.min_reference_sessions else None

    def reference_body(self, c: Candle) -> Optional[float]:
        h = self._bodies.get(clock_key(c.timestamp, c.interval, self.p), ())
        return _median(h) if len(h) >= self.p.min_reference_sessions else None

    def percentile(self, c: Candle) -> Optional[float]:
        """Share of reference ranges strictly below this candle's range (0..1); None if unavailable."""
        h = self.history(c)
        if len(h) < self.p.min_reference_sessions:
            return None
        return sum(1 for x in h if x < range_(c)) / len(h)

    def relative_range(self, c: Candle) -> Optional[float]:
        ref = self.reference_range(c)
        if ref is None or ref == 0:
            return None
        return range_(c) / ref

    def relative_body(self, c: Candle) -> Optional[float]:
        ref = self.reference_body(c)
        if ref is None or ref == 0:
            return None
        return body(c) / ref
