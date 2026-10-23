"""Bar-by-bar trade simulation in R (PROJECT DEFINITION, Skill 17)."""
from __future__ import annotations

import math
from dataclasses import dataclass, asdict
from typing import Optional

from .rules import EntryRule

COST_REF = {1: 1.0, 5: 0.5, 15: 0.3}      # round-trip cost in reference ranges (Level 16 gate; card 26)
MIN_STOP_REF = 0.5                       # stops closer than half a reference range are widened to it: tiny stops make R meaningless and
                                         # let discrete-path overshoot masquerade as profit (lesson 2026-10-04: +0.7R "edge" on a pure random walk)
STOP_SLIPPAGE_REF = 0.0                  # extra adverse fill beyond the stop, in reference ranges; 0 until a live pilot measures it (card 30)


def cost_in_r(interval_minutes: int, stop_ref: float) -> float:
    """Cost in R = cost in reference ranges ÷ stop distance in reference ranges."""
    return COST_REF[interval_minutes] / stop_ref if stop_ref > 0 else float("inf")


@dataclass
class Trade:
    signal_index: int
    entry_index: int
    exit_index: int
    direction: str
    entry: float
    stop: float
    target: Optional[float]
    exit_price: float
    exit_reason: str          # STOP | TARGET | TIME | SESSION_END
    stop_ref: float
    r_gross: float
    mae_r: float              # worst adverse excursion in R (<= 0)
    mfe_r: float              # best favourable excursion in R (>= 0)
    cost_r: float
    r_net: float

    def as_dict(self) -> dict:
        return asdict(self)


def simulate(seq, signal_indices, direction: str, rule: EntryRule, interval_minutes: int, min_stop_ref: float = MIN_STOP_REF,
             stop_slippage_ref: float = STOP_SLIPPAGE_REF) -> list:
    """seq: list of dicts with 'candle', 'rec', 'session_index'. For each signal bar index apply the rule. Fills are conservative:
    market entries at the next bar's open; stop entries at the extreme price (or the next open if gapped through); when a bar touches
    both stop and target the stop is assumed first; exits at the stop / target price (or at the open when a bar opens beyond them) or
    the exit bar's close. Stops closer than min_stop_ref reference ranges are widened to that distance."""
    sgn = 1 if direction == "UP" else -1
    trades = []
    n = len(seq)
    for i in signal_indices:
        if i + 1 >= n or seq[i + 1]["session_index"] != seq[i]["session_index"] + 1:
            continue
        sig = seq[i]["candle"]; ref = seq[i]["rec"].reference_range
        if not ref:
            continue
        # ---- trigger -------------------------------------------------------------------------------
        e_idx = e_px = None
        if rule.trigger == "NEXT_OPEN":
            e_idx, e_px = i + 1, seq[i + 1]["candle"].open
        elif rule.trigger == "CONFIRM_CLOSE":
            nb = seq[i + 1]["candle"]
            if (nb.close > nb.open) if sgn > 0 else (nb.close < nb.open):
                if i + 2 < n and seq[i + 2]["session_index"] == seq[i]["session_index"] + 2:
                    e_idx, e_px = i + 2, seq[i + 2]["candle"].open
        else:   # BREAK_EXTREME
            level = sig.high if sgn > 0 else sig.low
            for j in range(i + 1, min(n, i + 1 + rule.break_within)):
                if seq[j]["session_index"] != seq[i]["session_index"] + (j - i):
                    break
                b = seq[j]["candle"]
                if (b.high > level) if sgn > 0 else (b.low < level):
                    e_idx = j; e_px = max(level, b.open) if sgn > 0 else min(level, b.open)
                    break
        if e_idx is None:
            continue
        # ---- stop ----------------------------------------------------------------------------------
        if rule.stop == "REF_1":
            stop_px = e_px - sgn * 1.0 * ref
        elif rule.stop == "REF_2":
            stop_px = e_px - sgn * 2.0 * ref
        else:
            stop_px = sig.low if sgn > 0 else sig.high
        degenerate = (stop_px >= e_px) if sgn > 0 else (stop_px <= e_px)
        if degenerate or abs(e_px - stop_px) < min_stop_ref * ref:
            stop_px = e_px - sgn * min_stop_ref * ref                 # degenerate or too-tight stop: widened to the floor
        stop_dist = abs(e_px - stop_px)
        if stop_dist <= 0:
            continue
        stop_ref = stop_dist / ref
        target = None
        if rule.exit == "TARGET_1R":
            target = e_px + sgn * stop_dist
        elif rule.exit == "TARGET_2R":
            target = e_px + sgn * 2 * stop_dist
        hold = 1 if rule.exit == "TIME_1" else 3 if rule.exit == "TIME_3" else rule.max_hold
        # ---- walk forward ------------------------------------------------------------------------------
        mae = mfe = 0.0; exit_px = None; reason = None; x_idx = None
        for j in range(e_idx, min(n, e_idx + hold)):
            if seq[j]["session_index"] != seq[e_idx]["session_index"] + (j - e_idx):
                exit_px, reason, x_idx = seq[j - 1]["candle"].close, "SESSION_END", j - 1
                break
            b = seq[j]["candle"]
            adverse = (b.low - e_px) if sgn > 0 else (e_px - b.high)
            favourable = (b.high - e_px) if sgn > 0 else (e_px - b.low)
            mae = min(mae, adverse / stop_dist); mfe = max(mfe, favourable / stop_dist)
            hit_stop = (b.low <= stop_px) if sgn > 0 else (b.high >= stop_px)
            hit_target = target is not None and ((b.high >= target) if sgn > 0 else (b.low <= target))
            if hit_stop:
                fill = min(stop_px, b.open) if sgn > 0 else max(stop_px, b.open)          # a bar opening beyond the stop fills at the open
                exit_px, reason, x_idx = fill - sgn * stop_slippage_ref * ref, "STOP", j; break
            if hit_target:
                fill = max(target, b.open) if sgn > 0 else min(target, b.open)             # a bar opening beyond the target fills at the open
                exit_px, reason, x_idx = fill, "TARGET", j; break
            if j == e_idx + hold - 1:
                exit_px, reason, x_idx = b.close, "TIME", j
        if exit_px is None:
            exit_px, reason, x_idx = seq[min(n - 1, e_idx + hold - 1)]["candle"].close, "SESSION_END", min(n - 1, e_idx + hold - 1)
        r_gross = sgn * (exit_px - e_px) / stop_dist
        c_r = cost_in_r(interval_minutes, stop_ref)
        trades.append(Trade(i, e_idx, x_idx, direction, e_px, stop_px, target, exit_px, reason, round(stop_ref, 3), round(r_gross, 4), round(mae, 4), round(mfe, 4), round(c_r, 4), round(r_gross - c_r, 4)))
    return trades


def metrics(trades: list) -> dict:
    n = len(trades)
    if n == 0:
        return {"trades": 0}
    net = [t.r_net for t in trades]; gross = [t.r_gross for t in trades]
    mean = sum(net) / n; mg = sum(gross) / n
    sd = math.sqrt(sum((x - mean) ** 2 for x in net) / (n - 1)) if n > 1 else 0.0
    t_stat = mean / (sd / math.sqrt(n)) if sd else None
    wins = [x for x in net if x > 0]; losses = [x for x in net if x <= 0]
    pf = (sum(wins) / -sum(losses)) if losses and sum(losses) < 0 else None
    eq = 0.0; peak = 0.0; dd = 0.0
    for x in net:
        eq += x; peak = max(peak, eq); dd = min(dd, eq - peak)
    reasons = {}
    for t in trades:
        reasons[t.exit_reason] = reasons.get(t.exit_reason, 0) + 1
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    return {"trades": n, "win_rate": round(len(wins) / n, 4), "mean_r_gross": round(mg, 4), "mean_r_net": round(mean, 4), "sd_r_net": round(sd, 4),
            "t_stat_net": round(t_stat, 2) if t_stat is not None else None, "sqn": round(mean / sd * math.sqrt(min(n, 100)), 2) if sd else None,
            "profit_factor": round(pf, 3) if pf is not None else None, "max_drawdown_r": round(dd, 2), "mean_cost_r": round(sum(t.cost_r for t in trades) / n, 4),
            "median_mae_r_winners": med([t.mae_r for t in trades if t.r_net > 0]), "median_mae_r_losers": med([t.mae_r for t in trades if t.r_net <= 0]),
            "median_mfe_r": med([t.mfe_r for t in trades]), "exit_reasons": reasons}
