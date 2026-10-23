"""Streaming anatomy: one record per completed candle, computed with no look-ahead.

Session handling: candles are fed in order; a change of IST calendar date closes the previous
session (its candles enter the reference) before the new session's first candle is analysed.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import timedelta
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, range_, session_date
from .params import AnatomyParams, DEFAULT_PARAMS
from .reference import ClockReference, SizeClass, size_class, _median
from .shape import shape

# Words that would turn an observation into an interpretation (Level 3+) — never emitted here.
FORBIDDEN_WORDS = (
    "rejection", "pressure", "acceptance", "strong", "weak", "buyers", "sellers", "momentum",
    "support", "resistance", "reversal", "continuation", "indecision", "exhaustion", "aggressive",
    "institutional", "smart money", "breakout", "trend", "bullish engulfing", "hammer", "shooting star",
)


@dataclass
class AnatomyRecord:
    timestamp: str
    interval_seconds: int
    session_index: int                    # 0 = first candle of the session
    first_bar: bool
    static_window: bool                   # inside the CAS static window (regime >= 2026-08-03)
    shape: dict
    reference_sessions: int               # how many sessions back the reference median
    reference_range: Optional[float]
    relative_range: Optional[float]       # range / same-minute median of previous sessions
    reference_percentile: Optional[float]
    relative_body: Optional[float]
    size_class: str
    local_relative_range: Optional[float]  # deseasonalised range / median of previous k deseasonalised ranges (same session, first bar excluded)
    flags: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class AnatomyEngine:
    def __init__(self, p: AnatomyParams = DEFAULT_PARAMS):
        self.p = p
        self.ref = ClockReference(p)
        self._session = None
        self._idx = -1
        self._local: list[float] = []     # deseasonalised ranges of this session's non-first bars

    def _start_session(self, d) -> None:
        if self._session is not None:
            self.ref.close_session()
        self._session = d
        self._idx = -1
        self._local = []

    def process(self, c: Candle) -> AnatomyRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            self._start_session(d)
        self._idx += 1
        p = self.p
        rng = range_(c)
        static = self.ref.is_static_window(c)
        first = self._idx == 0
        flags = []
        if first:
            flags.append("FIRST_BAR")
        if static:
            flags.append("STATIC_WINDOW")
        if rng == 0:
            flags.append("ZERO_RANGE")

        ref_r = self.ref.reference_range(c)
        rel = self.ref.relative_range(c)
        pct = self.ref.percentile(c)
        relb = self.ref.relative_body(c)
        if static:
            cls = SizeClass.REFERENCE_UNAVAILABLE if rng else SizeClass.ZERO
            rel = pct = relb = None
            ref_r = None
        elif ref_r is None:
            cls = SizeClass.ZERO if rng == 0 else SizeClass.REFERENCE_UNAVAILABLE
            flags.append("REFERENCE_UNAVAILABLE")
        elif ref_r == 0:
            cls = SizeClass.ZERO if rng == 0 else SizeClass.REFERENCE_ZERO
            flags.append("REFERENCE_ZERO")
        else:
            cls = size_class(rel, rng, p)

        local = None
        if rel is not None and not first:
            if len(self._local) >= p.local_window_bars:
                base = _median(self._local[-p.local_window_bars:])
                local = rel / base if base > 0 else None
            self._local.append(rel)

        # the candle now enters the current session's pending reference (used only after close)
        self.ref.observe(c)
        return AnatomyRecord(
            timestamp=c.timestamp.isoformat(),
            interval_seconds=int(c.interval.total_seconds()),
            session_index=self._idx,
            first_bar=first,
            static_window=static,
            shape=shape(c, p),
            reference_sessions=len(self.ref.history(c)) if not static else 0,
            reference_range=ref_r,
            relative_range=rel,
            reference_percentile=pct,
            relative_body=relb,
            size_class=cls.value,
            local_relative_range=local,
            flags=flags,
        )

    def observations(self, c: Candle, rec: AnatomyRecord) -> list[str]:
        """Level 2 OBSERVATIONS: numbers plus defined class names with their parameters. No meaning."""
        p, s = self.p, rec.shape
        out = []
        if s["range"] == 0:
            out.append("Range is zero; shape ratios and relative size are undefined.")
        else:
            out.append(f"Body is {s['body_to_range']:.3f} of range (body class {s['body_class']}; doji threshold {p.doji_max_body_to_range}).")
            out.append(f"Upper wick {s['upper_wick_to_range']:.3f}, lower wick {s['lower_wick_to_range']:.3f} of range (wick profile {s['wick_profile']}).")
            out.append(f"Close at {s['close_location']:.3f} of range from the low (close zone {s['close_zone']}).")
        if rec.first_bar:
            out.append("First bar of the session: compared only with previous first bars.")
        if rec.static_window:
            out.append("Inside the 15:15-15:29 static index window (Closing Auction Session regime); relative size not computed.")
        elif rec.relative_range is not None:
            out.append(f"Range is {rec.relative_range:.2f}x the median of the previous {rec.reference_sessions} sessions at this clock minute "
                       f"(size class {rec.size_class}; thresholds {p.small_max_ratio}/{p.large_min_ratio}/{p.extreme_min_ratio}); "
                       f"larger than {100 * rec.reference_percentile:.0f}% of them.")
        elif rec.size_class == SizeClass.REFERENCE_UNAVAILABLE.value:
            out.append(f"Fewer than {p.min_reference_sessions} reference sessions for this clock minute; relative size unavailable.")
        elif rec.size_class == SizeClass.REFERENCE_ZERO.value:
            out.append("Reference median range is zero at this clock minute; ratio undefined.")
        if rec.local_relative_range is not None:
            out.append(f"Deseasonalised range is {rec.local_relative_range:.2f}x the median of the previous {p.local_window_bars} bars of this session.")
        return out


def replay_anatomy(candles: Iterable[Candle], p: AnatomyParams = DEFAULT_PARAMS) -> Iterator[tuple[int, Candle, AnatomyRecord]]:
    """Feed candles one at a time; yield (i, candle, record). Each record depends only on candles 0..i."""
    eng = AnatomyEngine(p)
    for i, c in enumerate(candles):
        yield i, c, eng.process(c)
