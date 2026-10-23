"""Reversal sequence machine (PROJECT DEFINITION, Skill 12)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.pullbacks import PullbackEngine
from .params import ReversalParams, DEFAULT_REV_PARAMS


@dataclass
class ReversalEvent:
    label: str                 # WEAKENING|UP, VIOLATION|UP, NEW_SEQUENCE|UP_TO_DOWN, REVERSAL_CONFIRMED|UP_TO_DOWN, REVERSAL_FAILED|UP, REVERSAL_EXPIRED|UP
    event: str
    old_trend: str             # the trend being reversed (UP | DOWN)
    confirmed_at: str
    bars_since_violation: Optional[int]
    observations: list
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class ReversalRecord:
    timestamp: str
    session_index: int
    market_state: str
    stage: str                 # NONE | ARMED | VIOLATED | NEW_SEQUENCE
    old_trend: Optional[str]
    weakening: bool
    bars_since_violation: Optional[int]
    events: list

    def as_dict(self) -> dict:
        d = asdict(self); d["events"] = [e.as_dict() if isinstance(e, ReversalEvent) else e for e in self.events]; return d


class ReversalEngine:
    def __init__(self, p: ReversalParams = DEFAULT_REV_PARAMS):
        self.p = p
        self._pb = PullbackEngine(p.pullbacks)
        self._session = None
        self._reset()

    def _reset(self):
        self._idx = -1
        self._stage = "NONE"; self._old = None; self._weak = False; self._viol_bars = None

    def process(self, c: Candle, rec: AnatomyRecord) -> ReversalRecord:
        d = session_date(c.timestamp)
        pbr = self._pb.process(c, rec)
        ms = self._pb.last_ms_record; pa = self._pb.last_pa_record
        if d != self._session:
            if self._session is None or self.p.pullbacks.market_state.price_action.structure.reset_each_session:
                self._reset()
            self._session = d
        self._idx += 1
        p = self.p
        ts = c.timestamp.isoformat()
        pd = p.as_dict()
        events: list[ReversalEvent] = []
        state = ms.state
        pb_labels = [e.label for e in pbr.events]
        e4 = pa.events4; e6 = pa.events6

        def ev(label, event, obs, bars=None):
            events.append(ReversalEvent(label, event, self._old, ts, bars, obs, pd))

        # --- arm on a trending state ------------------------------------------------------------------
        if self._stage in ("NONE", "ARMED"):
            if state == "TRENDING_UP":
                if self._stage == "NONE" or self._old != "UP":
                    self._weak = False
                self._stage, self._old = "ARMED", "UP"
            elif state == "TRENDING_DOWN":
                if self._stage == "NONE" or self._old != "DOWN":
                    self._weak = False
                self._stage, self._old = "ARMED", "DOWN"
        if self._stage == "ARMED":
            o = self._old
            opp = "DOWN" if o == "UP" else "UP"
            # weakening: deep or expired pullback, or fading momentum, while the trend stands
            weak_now = [x for x in (f"DEPTH_DEEP|{o}", f"EXPIRED|{o}") if x in pb_labels] + [x for x in e4 if x == f"MOMENTUM_FADING_{o}"]
            if f"CONTINUATION|{o}" in pb_labels:
                self._weak = False
            if weak_now and not self._weak and state in ("TRENDING_UP", "TRENDING_DOWN", "TRANSITIONING"):
                self._weak = True
                ev(f"WEAKENING|{o}", "WEAKENING", [f"{o} trend weakening: {', '.join(weak_now)}", f"market state {state}"])
            # structural violation: change of character against the trend, or a failed continuation
            viol = [x for x in e6 if x == f"CHOCH_{opp}"] + [x for x in pb_labels if x == f"FAILED_CONTINUATION|{o}"]
            if viol:
                self._stage = "VIOLATED"; self._viol_bars = 0
                ev(f"VIOLATION|{o}", "VIOLATION", [f"structure of the {o} trend violated: {', '.join(viol)}", f"weakening seen before: {self._weak}"], 0)
        elif self._stage in ("VIOLATED", "NEW_SEQUENCE"):
            o = self._old; opp = "DOWN" if o == "UP" else "UP"
            self._viol_bars += 1
            if self._stage == "VIOLATED" and pa.trend_state == opp:
                self._stage = "NEW_SEQUENCE"
                ev(f"NEW_SEQUENCE|{o}_TO_{opp}", "NEW_SEQUENCE", [f"Level 6 state {opp} after the violation ({self._viol_bars} bars)"], self._viol_bars)
            if state == f"TRENDING_{opp}":
                ev(f"REVERSAL_CONFIRMED|{o}_TO_{opp}", "REVERSAL_CONFIRMED", [f"market state TRENDING_{opp} {self._viol_bars} bars after the violation"], self._viol_bars)
                self._stage, self._old, self._weak, self._viol_bars = "ARMED", opp, False, None
            elif state == f"TRENDING_{o}":
                ev(f"REVERSAL_FAILED|{o}", "REVERSAL_FAILED", [f"market state back to TRENDING_{o} {self._viol_bars} bars after the violation"], self._viol_bars)
                self._stage, self._weak, self._viol_bars = "ARMED", False, None
            elif self._viol_bars >= p.confirm_bars:
                ev(f"REVERSAL_EXPIRED|{o}", "REVERSAL_EXPIRED", [f"{self._viol_bars} bars after the violation without a trending state either way (state {state})"], self._viol_bars)
                self._stage, self._old, self._weak, self._viol_bars = "NONE", None, False, None
        return ReversalRecord(ts, self._idx, state, self._stage, self._old, self._weak, self._viol_bars, events)


def replay_reversals(items: Iterable[tuple[Candle, AnatomyRecord]], p: ReversalParams = DEFAULT_REV_PARAMS) -> Iterator[tuple[int, Candle, ReversalRecord]]:
    eng = ReversalEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
