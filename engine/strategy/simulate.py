"""GAP_FADE simulation (PROJECT DEFINITION, Skill 20)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from .rules import GapFadeRule, STOP_REF_Q90, STOP_FLOOR_REF, MIN_60_BAR, COST_REF_1M
from .sessions import Session


@dataclass
class GapTrade:
    date: str
    direction: str
    entry_bar: int
    entry: float
    stop: float
    target: float
    exit_bar: int
    exit_price: float
    exit_reason: str            # TARGET | STOP | TIME | SESSION_END
    gap_pct: float
    stop_ref: float
    r_gross: float
    mae_r: float
    mfe_r: float
    cost_r: float               # development-era cost
    r_net: float                # development-era
    cost_r_stressed: float
    r_net_stressed: float

    def as_dict(self) -> dict:
        return asdict(self)


def simulate_gap_fade(s: Session, rule: GapFadeRule) -> Optional[GapTrade]:
    if s.prev_close is None or s.gap_class != "SMALL" or s.gap_sign != rule.sign:
        return None
    k = rule.entry_bar
    if k >= len(s.bars) or s.refs[k] is None or not s.refs[k]:
        return None
    sgn = 1 if rule.direction == "UP" else -1
    target = s.prev_close
    # the previous close must not have been touched before entry (the gap would already be filled)
    for b in s.bars[:k]:
        if b.low <= target <= b.high:
            return None
    e_px = s.bars[k].open
    if (e_px >= target) if sgn > 0 else (e_px <= target):
        return None
    ref = s.refs[k]; gap_abs = abs(s.bars[0].open - s.prev_close)
    dist = gap_abs if rule.stop == "GAP_1" else STOP_REF_Q90 * ref
    dist = max(dist, STOP_FLOOR_REF * ref)
    stop = e_px - sgn * dist
    last = len(s.bars) - 1 if rule.exit == "SESSION" else min(MIN_60_BAR, len(s.bars) - 1)
    mae = mfe = 0.0; exit_px = reason = x = None
    for j in range(k, last + 1):
        b = s.bars[j]
        adverse = (b.low - e_px) if sgn > 0 else (e_px - b.high); favourable = (b.high - e_px) if sgn > 0 else (e_px - b.low)
        mae = min(mae, adverse / dist); mfe = max(mfe, favourable / dist)
        if (b.low <= stop) if sgn > 0 else (b.high >= stop):
            exit_px = min(stop, b.open) if sgn > 0 else max(stop, b.open); reason, x = "STOP", j; break
        if (b.high >= target) if sgn > 0 else (b.low <= target):
            exit_px = max(target, b.open) if sgn > 0 else min(target, b.open); reason, x = "TARGET", j; break
    if exit_px is None:
        x = last; exit_px = s.bars[last].close; reason = "SESSION_END" if rule.exit == "SESSION" else "TIME"
    r = sgn * (exit_px - e_px) / dist
    c1 = COST_REF_1M["development_era"] * ref / dist; c2 = COST_REF_1M["stressed"] * ref / dist
    return GapTrade(s.date.isoformat(), rule.direction, k, e_px, stop, target, x, exit_px, reason, round(s.gap_pct, 4), round(dist / ref, 3), round(r, 4),
                    round(mae, 4), round(mfe, 4), round(c1, 4), round(r - c1, 4), round(c2, 4), round(r - c2, 4))


def run_rule(sessions: list, rule: GapFadeRule) -> list:
    out = []
    for s in sessions:
        t = simulate_gap_fade(s, rule)
        if t is not None:
            out.append(t)
    return out
