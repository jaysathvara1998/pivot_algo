"""Per-level event machine (PROJECT DEFINITION, Skill 09)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.levels import LevelEngine
from .params import BreakoutParams, DEFAULT_BREAKOUT_PARAMS, GROUPS


@dataclass
class BreakEvent:
    label: str                 # e.g. BREAK_UP|PREV_DAY
    event: str                 # APPROACH | TEST | TEST_RETURNED | BREAK | ACCEPTANCE | FAILED_BREAK | RECLAIM
    direction: str             # UP | DOWN (the side price pushed toward / crossed to)
    family: str
    group: str
    level: float
    confirmed_at: str
    observations: list
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class BreakoutRecord:
    timestamp: str
    session_index: int
    events: list
    watches: int               # open break watches (broken, not yet accepted or failed)

    def as_dict(self) -> dict:
        d = asdict(self); d["events"] = [e.as_dict() if isinstance(e, BreakEvent) else e for e in self.events]; return d


class _LevelState:
    __slots__ = ("side", "near", "watch_dir", "watch_count", "watch_bars", "accepted_sides", "last_break_ts")

    def __init__(self):
        self.side = None            # +1 close above, -1 below (0 ignored)
        self.near = False           # previous bar within near_ref
        self.watch_dir = None       # +1 / -1 while a break is unresolved
        self.watch_count = 0
        self.watch_bars = 0
        self.accepted_sides = set()
        self.last_break_ts = None


class BreakoutEngine:
    def __init__(self, p: BreakoutParams = DEFAULT_BREAKOUT_PARAMS):
        self.p = p
        self._levels = LevelEngine(p.levels)
        self._session = None
        self._states: dict[tuple, _LevelState] = {}
        self._idx = -1

    def process(self, c: Candle, rec: AnatomyRecord) -> BreakoutRecord:
        d = session_date(c.timestamp)
        lv_rec = self._levels.process(c, rec)
        if d != self._session:
            self._session = d; self._states = {}; self._idx = -1
        self._idx += 1
        ts = c.timestamp.isoformat()
        ref = rec.reference_range
        p = self.p
        pd = p.as_dict()
        events: list[BreakEvent] = []
        for L in self._levels.known_levels():
            if L.family not in GROUPS:
                continue
            key = (L.family, L.price)
            st = self._states.setdefault(key, _LevelState())
            group = GROUPS[L.family]
            side_now = 1 if c.close > L.price else -1 if c.close < L.price else 0
            touch = c.low <= L.price <= c.high
            dist = (c.close - L.price) / ref if ref else None
            near_now = dist is not None and abs(dist) <= p.near_ref
            created_now = L.known_from == ts
            if st.side is not None and not created_now and side_now != 0 and side_now != st.side:
                direction = "UP" if side_now > 0 else "DOWN"
                obs = [f"close {c.close} crossed {L.family} {L.price} ({'above' if side_now > 0 else 'below'})", f"previous close on the other side"]
                events.append(BreakEvent(f"BREAK_{direction}|{group}", "BREAK", direction, L.family, group, L.price, ts, obs, pd))
                if -side_now in st.accepted_sides:
                    events.append(BreakEvent(f"RECLAIM_{direction}|{group}", "RECLAIM", direction, L.family, group, L.price, ts,
                                             obs + [f"the level had been accepted on the {'lower' if side_now > 0 else 'upper'} side earlier this session"], pd))
                if st.watch_dir is not None and st.watch_dir == -side_now and st.watch_bars < p.fail_h:
                    fdir = "UP" if st.watch_dir > 0 else "DOWN"
                    events.append(BreakEvent(f"FAILED_BREAK_{fdir}|{group}", "FAILED_BREAK", fdir, L.family, group, L.price, ts,
                                             [f"break {fdir} at {st.last_break_ts[11:16]} closed back across within {st.watch_bars + 1} bars (< {p.fail_h})"], pd))
                st.watch_dir, st.watch_count, st.watch_bars, st.last_break_ts = side_now, 1, 0, ts
                if p.accept_k == 1:
                    events.append(BreakEvent(f"ACCEPTANCE_{direction}|{group}", "ACCEPTANCE", direction, L.family, group, L.price, ts, [f"1 close beyond"], pd))
                    st.accepted_sides.add(side_now); st.watch_dir = None
            else:
                if st.watch_dir is not None and not created_now:
                    st.watch_bars += 1
                    if side_now == st.watch_dir:
                        st.watch_count += 1
                        if st.watch_count >= p.accept_k:
                            direction = "UP" if st.watch_dir > 0 else "DOWN"
                            events.append(BreakEvent(f"ACCEPTANCE_{direction}|{group}", "ACCEPTANCE", direction, L.family, group, L.price, ts,
                                                     [f"{p.accept_k} consecutive closes {'above' if st.watch_dir > 0 else 'below'} {L.family} {L.price} since the break at {st.last_break_ts[11:16]}"], pd))
                            st.accepted_sides.add(st.watch_dir); st.watch_dir = None
                    elif side_now == 0:
                        pass
                if touch and st.side is not None and side_now == st.side and not created_now:
                    direction = "UP" if side_now < 0 else "DOWN"             # pushed up into the level from below -> TEST_UP
                    obs = [f"bar range {c.low}-{c.high} spans {L.family} {L.price}", f"close {c.close} stays {'below' if side_now < 0 else 'above'}"]
                    events.append(BreakEvent(f"TEST_{direction}|{group}", "TEST", direction, L.family, group, L.price, ts, obs, pd))
                    if dist is not None and abs(dist) >= p.near_ref:
                        events.append(BreakEvent(f"TEST_RETURNED_{direction}|{group}", "TEST_RETURNED", direction, L.family, group, L.price, ts,
                                                 obs + [f"close {abs(dist):.2f} reference ranges away from the level"], pd))
                elif near_now and not st.near and not touch and st.side is not None and not created_now and side_now == st.side:
                    direction = "UP" if side_now < 0 else "DOWN"
                    events.append(BreakEvent(f"APPROACH_{direction}|{group}", "APPROACH", direction, L.family, group, L.price, ts,
                                             [f"close {c.close} within {p.near_ref} reference ranges of {L.family} {L.price} ({dist:+.2f}), previous bar farther"], pd))
            if side_now != 0:
                st.side = side_now
            st.near = near_now
        return BreakoutRecord(ts, self._idx, events, sum(1 for s in self._states.values() if s.watch_dir is not None))


def replay_breakouts(items: Iterable[tuple[Candle, AnatomyRecord]], p: BreakoutParams = DEFAULT_BREAKOUT_PARAMS) -> Iterator[tuple[int, Candle, BreakoutRecord]]:
    eng = BreakoutEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
