"""Managed trade simulation in R (PROJECT DEFINITION, Skill 19). Same fill conventions as Level 17: entry at the next bar's open; when a
bar touches the stop and a favourable level the stop is assumed first; a bar opening beyond a level fills at the open; stop/trail updates
made at a bar's close take effect from the next bar (no same-bar look-ahead)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Optional

from engine.entries.simulate import cost_in_r, MIN_STOP_REF, STOP_SLIPPAGE_REF
from .rules import ManagementRule, STOP_REF, HOLD


@dataclass
class ManagedTrade:
    signal_index: int
    entry_index: int
    exit_index: int
    direction: str
    entry: float
    initial_stop: float
    final_stop: float
    exit_price: float            # price of the final leg
    exit_reason: str             # STOP | BE_STOP | TRAIL_STOP | TIME | SESSION_END (final leg)
    partial_taken: bool
    stop_ref: float
    r_gross: float               # position-weighted
    mae_r: float
    mfe_r: float
    cost_r: float
    r_net: float
    bars_held: int

    def as_dict(self) -> dict:
        return asdict(self)


def simulate_managed(seq, signal_indices, direction: str, rule: ManagementRule, interval_minutes: int, min_stop_ref: float = MIN_STOP_REF,
                     stop_slippage_ref: float = STOP_SLIPPAGE_REF) -> list:
    sgn = 1 if direction == "UP" else -1
    n = len(seq); trades = []
    hold = HOLD[rule.exit]
    for i in signal_indices:
        if i + 1 >= n or seq[i + 1]["session_index"] != seq[i]["session_index"] + 1:
            continue
        ref = seq[i]["rec"].reference_range
        if not ref:
            continue
        e_idx = i + 1; e_px = seq[e_idx]["candle"].open
        stop_mult = max(STOP_REF[rule.stop], min_stop_ref)
        stop_px = e_px - sgn * stop_mult * ref
        stop_dist = abs(e_px - stop_px); stop_ref = stop_dist / ref
        one_r = e_px + sgn * stop_dist
        cur_stop = stop_px; stop_kind = "STOP"
        best = e_px                                   # best favourable extreme so far
        mae = mfe = 0.0
        frac = 1.0; realised = 0.0                    # position fraction open; R realised by closed legs
        partial = False
        exit_px = reason = x_idx = None
        recent = []                                   # adverse extremes of closed bars since entry (for STRUCT trail)
        for j in range(e_idx, min(n, e_idx + hold)):
            if seq[j]["session_index"] != seq[e_idx]["session_index"] + (j - e_idx):
                exit_px, reason, x_idx = seq[j - 1]["candle"].close, "SESSION_END", j - 1
                break
            b = seq[j]["candle"]
            adverse = (b.low - e_px) if sgn > 0 else (e_px - b.high)
            favourable = (b.high - e_px) if sgn > 0 else (e_px - b.low)
            mae = min(mae, adverse / stop_dist); mfe = max(mfe, favourable / stop_dist)
            # 1. stop (current, set at an earlier close) — assumed first
            if (b.low <= cur_stop) if sgn > 0 else (b.high >= cur_stop):
                fill = (min(cur_stop, b.open) if sgn > 0 else max(cur_stop, b.open)) - sgn * stop_slippage_ref * ref
                exit_px, reason, x_idx = fill, stop_kind, j
                break
            # 2. partial at +1R
            if rule.partial == "HALF_1R" and not partial and ((b.high >= one_r) if sgn > 0 else (b.low <= one_r)):
                fill = max(one_r, b.open) if sgn > 0 else min(one_r, b.open)
                realised += 0.5 * sgn * (fill - e_px) / stop_dist; frac = 0.5; partial = True
            # 3. time exit at this bar's close
            if j == e_idx + hold - 1:
                exit_px, reason, x_idx = b.close, "TIME", j
                break
            # 4. end-of-bar stop updates, effective from the next bar
            best = max(best, b.high) if sgn > 0 else min(best, b.low)
            recent.append(b.low if sgn > 0 else b.high)
            if rule.breakeven == "BE_1R" and favourable >= stop_dist:
                cand = e_px
                if (cand > cur_stop) if sgn > 0 else (cand < cur_stop):
                    cur_stop, stop_kind = cand, "BE_STOP"
            if rule.trail == "TRAIL_2":
                cand = best - sgn * rule.trail_ref * ref
                if (cand > cur_stop) if sgn > 0 else (cand < cur_stop):
                    cur_stop, stop_kind = cand, "TRAIL_STOP"
            elif rule.trail == "STRUCT_3" and len(recent) >= rule.struct_bars:
                window = recent[-rule.struct_bars:]
                cand = min(window) if sgn > 0 else max(window)
                if (cand > cur_stop) if sgn > 0 else (cand < cur_stop):
                    cur_stop, stop_kind = cand, "TRAIL_STOP"
        if exit_px is None:                             # data ended inside the trade
            x_idx = min(n - 1, e_idx + hold - 1); exit_px, reason = seq[x_idx]["candle"].close, "SESSION_END"
        r_gross = realised + frac * sgn * (exit_px - e_px) / stop_dist
        c_r = cost_in_r(interval_minutes, stop_ref)
        trades.append(ManagedTrade(i, e_idx, x_idx, direction, e_px, stop_px, cur_stop, exit_px, reason, partial, round(stop_ref, 3), round(r_gross, 4),
                                   round(mae, 4), round(mfe, 4), round(c_r, 4), round(r_gross - c_r, 4), x_idx - e_idx + 1))
    return trades
