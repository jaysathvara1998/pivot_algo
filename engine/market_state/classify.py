"""Market-state classifier over Level 7 records (PROJECT DEFINITION, Skill 10)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.price_action import PriceActionEngine
from .params import MarketStateParams, DEFAULT_MS_PARAMS, STATES


@dataclass
class MarketStateRecord:
    timestamp: str
    session_index: int
    state: str
    reasons: list                      # ordered observations that produced the state (P9)
    bars_in_state: int
    vol_regime: str                    # LOW | NORMAL | HIGH | UNDEFINED
    trend_state: str                   # Level 6
    struct_zone: str                   # Level 7
    changed: bool                      # state differs from the previous bar's state
    events: list = field(default_factory=list)   # STATE_<new> at changes

    def as_dict(self) -> dict:
        return asdict(self)


def _median(xs):
    s = sorted(xs); n = len(s)
    return s[n // 2] if n % 2 else 0.5 * (s[n // 2 - 1] + s[n // 2])


class MarketStateEngine:
    def __init__(self, p: MarketStateParams = DEFAULT_MS_PARAMS):
        self.p = p
        self._pa = PriceActionEngine(p.price_action)
        self._session = None
        self._reset()

    def _reset(self):
        self._idx = -1
        self._state = None
        self._bars_in_state = 0
        self._trans_left = 0
        self._rel = []
        self._t6 = (None, 0)                 # (Level 6 state, bars it has lasted)

    def process(self, c: Candle, rec: AnatomyRecord) -> MarketStateRecord:
        d = session_date(c.timestamp)
        pa = self._pa.process(c, rec)
        self.last_pa_record = pa                      # exposed for Level 11
        if d != self._session:
            if self._session is None or self.p.price_action.structure.reset_each_session:
                self._reset()
            self._session = d
        self._idx += 1
        p = self.p
        ts = c.timestamp.isoformat()
        # volatility regime from recent deseasonalised ranges (excluding first bar / static window)
        if rec.relative_range is not None and not rec.first_bar and not rec.static_window:
            self._rel.append(rec.relative_range)
        window = self._rel[-p.vol_window:]
        if len(window) >= p.vol_window:
            m = _median(window)
            vol = "LOW" if m < p.vol_low else "HIGH" if m > p.vol_high else "NORMAL"
        else:
            vol = "UNDEFINED"
        # transition clock: any trend-state change or change of character starts / restarts it
        if any(e.startswith("TREND_") or e.startswith("CHOCH_") for e in pa.events6):
            self._trans_left = p.transition_bars
        reasons = []
        ts6 = pa.trend_state
        prev6, age6 = self._t6                 # age of the Level 6 state, counted here
        age6 = age6 + 1 if ts6 == prev6 else 0
        self._t6 = (ts6, age6)
        if ts6 == "UNDEFINED":
            state = "UNCLEAR"; reasons.append("Level 6 trend state UNDEFINED (fewer than two confirmed swings of a kind)")
        elif self._trans_left > 0:
            state = "TRANSITIONING"; reasons.append(f"within {p.transition_bars} bars of a trend-state change or change of character ({self._trans_left} left)")
            self._trans_left -= 1
        elif age6 < p.min_state_bars:
            state = "TRANSITIONING"; reasons.append(f"Level 6 state {ts6} is only {age6} bars old (< {p.min_state_bars})")
        elif ts6 == "UP":
            if pa.struct_zone == "BELOW":
                state = "UNCLEAR"; reasons.append("Level 6 UP but close below the last swing low (conflict)")
            else:
                state = "TRENDING_UP"; reasons.append(f"Level 6 UP for {age6} bars; close in zone {pa.struct_zone}")
        elif ts6 == "DOWN":
            if pa.struct_zone == "ABOVE":
                state = "UNCLEAR"; reasons.append("Level 6 DOWN but close above the last swing high (conflict)")
            else:
                state = "TRENDING_DOWN"; reasons.append(f"Level 6 DOWN for {age6} bars; close in zone {pa.struct_zone}")
        else:
            state = "RANGING"; reasons.append(f"Level 6 {ts6} for {age6} bars")
        reasons.append(f"volatility regime {vol}")
        changed = state != self._state
        events = [f"STATE_{state}"] if changed else []
        self._bars_in_state = 0 if changed else self._bars_in_state + 1
        self._state = state
        return MarketStateRecord(ts, self._idx, state, reasons, self._bars_in_state, vol, ts6, pa.struct_zone, changed, events)


def replay_market_state(items: Iterable[tuple[Candle, AnatomyRecord]], p: MarketStateParams = DEFAULT_MS_PARAMS) -> Iterator[tuple[int, Candle, MarketStateRecord]]:
    eng = MarketStateEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
