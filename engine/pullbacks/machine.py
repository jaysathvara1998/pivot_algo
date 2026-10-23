"""Pullback machine within trending states (PROJECT DEFINITION, Skill 11)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.market_state import MarketStateEngine
from .params import PullbackParams, DEFAULT_PB_PARAMS, DEPTH_CLASSES


def depth_class(depth: float, p: PullbackParams = DEFAULT_PB_PARAMS) -> str:
    if depth < p.shallow_max:
        return "SHALLOW"
    if depth < p.normal_max:
        return "NORMAL"
    if depth <= p.deep_max:
        return "DEEP"
    return "BROKEN"


@dataclass
class PullbackEvent:
    label: str                 # e.g. PULLBACK_START|UP, DEPTH_NORMAL|UP, CONTINUATION|UP, FAILED_CONTINUATION|DOWN, EXPIRED|UP
    event: str
    trend: str                 # UP | DOWN
    confirmed_at: str
    impulse_origin: float      # last confirmed swing price (HL in UP)
    impulse_extreme: float
    depth: Optional[float]     # share of the impulse retraced at this bar
    depth_ref: Optional[float] # retracement in reference ranges
    bars_since_start: int
    observations: list
    depth_class: Optional[str] = None     # max-depth class at the time of the event (BROKEN for a failed continuation)
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class PullbackRecord:
    timestamp: str
    session_index: int
    market_state: str
    phase: str                 # NONE | IMPULSE | PULLBACK
    trend: Optional[str]
    impulse_origin: Optional[float]
    impulse_extreme: Optional[float]
    depth: Optional[float]
    max_depth: Optional[float]
    depth_class: Optional[str]
    bars_in_pullback: int
    structure_preserved: Optional[bool]   # pullback has not closed beyond the last confirmed swing
    events: list

    def as_dict(self) -> dict:
        d = asdict(self); d["events"] = [e.as_dict() if isinstance(e, PullbackEvent) else e for e in self.events]; return d


class PullbackEngine:
    def __init__(self, p: PullbackParams = DEFAULT_PB_PARAMS):
        self.p = p
        self._ms = MarketStateEngine(p.market_state)
        self._session = None
        self._reset()

    def _reset(self):
        self._idx = -1
        self._phase = "NONE"; self._trend = None
        self._origin = self._extreme = None
        self._max_depth = None; self._class = None; self._pb_bars = 0; self._pb_start_idx = None

    def _clear(self):
        self._phase = "NONE"; self._trend = None; self._origin = self._extreme = None
        self._max_depth = None; self._class = None; self._pb_bars = 0; self._pb_start_idx = None

    def process(self, c: Candle, rec: AnatomyRecord) -> PullbackRecord:
        d = session_date(c.timestamp)
        ms = self._ms.process(c, rec)
        pa = self._ms.last_pa_record
        self.last_ms_record = ms; self.last_pa_record = pa      # exposed for Level 12
        if d != self._session:
            if self._session is None or self.p.market_state.price_action.structure.reset_each_session:
                self._reset()
            self._session = d
        self._idx += 1
        p = self.p
        ts = c.timestamp.isoformat()
        ref = rec.reference_range
        pd = p.as_dict()
        events: list[PullbackEvent] = []
        up = self._trend == "UP"

        def ev(label, event, depth, depth_ref, obs):
            cls = "BROKEN" if event == "FAILED_CONTINUATION" else self._class
            events.append(PullbackEvent(label, event, self._trend, ts, self._origin, self._extreme, depth, depth_ref, self._pb_bars, obs, cls, pd))

        # --- start / maintain an impulse in a trending state --------------------------------------------
        if self._phase == "NONE":
            if ms.state == "TRENDING_UP" and pa.last_low is not None:
                self._phase, self._trend, self._origin, self._extreme = "IMPULSE", "UP", pa.last_low, c.high
            elif ms.state == "TRENDING_DOWN" and pa.last_high is not None:
                self._phase, self._trend, self._origin, self._extreme = "IMPULSE", "DOWN", pa.last_high, c.low
        elif self._phase == "IMPULSE":
            up = self._trend == "UP"
            # a confirmed new swing of the origin kind moves the origin (HL / LH) forward while the trend holds
            if up and pa.last_low is not None and pa.last_low > self._origin:
                self._origin = pa.last_low
            if not up and pa.last_high is not None and pa.last_high < self._origin:
                self._origin = pa.last_high
            self._extreme = max(self._extreme, c.high) if up else min(self._extreme, c.low)
            if ms.state not in ("TRENDING_UP", "TRENDING_DOWN", "TRANSITIONING"):
                self._clear()
            else:
                leg = (self._extreme - self._origin) if up else (self._origin - self._extreme)
                retr_pts = (self._extreme - c.close) if up else (c.close - self._extreme)
                if ref and leg > 0 and retr_pts >= p.start_min_ref * ref:
                    self._phase = "PULLBACK"; self._pb_bars = 0; self._pb_start_idx = self._idx
                    depth = retr_pts / leg; self._max_depth = depth; self._class = depth_class(depth, p)
                    ev(f"PULLBACK_START|{self._trend}", "PULLBACK_START", depth, retr_pts / ref,
                       [f"impulse {self._origin} -> {self._extreme} ({leg / ref:.1f} ref ranges)", f"close {c.close} retraced {depth:.2f} of it ({retr_pts / ref:.1f} ref ranges)", f"depth class {self._class}"])
                    ev(f"DEPTH_{self._class}|{self._trend}", "DEPTH", depth, retr_pts / ref, [f"first bar at depth class {self._class}"])
                    self._resolve(c, ref, events, ev, depth)
        elif self._phase == "PULLBACK":
            up = self._trend == "UP"
            self._pb_bars += 1
            leg = (self._extreme - self._origin) if up else (self._origin - self._extreme)
            retr_pts = (self._extreme - c.close) if up else (c.close - self._extreme)
            depth = retr_pts / leg if leg > 0 else None
            if depth is not None and depth > self._max_depth:
                self._max_depth = depth
                cls = depth_class(depth, p)
                if cls != self._class:
                    self._class = cls
                    ev(f"DEPTH_{cls}|{self._trend}", "DEPTH", depth, retr_pts / ref if ref else None, [f"pullback deepened to class {cls} ({depth:.2f} of the impulse)"])
            self._resolve(c, ref, events, ev, depth)

        depth_now = None
        if self._phase == "PULLBACK" and self._extreme is not None:
            leg = (self._extreme - self._origin) if up else (self._origin - self._extreme)
            depth_now = ((self._extreme - c.close) if up else (c.close - self._extreme)) / leg if leg > 0 else None
        preserved = None
        if self._phase == "PULLBACK":
            preserved = c.close > self._origin if up else c.close < self._origin
        return PullbackRecord(ts, self._idx, ms.state, self._phase, self._trend, self._origin, self._extreme, depth_now, self._max_depth,
                              self._class, self._pb_bars if self._phase == "PULLBACK" else 0, preserved, events)

    def _resolve(self, c, ref, events, ev, depth):
        up = self._trend == "UP"
        p = self.p
        if (c.close > self._extreme) if up else (c.close < self._extreme):
            ev(f"CONTINUATION|{self._trend}", "CONTINUATION", depth, None, [f"close {c.close} beyond the impulse extreme {self._extreme} after {self._pb_bars} pullback bars; max depth {self._max_depth:.2f} ({self._class})"])
            # the continuation starts a new impulse from the same origin
            self._phase = "IMPULSE"; self._extreme = c.high if up else c.low; self._max_depth = None; self._class = None; self._pb_bars = 0
        elif (c.close < self._origin) if up else (c.close > self._origin):
            ev(f"FAILED_CONTINUATION|{self._trend}", "FAILED_CONTINUATION", depth, None, [f"close {c.close} beyond the last confirmed swing {self._origin}: structure violated after {self._pb_bars} pullback bars"])
            self._clear()
        elif self._pb_bars >= p.expire_bars:
            ev(f"EXPIRED|{self._trend}", "EXPIRED", depth, None, [f"{self._pb_bars} pullback bars without resolution; max depth {self._max_depth:.2f} ({self._class})"])
            self._clear()


def replay_pullbacks(items: Iterable[tuple[Candle, AnatomyRecord]], p: PullbackParams = DEFAULT_PB_PARAMS) -> Iterator[tuple[int, Candle, PullbackRecord]]:
    eng = PullbackEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
