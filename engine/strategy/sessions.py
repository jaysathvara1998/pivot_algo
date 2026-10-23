"""Session objects for strategy simulation: real sessions from the Level 2 + Level 15 pipelines, and gap-aware random-walk sessions."""
from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Optional

from engine.context import ContextEngine
from engine.ohlc import Candle
from engine.setups.split import window_of


@dataclass
class Session:
    date: object
    bars: list                       # Candle
    refs: list                       # reference range per bar (may be None early in the sample)
    prev_close: Optional[float]
    gap_pct: Optional[float]
    gap_class: Optional[str]
    gap_sign: Optional[str]
    window: str
    synthetic: bool = False
    extra: dict = field(default_factory=dict)


def build_sessions(items) -> list:
    """items: (candle, anatomy record) pairs of the 1-minute stream. The context engine supplies the gap fields of each session."""
    ctx = ContextEngine(); out = []; cur = None
    for c, r in items:
        cr = ctx.process(c, r)
        if cr.session_index == 0:
            cur = Session(c.timestamp.date(), [], [], cr.prev_close, cr.gap_pct, cr.gap_class, cr.gap_sign, window_of(c.timestamp.date()))
            out.append(cur)
        cur.bars.append(c); cur.refs.append(r.reference_range)
    return out


def session_sigma(s: Session) -> float:
    rets = [math.log(b.close / a.close) for a, b in zip(s.bars, s.bars[1:]) if a.close > 0 and b.close > 0]
    if len(rets) < 2:
        return 0.0
    m = sum(rets) / len(rets)
    return math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1))


def null_sessions(real: list, paths_per_session: int = 10, sub_steps: int = 20, seed: int = 20261020) -> list:
    """Gap-aware null: for each real session keep its open, previous close, gap fields, timestamps and reference ranges; replace the path by a
    driftless log random walk with the session's own per-bar sigma (Level 15 construction, with sub-steps so highs/lows are not degenerate)."""
    rnd = random.Random(seed); out = []
    for s in real:
        if s.prev_close is None or len(s.bars) < 2:
            continue
        sig = session_sigma(s) / math.sqrt(sub_steps)
        for k in range(paths_per_session):
            px = s.bars[0].open; bars = []
            for b in s.bars:
                o = px; hi = lo = px
                for _ in range(sub_steps):
                    px *= math.exp(rnd.gauss(0, sig)); hi = max(hi, px); lo = min(lo, px)
                bars.append(Candle(b.timestamp, o, hi, lo, px, b.interval))
            out.append(Session(s.date, bars, list(s.refs), s.prev_close, s.gap_pct, s.gap_class, s.gap_sign, s.window, True, {"path": k}))
    return out
