"""Ordered candle series with inter-candle facts, built incrementally.

No-look-ahead is enforced by construction: a candle can only be *appended*, and every
derived value for candle ``i`` is computed from candles ``<= i`` at the moment of append.
``replay`` exposes the state after each append so tests can prove that
state(after N) == state(built from first N alone).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable, Iterator, Optional

from .candle import Candle


class SeriesError(ValueError):
    """code ∈ {NON_INCREASING_TIMESTAMP, DUPLICATE_TIMESTAMP, INTERVAL_MISMATCH}"""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


@dataclass(frozen=True)
class InterCandle:
    """Facts relating candle ``i`` to candle ``i-1`` (PROJECT DEFINITIONS).

    gap              = open_i - close_{i-1}           (signed; >0 gap up, <0 gap down)
    inter_change     = close_i - close_{i-1}          (signed)
    rel_inter_change = inter_change / close_{i-1}
    time_gap         = start_i - end_{i-1}            (timedelta(0) when contiguous)
    contiguous       = time_gap == 0
    """

    gap: float
    inter_change: float
    rel_inter_change: float
    time_gap: timedelta
    contiguous: bool


class CandleSeries:
    """Append-only, strictly time-ordered, single-interval candle series."""

    def __init__(self, interval: timedelta):
        if interval <= timedelta(0):
            raise ValueError("interval must be positive")
        self.interval = interval
        self._candles: list[Candle] = []
        self._inter: list[Optional[InterCandle]] = []

    # ----------------------------------------------------------------- mutation
    def append(self, c: Candle) -> Optional[InterCandle]:
        if c.interval != self.interval:
            raise SeriesError("INTERVAL_MISMATCH", f"candle interval {c.interval} != series {self.interval}")
        prev = self._candles[-1] if self._candles else None
        if prev is not None:
            if c.timestamp == prev.timestamp:
                raise SeriesError("DUPLICATE_TIMESTAMP", f"{c.timestamp.isoformat()} already present")
            if c.timestamp < prev.timestamp:
                raise SeriesError(
                    "NON_INCREASING_TIMESTAMP",
                    f"{c.timestamp.isoformat()} <= last {prev.timestamp.isoformat()}",
                )
        inter = None if prev is None else _inter(prev, c)
        self._candles.append(c)
        self._inter.append(inter)
        return inter

    def extend(self, candles: Iterable[Candle]) -> None:
        for c in candles:
            self.append(c)

    # ------------------------------------------------------------------ access
    def __len__(self) -> int:
        return len(self._candles)

    def __getitem__(self, i: int) -> Candle:
        return self._candles[i]

    @property
    def last(self) -> Optional[Candle]:
        return self._candles[-1] if self._candles else None

    def inter(self, i: int) -> Optional[InterCandle]:
        """Inter-candle facts for index i (None for i == 0)."""
        return self._inter[i]

    def candles(self) -> tuple[Candle, ...]:
        return tuple(self._candles)

    def state(self) -> dict:
        """Deterministic snapshot of everything known so far (used by replay tests)."""
        last = self.last
        li = self._inter[-1] if self._inter else None
        return {
            "n": len(self),
            "last_timestamp": last.timestamp.isoformat() if last else None,
            "last_close": last.close if last else None,
            "last_gap": li.gap if li else None,
            "last_inter_change": li.inter_change if li else None,
            "last_time_gap_seconds": int(li.time_gap.total_seconds()) if li else None,
        }


def _inter(prev: Candle, cur: Candle) -> InterCandle:
    inter_change = cur.close - prev.close
    tg = cur.timestamp - prev.end
    return InterCandle(
        gap=cur.open - prev.close,
        inter_change=inter_change,
        rel_inter_change=inter_change / prev.close,
        time_gap=tg,
        contiguous=(tg == timedelta(0)),
    )


def replay(candles: Iterable[Candle], interval: timedelta) -> Iterator[tuple[int, Candle, dict]]:
    """Feed candles one at a time; yield (index, candle, series.state()) after each.

    This is the T0 → new candle → update → T1 loop. The consumer never sees candle i+1
    while processing candle i.
    """
    s = CandleSeries(interval)
    for i, c in enumerate(candles):
        s.append(c)
        yield i, c, s.state()
