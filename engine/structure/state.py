"""Trend state and structure events from confirmed swings (PROJECT DEFINITION, Skill 06)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.swings import SwingEngine, SwingParams, Swing
from .params import StructureParams, DEFAULT_STRUCTURE_PARAMS

STATES = ("UNDEFINED", "UP", "DOWN", "RANGE_EXPANDING", "RANGE_CONTRACTING")


@dataclass
class StructEvent:
    label: str                 # TREND_UP | TREND_DOWN | TREND_RANGE_EXPANDING | TREND_RANGE_CONTRACTING | BOS_UP | BOS_DOWN | CHOCH_UP | CHOCH_DOWN
    confirmed_at: str
    observations: list
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class StructureRecord:
    timestamp: str
    session_index: int
    trend_state: str
    last_high: Optional[float]
    last_low: Optional[float]
    last_high_relation: Optional[str]
    last_low_relation: Optional[str]
    bars_in_state: int
    events: list

    def as_dict(self) -> dict:
        d = asdict(self); d["events"] = [e.as_dict() if isinstance(e, StructEvent) else e for e in self.events]; return d


def trend_state(last_high: Optional[Swing], last_low: Optional[Swing]) -> str:
    """UP: last high HH and last low HL. DOWN: LH and LL. RANGE_EXPANDING: HH and LL. RANGE_CONTRACTING: LH and HL.
    UNDEFINED if either swing is missing, FIRST or EQUAL."""
    if last_high is None or last_low is None:
        return "UNDEFINED"
    h, l = last_high.relation, last_low.relation
    if h in ("FIRST", "EQUAL") or l in ("FIRST", "EQUAL"):
        return "UNDEFINED"
    if h == "HH" and l == "HL":
        return "UP"
    if h == "LH" and l == "LL":
        return "DOWN"
    if h == "HH" and l == "LL":
        return "RANGE_EXPANDING"
    return "RANGE_CONTRACTING"


class StructureEngine:
    def __init__(self, p: StructureParams = DEFAULT_STRUCTURE_PARAMS):
        self.p = p
        self._swings = SwingEngine(SwingParams(**{**p.swing.as_dict(), "reset_each_session": p.reset_each_session}))
        self._session = None
        self._reset()

    def _reset(self):
        self._idx = -1
        self._last_high: Optional[Swing] = None
        self._last_low: Optional[Swing] = None
        self._high_broken = False
        self._low_broken = False
        self._state = "UNDEFINED"
        self._bars_in_state = 0
        self.last_swing_record = None
        self.last_pivot = None                               # most recent confirmed swing of either kind

    def process(self, c: Candle, rec: AnatomyRecord) -> StructureRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            if self._session is None or self.p.reset_each_session:
                self._reset()
            self._session = d
        self._idx += 1
        ts = c.timestamp.isoformat()
        pd = self.p.as_dict()
        events: list[StructEvent] = []
        sw_rec = self._swings.process(c, rec)
        self.last_swing_record = sw_rec                     # exposed for Level 7 (current leg direction, candidates)
        det = self.p.detector
        # --- new confirmed swings update the structure ------------------------------------------------
        for sw in sw_rec.events:
            if sw.detector != det:
                continue
            if sw.kind == "HIGH":
                self._last_high = sw; self._high_broken = False
            else:
                self._last_low = sw; self._low_broken = False
            if self.last_pivot is None or sw.pivot_index >= self.last_pivot.pivot_index:
                self.last_pivot = sw
        new_state = trend_state(self._last_high, self._last_low)
        if new_state != self._state:
            obs = [f"last swing high {self._last_high.price} ({self._last_high.relation}) confirmed {self._last_high.confirmed_at[11:16]}" if self._last_high else "no swing high",
                   f"last swing low {self._last_low.price} ({self._last_low.relation}) confirmed {self._last_low.confirmed_at[11:16]}" if self._last_low else "no swing low",
                   f"previous state {self._state} for {self._bars_in_state} bars"]
            events.append(StructEvent(f"TREND_{new_state}", ts, obs, pd))
            self._state = new_state; self._bars_in_state = 0
        else:
            self._bars_in_state += 1
        # --- BOS / CHOCH against the known swings (confirmed at or before this bar) --------------------------
        up_ref = c.close if self.p.use_close else c.high
        dn_ref = c.close if self.p.use_close else c.low
        if self._last_high is not None and not self._high_broken and self._last_high.confirmed_index <= self._idx and up_ref > self._last_high.price:
            label = "BOS_UP" if self._state == "UP" else "CHOCH_UP" if self._state == "DOWN" else "BREAK_UP"
            events.append(StructEvent(label, ts, [f"{'close' if self.p.use_close else 'high'} {up_ref} above last swing high {self._last_high.price} ({self._last_high.relation})", f"trend state {self._state}"], pd))
            self._high_broken = True
        if self._last_low is not None and not self._low_broken and self._last_low.confirmed_index <= self._idx and dn_ref < self._last_low.price:
            label = "BOS_DOWN" if self._state == "DOWN" else "CHOCH_DOWN" if self._state == "UP" else "BREAK_DOWN"
            events.append(StructEvent(label, ts, [f"{'close' if self.p.use_close else 'low'} {dn_ref} below last swing low {self._last_low.price} ({self._last_low.relation})", f"trend state {self._state}"], pd))
            self._low_broken = True
        return StructureRecord(ts, self._idx, self._state, self._last_high.price if self._last_high else None, self._last_low.price if self._last_low else None,
                               self._last_high.relation if self._last_high else None, self._last_low.relation if self._last_low else None, self._bars_in_state, events)


def replay_structure(items: Iterable[tuple[Candle, AnatomyRecord]], p: StructureParams = DEFAULT_STRUCTURE_PARAMS) -> Iterator[tuple[int, Candle, StructureRecord]]:
    eng = StructureEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
