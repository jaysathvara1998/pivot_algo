"""Per-session context record (PROJECT DEFINITION, Skill 15)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.session import SessionEngine, DEFAULT_SESSION_PARAMS
from .params import ContextParams, DEFAULT_CTX_PARAMS
from .events_data import events_on


def gap_class(gap_pct: float, p: ContextParams = DEFAULT_CTX_PARAMS) -> str:
    a = abs(gap_pct)
    return "FLAT" if a < p.gap_flat_pct else "SMALL" if a < p.gap_small_pct else "MEDIUM" if a < p.gap_medium_pct else "LARGE"


@dataclass
class ContextRecord:
    timestamp: str
    session_index: int
    # fixed at the first bar's close
    date: str
    regime: str
    expiry_flags: dict
    events: list
    prev_close: Optional[float]           # previous session's last continuous print
    prev_high: Optional[float]
    prev_low: Optional[float]
    prev_range_pct: Optional[float]
    prev_close_location: Optional[float]  # (prev_close - prev_low)/(prev_high - prev_low)
    gap_pct: Optional[float]              # (open - prev_close)/prev_close × 100
    gap_ref: Optional[float]              # gap / first bar's reference range
    gap_class: Optional[str]
    gap_sign: Optional[str]               # UP | DOWN | NONE
    open_vs_prev_range: Optional[str]     # ABOVE | INSIDE | BELOW
    # running
    gap_filled: Optional[bool]            # previous close revisited (bar range spans it) at or before this bar
    gap_fill_bar: Optional[int]
    first_hour_return_pct: Optional[float]  # (close at minute 60 - open)/open × 100, known from minute 60
    session_range_pct: float
    session_range_over_prev: Optional[float]
    labels: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class ContextEngine:
    def __init__(self, p: ContextParams = DEFAULT_CTX_PARAMS):
        self.p = p
        self._sess = SessionEngine(DEFAULT_SESSION_PARAMS)
        self._session = None
        self._prev = None           # (high, low, last_continuous_close) of the previous session
        self._cur = None

    def process(self, c: Candle, rec: AnatomyRecord) -> ContextRecord:
        d = session_date(c.timestamp)
        s = self._sess.process(c, rec)
        if d != self._session:
            if self._cur is not None:
                self._prev = (self._cur["high"], self._cur["low"], self._cur["last_close"])
            self._session = d
            open_ = c.open
            prev = self._prev
            gap_pct = (open_ - prev[2]) / prev[2] * 100 if prev else None
            ref = rec.reference_range
            self._cur = {"open": open_, "high": c.high, "low": c.low, "last_close": c.close, "filled": None, "fill_bar": None, "fh": None,
                         "fixed": {"prev_close": prev[2] if prev else None, "prev_high": prev[0] if prev else None, "prev_low": prev[1] if prev else None,
                                   "prev_range_pct": (prev[0] - prev[1]) / prev[2] * 100 if prev else None,
                                   "prev_close_location": (prev[2] - prev[1]) / (prev[0] - prev[1]) if prev and prev[0] > prev[1] else None,
                                   "gap_pct": gap_pct, "gap_ref": (open_ - prev[2]) / ref if (prev and ref) else None,
                                   "gap_class": gap_class(gap_pct, self.p) if gap_pct is not None else None,
                                   "gap_sign": ("UP" if gap_pct > 0 else "DOWN" if gap_pct < 0 else "NONE") if gap_pct is not None else None,
                                   "open_vs_prev_range": ("ABOVE" if open_ > prev[0] else "BELOW" if open_ < prev[1] else "INSIDE") if prev else None,
                                   "events": events_on(d)}}
            if prev:
                self._cur["filled"] = c.low <= prev[2] <= c.high
                self._cur["fill_bar"] = 0 if self._cur["filled"] else None
        else:
            cur = self._cur
            cur["high"] = max(cur["high"], c.high); cur["low"] = min(cur["low"], c.low)
            if cur["filled"] is False and cur["fixed"]["prev_close"] is not None and c.low <= cur["fixed"]["prev_close"] <= c.high:
                cur["filled"] = True; cur["fill_bar"] = s.session_index
        cur = self._cur
        if not s.static_window:
            cur["last_close"] = c.close
        if s.minutes_since_open == self.p.first_hour_minutes - 1 and cur["fh"] is None:
            cur["fh"] = (c.close - cur["open"]) / cur["open"] * 100
        f = cur["fixed"]
        rng_pct = (cur["high"] - cur["low"]) / cur["open"] * 100
        labels = []
        if f["gap_class"] is not None:
            labels += [f"GAP_{f['gap_class']}", f"GAP_{f['gap_class']}_{f['gap_sign']}", f"OPEN_{f['open_vs_prev_range']}"]
        labels += [f"EVENT_{e['kind']}" for e in f["events"]]
        return ContextRecord(c.timestamp.isoformat(), s.session_index, d.isoformat(), s.regime, dict(s.flags), f["events"],
                             f["prev_close"], f["prev_high"], f["prev_low"], f["prev_range_pct"], f["prev_close_location"], f["gap_pct"], f["gap_ref"],
                             f["gap_class"], f["gap_sign"], f["open_vs_prev_range"], cur["filled"], cur["fill_bar"], cur["fh"], rng_pct,
                             rng_pct / f["prev_range_pct"] if f["prev_range_pct"] else None, labels)


def replay_context(items: Iterable[tuple[Candle, AnatomyRecord]], p: ContextParams = DEFAULT_CTX_PARAMS) -> Iterator[tuple[int, Candle, ContextRecord]]:
    eng = ContextEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
