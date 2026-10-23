"""Per-bar integrated description with price location and current leg (PROJECT DEFINITION, Skill 07)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.behaviour import interpret, FORBIDDEN_LEVEL3
from engine.sequences import SequenceEngine
from engine.structure import StructureEngine
from .params import PriceActionParams, DEFAULT_PA_PARAMS

FORBIDDEN_LEVEL7 = FORBIDDEN_LEVEL3 + ("support", "resistance", "breakout", "pattern", "setup", "target", "stop loss", "bullish bias", "bearish bias")


@dataclass
class PriceActionRecord:
    timestamp: str
    session_index: int
    # Level 2-3
    size_class: str
    body_class: str
    close_zone: str
    labels3: list
    # Level 4
    run_direction: Optional[str]
    run_length: int
    impulse_state: str
    events4: list
    # Level 6
    trend_state: str
    last_high: Optional[float]
    last_low: Optional[float]
    events6: list
    # Level 7: location
    struct_location: Optional[float]      # (close - last_low) / (last_high - last_low); may be <0 or >1
    struct_zone: str                      # BELOW | LOWER | MIDDLE | UPPER | ABOVE | UNDEFINED
    dist_to_high_ref: Optional[float]     # (last_high - close) / reference_range (signed: negative when above)
    dist_to_low_ref: Optional[float]      # (close - last_low) / reference_range
    session_high: float
    session_low: float
    session_location: Optional[float]     # (close - session_low) / (session_high - session_low)
    new_session_high: bool
    new_session_low: bool
    from_open_ref: Optional[float]        # (close - session open) / reference_range
    # Level 7: current leg
    leg_direction: Optional[str]          # UP | DOWN (from the most recent confirmed pivot) | None
    leg_bars: Optional[int]
    leg_size_ref: Optional[float]
    leg_type: str                         # WITH_TREND | AGAINST_TREND | UNDEFINED
    retracement: Optional[float]          # against-trend leg: share of the previous leg retraced
    events7: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


def _zone(loc: Optional[float], edges) -> str:
    if loc is None:
        return "UNDEFINED"
    if loc < 0:
        return "BELOW"
    if loc > 1:
        return "ABOVE"
    if loc < edges[0]:
        return "LOWER"
    if loc < edges[1]:
        return "MIDDLE"
    return "UPPER"


class PriceActionEngine:
    def __init__(self, p: PriceActionParams = DEFAULT_PA_PARAMS):
        self.p = p
        self._seq = SequenceEngine(p.sequences)
        self._struct = StructureEngine(p.structure)
        self._session = None
        self._reset()

    def _reset(self):
        self._idx = -1
        self._sh = self._sl = None
        self._open = None

    def process(self, c: Candle, rec: AnatomyRecord) -> PriceActionRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            self._reset(); self._session = d
        self._idx += 1
        p = self.p
        ref = rec.reference_range
        s4 = self._seq.process(c, rec)
        s6 = self._struct.process(c, rec)
        labels3 = [i.label for i in interpret(rec)]
        events7 = []
        # --- session range and open ----------------------------------------------------------------
        if self._idx == 0:
            self._sh, self._sl, self._open = c.high, c.low, c.open
            nsh = nsl = False
        else:
            nsh = c.high > self._sh; nsl = c.low < self._sl
            self._sh = max(self._sh, c.high); self._sl = min(self._sl, c.low)
        if nsh:
            events7.append("NEW_SESSION_HIGH")
        if nsl:
            events7.append("NEW_SESSION_LOW")
        sess_loc = (c.close - self._sl) / (self._sh - self._sl) if self._sh > self._sl else None
        if sess_loc is not None and not nsh and sess_loc >= p.session_top:
            events7.append("SESSION_LOC_TOP")
        if sess_loc is not None and not nsl and sess_loc <= p.session_bottom:
            events7.append("SESSION_LOC_BOTTOM")
        from_open = (c.close - self._open) / ref if ref else None
        # --- structural location -------------------------------------------------------------------
        hi, lo = s6.last_high, s6.last_low
        loc = (c.close - lo) / (hi - lo) if (hi is not None and lo is not None and hi > lo) else None
        zone = _zone(loc, p.struct_zone_edges)
        d_hi = (hi - c.close) / ref if (hi is not None and ref) else None
        d_lo = (c.close - lo) / ref if (lo is not None and ref) else None
        if s6.trend_state in ("UP", "DOWN") and zone != "UNDEFINED":
            events7.append(f"{s6.trend_state}|{zone}")
        # --- current leg ---------------------------------------------------------------------------
        piv = self._struct.last_pivot
        leg_dir = leg_bars = leg_size = retr = None
        leg_type = "UNDEFINED"
        if piv is not None:
            leg_dir = "UP" if piv.kind == "LOW" else "DOWN"
            leg_bars = self._idx - piv.pivot_index
            leg_size = abs(c.close - piv.price) / ref if ref else None
            if s6.trend_state in ("UP", "DOWN"):
                leg_type = "WITH_TREND" if leg_dir == s6.trend_state else "AGAINST_TREND"
                events7.append(f"LEG_{leg_type}")
            if hi is not None and lo is not None and hi > lo:
                retr = (hi - c.close) / (hi - lo) if leg_dir == "DOWN" else (c.close - lo) / (hi - lo)
        return PriceActionRecord(
            c.timestamp.isoformat(), self._idx, rec.size_class, rec.shape["body_class"], rec.shape["close_zone"], labels3,
            s4.run_direction, s4.run_length, s4.impulse_state, [e.label for e in s4.events],
            s6.trend_state, hi, lo, [e.label for e in s6.events],
            loc, zone, d_hi, d_lo, self._sh, self._sl, sess_loc, nsh, nsl, from_open,
            leg_dir, leg_bars, leg_size, leg_type, retr, events7)


def describe(r: PriceActionRecord) -> list[str]:
    """Plain-language description of one bar: observations and stored states only."""
    t = r.timestamp[11:16]
    out = [f"{t}: size {r.size_class}, body {r.body_class}, close zone {r.close_zone}" + (f"; Level 3 {', '.join(r.labels3)}" if r.labels3 and r.labels3 != ['UNCLASSIFIED'] else "") + "."]
    if r.run_length:
        out.append(f"Run of {r.run_length} {r.run_direction} closes; impulse state {r.impulse_state}" + (f"; sequence events {', '.join(r.events4)}" if r.events4 else "") + ".")
    if r.trend_state == "UNDEFINED":
        out.append("Trend state UNDEFINED (fewer than two confirmed swings of a kind).")
    else:
        out.append(f"Trend state {r.trend_state}; last swing high {r.last_high}, last swing low {r.last_low}" + (f"; structure events {', '.join(r.events6)}" if r.events6 else "") + ".")
    if r.struct_location is not None:
        out.append(f"Close at {r.struct_location:.2f} of the structural range ({r.struct_zone}); {r.dist_to_high_ref:.1f} reference ranges below the last swing high, {r.dist_to_low_ref:.1f} above the last swing low.")
    if r.session_location is not None:
        extra = " New session high." if r.new_session_high else " New session low." if r.new_session_low else ""
        out.append(f"Close at {r.session_location:.2f} of the session range ({r.session_low}-{r.session_high}); {r.from_open_ref:+.1f} reference ranges from the open.{extra}")
    if r.leg_direction is not None:
        s = f"Current leg {r.leg_direction} for {r.leg_bars} bars" + (f", {r.leg_size_ref:.1f} reference ranges" if r.leg_size_ref is not None else "")
        if r.leg_type != "UNDEFINED":
            s += f", {r.leg_type.lower().replace('_', ' ')}"
        if r.retracement is not None and r.leg_type == "AGAINST_TREND":
            s += f", retracing {r.retracement:.2f} of the previous leg"
        out.append(s + ".")
    return out


def replay_price_action(items: Iterable[tuple[Candle, AnatomyRecord]], p: PriceActionParams = DEFAULT_PA_PARAMS) -> Iterator[tuple[int, Candle, PriceActionRecord]]:
    eng = PriceActionEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
