"""Multi-timeframe context engine (PROJECT DEFINITION, Skill 13)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import timedelta
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.ohlc.aggregate import anchor_start
from engine.anatomy import AnatomyEngine, AnatomyRecord
from engine.market_state import MarketStateEngine
from .params import MTFParams, DEFAULT_MTF_PARAMS


def alignment(ltf_state: str, htf_state: str) -> str:
    if htf_state == "TRENDING_UP":
        return "ALIGNED_UP" if ltf_state == "TRENDING_UP" else "CONFLICT_LTF_DOWN" if ltf_state == "TRENDING_DOWN" else "HTF_UP_LTF_OTHER"
    if htf_state == "TRENDING_DOWN":
        return "ALIGNED_DOWN" if ltf_state == "TRENDING_DOWN" else "CONFLICT_LTF_UP" if ltf_state == "TRENDING_UP" else "HTF_DOWN_LTF_OTHER"
    if htf_state == "RANGING":
        return "HTF_RANGING"
    if htf_state == "TRANSITIONING":
        return "HTF_TRANSITIONING"
    return "HTF_UNCLEAR"


@dataclass
class HTFContext:
    minutes: int
    state: str                     # last COMPLETE HTF market state (UNCLEAR before any complete bar)
    trend_state: str
    vol_regime: str
    as_of: Optional[str]           # completion timestamp of the HTF bar that produced the state
    complete_bars: int             # HTF bars completed so far today (or since start when cross-session)
    forming_elapsed: int           # 1-minute bars inside the forming HTF bar
    forming_open: Optional[float]
    forming_high: Optional[float]
    forming_low: Optional[float]
    forming_close: Optional[float]
    alignment: str

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class MTFRecord:
    timestamp: str
    session_index: int
    ltf_state: str
    htf: dict                      # minutes -> HTFContext
    labels: list

    def as_dict(self) -> dict:
        d = asdict(self); d["htf"] = {str(k): (v.as_dict() if isinstance(v, HTFContext) else v) for k, v in self.htf.items()}; return d


class _HTF:
    def __init__(self, minutes: int, p: MTFParams):
        self.k = minutes
        self.delta = timedelta(minutes=minutes)
        self.anat = AnatomyEngine(p.anatomy)
        self.ms = MarketStateEngine(p.htf_state_params())
        self.cur = None            # [start, o, h, l, c, n]
        self.cur_date = None
        self.state = "UNCLEAR"; self.trend_state = "UNDEFINED"; self.vol = "UNDEFINED"; self.as_of = None
        self.complete = 0

    def push(self, c: Candle) -> Optional[Candle]:
        """Feed a 1m candle; return the HTF candle if this 1m bar completes it."""
        start = anchor_start(c.timestamp, self.k)
        d = session_date(c.timestamp)
        emitted = None
        if self.cur is not None and (c.timestamp >= self.cur[0] + self.delta or d != self.cur_date):
            emitted = Candle(self.cur[0], self.cur[1], self.cur[2], self.cur[3], self.cur[4], self.delta)   # closed by a session change
            self.cur = None
        if self.cur is None:
            self.cur = [start, c.open, c.high, c.low, c.close, 1]; self.cur_date = d
        else:
            self.cur[2] = max(self.cur[2], c.high); self.cur[3] = min(self.cur[3], c.low); self.cur[4] = c.close; self.cur[5] += 1
        if c.end == self.cur[0] + self.delta:                       # this 1m bar ends exactly at the bucket end: complete now
            done = Candle(self.cur[0], self.cur[1], self.cur[2], self.cur[3], self.cur[4], self.delta)
            self.cur = None
            return done if emitted is None else emitted            # (a session-change flush and a completion cannot coincide)
        return emitted

    def update(self, bar: Candle):
        rec = self.anat.process(bar)
        ms = self.ms.process(bar, rec)
        self.state, self.trend_state, self.vol, self.as_of = ms.state, ms.trend_state, ms.vol_regime, bar.end.isoformat()
        self.complete += 1

    def context(self, ltf_state: str) -> HTFContext:
        f = self.cur
        return HTFContext(self.k, self.state, self.trend_state, self.vol, self.as_of, self.complete,
                          f[5] if f else 0, f[1] if f else None, f[2] if f else None, f[3] if f else None, f[4] if f else None,
                          alignment(ltf_state, self.state))


class MTFEngine:
    def __init__(self, p: MTFParams = DEFAULT_MTF_PARAMS):
        self.p = p
        self._ltf = MarketStateEngine(p.ltf_state)
        self._htf = {k: _HTF(k, p) for k in p.higher_minutes}
        self._session = None; self._idx = -1

    def process(self, c: Candle, rec: AnatomyRecord) -> MTFRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            self._session = d; self._idx = -1
            if not self.p.htf_cross_session:
                for h in self._htf.values():
                    h.complete = 0
        self._idx += 1
        # HTF bars complete BEFORE the 1m record is produced, so the state known at this bar's close may include a bar that closes now
        for h in self._htf.values():
            done = h.push(c)
            if done is not None:
                h.update(done)
        ltf = self._ltf.process(c, rec)
        ctx = {k: h.context(ltf.state) for k, h in self._htf.items()}
        labels = [f"{k}m:{v.alignment}" for k, v in ctx.items()]
        return MTFRecord(c.timestamp.isoformat(), self._idx, ltf.state, ctx, labels)


def replay_mtf(items: Iterable[tuple[Candle, AnatomyRecord]], p: MTFParams = DEFAULT_MTF_PARAMS) -> Iterator[tuple[int, Candle, MTFRecord]]:
    eng = MTFEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
