"""Confirmed swing detection (PROJECT DEFINITION, Skill 05)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from .params import SwingParams, DEFAULT_SWING_PARAMS


@dataclass
class Swing:
    detector: str                 # FRACTAL | ZIGZAG
    kind: str                     # HIGH | LOW
    price: float
    pivot_index: int              # session index of the pivot bar
    pivot_time: str
    confirmed_index: int
    confirmed_at: str
    lag_bars: int                 # confirmed_index - pivot_index
    relation: str                 # FIRST | HH | LH | EQUAL (highs) ; FIRST | HL | LL | EQUAL (lows)
    size_ref: Optional[float]     # |price - previous opposite-kind pivot price| / reference_range(pivot bar)
    duration_bars: Optional[int]  # pivot_index - previous opposite-kind pivot_index
    params: dict = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{'FR' if self.detector == 'FRACTAL' else 'ZZ'}_{self.kind}_{self.relation}"

    def as_dict(self) -> dict:
        d = asdict(self); d["label"] = self.label; return d


@dataclass
class SwingRecord:
    timestamp: str
    session_index: int
    events: list                  # swings confirmed at this bar
    zz_direction: Optional[str]   # UP (tracking a candidate high) | DOWN | None
    zz_candidate_high: Optional[float]
    zz_candidate_low: Optional[float]
    known_swings: int

    def as_dict(self) -> dict:
        d = asdict(self); d["events"] = [e.as_dict() if isinstance(e, Swing) else e for e in self.events]; return d


class _Track:
    """Per-detector memory of the last confirmed swings for relation/size/duration."""

    def __init__(self):
        self.last_high: Optional[Swing] = None
        self.last_low: Optional[Swing] = None

    def make(self, detector, kind, price, pidx, ptime, cidx, ctime, ref, p: SwingParams) -> Swing:
        prev_same = self.last_high if kind == "HIGH" else self.last_low
        prev_opp = self.last_low if kind == "HIGH" else self.last_high
        if prev_same is None:
            rel = "FIRST"
        elif abs(price - prev_same.price) <= p.equal_tol:
            rel = "EQUAL"
        elif kind == "HIGH":
            rel = "HH" if price > prev_same.price else "LH"
        else:
            rel = "HL" if price > prev_same.price else "LL"
        size = (abs(price - prev_opp.price) / ref) if (prev_opp is not None and ref) else None
        dur = (pidx - prev_opp.pivot_index) if prev_opp is not None else None
        sw = Swing(detector, kind, price, pidx, ptime, cidx, ctime, cidx - pidx, rel, size, dur, p.as_dict())
        if kind == "HIGH":
            self.last_high = sw
        else:
            self.last_low = sw
        return sw


class SwingEngine:
    def __init__(self, p: SwingParams = DEFAULT_SWING_PARAMS):
        self.p = p
        self._session = None
        self._reset()

    def _reset(self):
        self._idx = -1
        self._win: deque = deque(maxlen=2 * self.p.fractal_n + 1)   # (idx, ts, high, low, ref)
        self._fr = _Track(); self._zz = _Track()
        self._dir: Optional[str] = None
        self._ch = self._cl = None                                   # candidate high/low prices
        self._ch_i = self._cl_i = None                               # their (idx, ts, ref)
        self._known = 0

    def process(self, c: Candle, rec: AnatomyRecord) -> SwingRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            if self._session is None or self.p.reset_each_session:
                self._reset()
            self._session = d
        self._idx += 1
        ts = c.timestamp.isoformat()
        ref = rec.reference_range
        events: list[Swing] = []
        n = self.p.fractal_n

        # ---- fractal: candidate is the centre of the (2n+1)-bar window; confirmed by this (last) bar --------
        self._win.append((self._idx, ts, c.high, c.low, ref))
        if len(self._win) == 2 * n + 1:
            w = list(self._win); ci, cts, chh, cll, cref = w[n]
            others = w[:n] + w[n + 1:]
            if all(chh > x[2] for x in others):
                events.append(self._fr.make("FRACTAL", "HIGH", chh, ci, cts, self._idx, ts, cref, self.p))
            if all(cll < x[3] for x in others):
                events.append(self._fr.make("FRACTAL", "LOW", cll, ci, cts, self._idx, ts, cref, self.p))

        # ---- zigzag: reversal of T reference ranges (current bar's reference) from the candidate extreme ------
        thr = (self.p.zigzag_T * ref) if ref else None
        if self._ch is None:
            self._ch, self._ch_i = c.high, (self._idx, ts, ref)
            self._cl, self._cl_i = c.low, (self._idx, ts, ref)
        else:
            if self._dir in (None, "UP") and c.high > self._ch:
                self._ch, self._ch_i = c.high, (self._idx, ts, ref)
                if self._dir == "UP":
                    pass
            if self._dir in (None, "DOWN") and c.low < self._cl:
                self._cl, self._cl_i = c.low, (self._idx, ts, ref)
            if thr is not None:
                if self._dir in (None, "UP") and c.close <= self._ch - thr:
                    pi, pts, pref = self._ch_i
                    events.append(self._zz.make("ZIGZAG", "HIGH", self._ch, pi, pts, self._idx, ts, pref, self.p))
                    self._dir = "DOWN"
                    self._cl, self._cl_i = c.low, (self._idx, ts, ref)
                    for (i2, t2, h2, l2, r2) in list(self._win):          # lowest low since the pivot bar
                        if i2 > pi and l2 < self._cl:
                            self._cl, self._cl_i = l2, (i2, t2, r2)
                elif self._dir in (None, "DOWN") and c.close >= self._cl + thr:
                    pi, pts, pref = self._cl_i
                    events.append(self._zz.make("ZIGZAG", "LOW", self._cl, pi, pts, self._idx, ts, pref, self.p))
                    self._dir = "UP"
                    self._ch, self._ch_i = c.high, (self._idx, ts, ref)
                    for (i2, t2, h2, l2, r2) in list(self._win):
                        if i2 > pi and h2 > self._ch:
                            self._ch, self._ch_i = h2, (i2, t2, r2)
        self._known += len(events)
        return SwingRecord(ts, self._idx, events, self._dir, self._ch, self._cl, self._known)


def replay_swings(items: Iterable[tuple[Candle, AnatomyRecord]], p: SwingParams = DEFAULT_SWING_PARAMS) -> Iterator[tuple[int, Candle, SwingRecord]]:
    eng = SwingEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
