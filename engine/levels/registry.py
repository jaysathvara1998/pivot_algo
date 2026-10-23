"""Level registry with availability stamps (PROJECT DEFINITION, Skill 08)."""
from __future__ import annotations

import math
from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.swings import SwingEngine, SwingParams
from .params import LevelParams, DEFAULT_LEVEL_PARAMS

FAMILIES = ("PDH", "PDL", "PDC", "OPEN", "ORH", "ORL", "SWING_HIGH", "SWING_LOW", "PIVOT", "ROUND")


@dataclass(frozen=True)
class Level:
    family: str
    price: float
    known_from: str            # ISO timestamp of the bar at whose close the level became known
    provenance: str            # how it was built


@dataclass
class LevelRecord:
    timestamp: str
    session_index: int
    levels: dict               # family -> list of prices known at this bar
    nearest: dict              # family -> {"price", "dist_ref" (signed: close - level, in reference ranges), "side"}
    touches: list              # families whose level lies within [low, high] of this bar
    labels: list               # AT_<family>|<side>, CROSS_<family>_UP/DOWN
    events: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class LevelEngine:
    def __init__(self, p: LevelParams = DEFAULT_LEVEL_PARAMS):
        self.p = p
        self._swings = SwingEngine(SwingParams(**{**p.swing.as_dict(), "reset_each_session": True}))
        self._session = None
        self._prev_summary: Optional[dict] = None       # previous completed session: high, low, last continuous close
        self._cur = None
        self._levels: dict[str, list[Level]] = {}
        self._prev_close_side: dict[tuple, int] = {}      # (family, price) -> +1 above / -1 below at the previous bar
        self._idx = -1

    # ---------------------------------------------------------------------------------------
    def _start_session(self, c: Candle):
        if self._cur is not None:
            self._prev_summary = {"high": self._cur["high"], "low": self._cur["low"], "close": self._cur["last_continuous_close"], "date": self._cur["date"]}
        self._cur = {"date": session_date(c.timestamp), "high": c.high, "low": c.low, "last_continuous_close": c.close, "open": c.open, "or_high": None, "or_low": None, "bars": 0}
        self._idx = -1
        self._levels = {f: [] for f in FAMILIES}
        self._prev_close_side = {}
        ts = c.timestamp.isoformat()
        if self._prev_summary is not None:
            ps = self._prev_summary
            self._levels["PDH"] = [Level("PDH", ps["high"], ts, f"previous session {ps['date']} high (regular session bars)")]
            self._levels["PDL"] = [Level("PDL", ps["low"], ts, f"previous session {ps['date']} low")]
            self._levels["PDC"] = [Level("PDC", ps["close"], ts, f"previous session {ps['date']} close = {self.p.close_definition}")]
            P = (ps["high"] + ps["low"] + ps["close"]) / 3
            self._levels["PIVOT"] = [Level("PIVOT", round(x, 2), ts, f"floor pivot {n} from previous session") for n, x in
                                     (("P", P), ("R1", 2 * P - ps["low"]), ("S1", 2 * P - ps["high"]), ("R2", P + (ps["high"] - ps["low"])), ("S2", P - (ps["high"] - ps["low"])))]
        self._levels["OPEN"] = [Level("OPEN", c.open, ts, "session open = first bar open")]

    def known_levels(self) -> list:
        """All Level objects currently in the registry (for Level 9)."""
        return [L for fam in FAMILIES for L in self._levels.get(fam, [])]

    def _is_static(self, c: Candle) -> bool:
        return c.timestamp.date() >= self.p.static_window_from and c.timestamp.time() >= self.p.static_window_start

    def process(self, c: Candle, rec: AnatomyRecord) -> LevelRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            self._start_session(c); self._session = d
        self._idx += 1
        p = self.p
        ts = c.timestamp.isoformat()
        cur = self._cur
        cur["high"] = max(cur["high"], c.high); cur["low"] = min(cur["low"], c.low)
        if not self._is_static(c):
            cur["last_continuous_close"] = c.close
        cur["bars"] += 1
        # opening range: first k minutes; known at the close of the bar whose end reaches k minutes
        k_bars = max(1, p.opening_range_minutes // max(1, int(c.interval.total_seconds() // 60)))
        if cur["bars"] <= k_bars:
            cur["or_high"] = c.high if cur["or_high"] is None else max(cur["or_high"], c.high)
            cur["or_low"] = c.low if cur["or_low"] is None else min(cur["or_low"], c.low)
            if cur["bars"] == k_bars:
                self._levels["ORH"] = [Level("ORH", cur["or_high"], ts, f"high of the first {p.opening_range_minutes} minutes")]
                self._levels["ORL"] = [Level("ORL", cur["or_low"], ts, f"low of the first {p.opening_range_minutes} minutes")]
        # swing levels (confirmed zigzag pivots of this session)
        sw = self._swings.process(c, rec)
        for e in sw.events:
            if e.detector != "ZIGZAG":
                continue
            fam = "SWING_HIGH" if e.kind == "HIGH" else "SWING_LOW"
            self._levels[fam] = (self._levels[fam] + [Level(fam, e.price, ts, f"zigzag swing {e.kind} pivot {e.pivot_time[11:16]} confirmed {ts[11:16]}")])[-p.n_swing_levels:]
        # round grid: nearest grid lines around the close
        base = math.floor(c.close / p.round_grid) * p.round_grid
        self._levels["ROUND"] = [Level("ROUND", base + i * p.round_grid, ts, f"round grid {p.round_grid:g}") for i in (-1, 0, 1, 2)]
        # distances, touches, labels, crosses
        ref = rec.reference_range
        nearest, touches, labels = {}, [], []
        for fam in FAMILIES:
            lv = self._levels.get(fam, [])
            if not lv:
                continue
            best = min(lv, key=lambda L: abs(c.close - L.price))
            dist = (c.close - best.price) / ref if ref else None
            side = "ABOVE" if c.close > best.price else "BELOW" if c.close < best.price else "ON"
            nearest[fam] = {"price": best.price, "dist_ref": round(dist, 4) if dist is not None else None, "side": side, "known_from": best.known_from}
            if any(L.price >= c.low and L.price <= c.high for L in lv):
                touches.append(fam)
            if dist is not None and abs(dist) <= p.at_level_ref and self._idx > 0:
                labels.append(f"AT_{fam}|{side}")
            for L in lv:
                key = (fam, L.price)
                s_now = 1 if c.close > L.price else -1 if c.close < L.price else 0
                s_prev = self._prev_close_side.get(key)
                if s_prev is not None and s_now != 0 and s_now != s_prev and L.known_from <= ts and L.known_from != ts:
                    labels.append(f"CROSS_{fam}_{'UP' if s_now > 0 else 'DOWN'}")
                if s_now != 0:
                    self._prev_close_side[key] = s_now
        return LevelRecord(ts, self._idx, {f: [L.price for L in self._levels.get(f, [])] for f in FAMILIES if self._levels.get(f)}, nearest, touches, sorted(set(labels)))


def replay_levels(items: Iterable[tuple[Candle, AnatomyRecord]], p: LevelParams = DEFAULT_LEVEL_PARAMS) -> Iterator[tuple[int, Candle, LevelRecord]]:
    eng = LevelEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
