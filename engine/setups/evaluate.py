"""Development-window evaluation of registered setups with deflation and a cost gate."""
from __future__ import annotations

import math
from collections import defaultdict

from engine.behaviour.study import binomial_z, two_sample_z
from .registry import REGISTRY, Setup
from .split import assert_window

# transaction cost in reference ranges (Foundations card 26: NIFTY futures round trip ≈ 14.9 points post-2026-04-01, ≈ 7.3 before;
# a 5m NIFTY reference range ≈ 19 points, a 1m ≈ 7): PROJECT DEFINITION gate values
COST_REF = {1: 1.0, 5: 0.5, 15: 0.3}


def _norm_ppf(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if 0.5 * (1 + math.erf(mid / math.sqrt(2))) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def bonferroni_z(n_trials: int, alpha: float = 0.05) -> float:
    """One-sided z a trial must clear so that the family-wise error is alpha across n_trials (Bonferroni)."""
    return _norm_ppf(1 - alpha / max(1, n_trials))


def _labels_at(bar_labels: list, state: str, vol: str) -> set:
    return set(bar_labels) | {f"STATE_{state}", f"VOL_{vol}"}


def evaluate_setups(stream, null_stream, registry=REGISTRY, interval_minutes: int = 5, cost_ref: dict = COST_REF, alpha: float = 0.05,
                    fill_null: dict = None) -> dict:
    """stream / null_stream: lists of dicts per bar with keys
       candle, rec (anatomy), labels (list of str), state, vol, session_index, fill (bool|None, context setups).
    Only DEVELOPMENT-window bars of the real stream are evaluated (filtered here); the null stream is used in full.
    fill_null: {setup name: (p_null, n_null)} for FILL setups, from the Level 15 gap-aware null on the same development sessions
    (a gap-less random walk cannot serve as the null for a gap setup)."""
    dev, forbidden = assert_window([(b["candle"], b) for b in stream])
    bars = [b for c, b in dev]
    n_trials = len(registry)
    zcrit = bonferroni_z(n_trials, alpha)
    out = {"interval_minutes": interval_minutes, "development_bars": len(bars), "windows_present_but_not_evaluated": forbidden,
           "n_trials_registered": n_trials, "bonferroni_z_one_sided": round(zcrit, 3), "cost_ref": cost_ref.get(interval_minutes), "setups": {}}

    def outcome(seq, i, setup):
        b = seq[i]
        if setup.expected == "FILL":
            return b.get("fill"), None
        j = i + setup.horizon_bars
        if j >= len(seq) or seq[j]["session_index"] != b["session_index"] + setup.horizon_bars:
            return None, None
        c0, cj = b["candle"], seq[j]["candle"]
        ref = b["rec"].reference_range
        move_ref = (cj.close - c0.close) / ref if ref else None
        if setup.expected == "UP":
            return cj.close > c0.close, move_ref
        if setup.expected == "DOWN":
            return cj.close < c0.close, -move_ref if move_ref is not None else None
        if setup.expected == "LARGE":
            return seq[j]["rec"].size_class in ("LARGE", "EXTREME"), None
        return None, None

    def scan(seq, setup):
        k = n = 0; moves = []
        for i, b in enumerate(seq):
            labs = _labels_at(b["labels"], b["state"], b["vol"])
            if not all(r in labs for r in setup.requires) or any(x in labs for x in setup.excludes):
                continue
            hit, mv = outcome(seq, i, setup)
            if hit is None:
                continue
            n += 1; k += bool(hit)
            if mv is not None:
                moves.append(mv)
        return k, n, moves

    # base rates for size and fill outcomes come from the development window itself
    base_large = None
    if bars:
        hits = [(bars[i + 1]["rec"].size_class in ("LARGE", "EXTREME")) for i in range(len(bars) - 1) if bars[i + 1]["session_index"] == bars[i]["session_index"] + 1]
        base_large = sum(hits) / len(hits) if hits else None
    for s in registry:
        if s.interval_minutes != interval_minutes:
            continue
        k, n, moves = scan(bars, s)
        nk, nn, _ = scan(null_stream, s)
        p = k / n if n else None
        if s.expected in ("UP", "DOWN"):
            p_null = nk / nn if nn else None
            z = two_sample_z(k, n, nk, nn) if (n and nn) else None
        elif s.expected == "LARGE":
            p_null = base_large; z = binomial_z(k, n, base_large) if (n and base_large) else None
        else:
            fp = (fill_null or {}).get(s.name)
            if fp:
                p_null, nn = fp[0], fp[1]; z = two_sample_z(k, n, round(p_null * nn), nn) if (n and nn) else None
            else:
                p_null = nk / nn if nn else None; z = two_sample_z(k, n, nk, nn) if (n and nn) else None
        mean_move = sum(moves) / len(moves) if moves else None
        sd_move = math.sqrt(sum((m - mean_move) ** 2 for m in moves) / (len(moves) - 1)) if len(moves) > 1 else None
        t_move = mean_move / (sd_move / math.sqrt(len(moves))) if (sd_move and len(moves) > 1) else None
        cost = cost_ref.get(interval_minutes)
        verdict = "NOT_ENOUGH_DATA" if not n or n < 100 else ("INSIDE_NOISE" if z is None or z < zcrit else
                  ("BEATS_NOISE_NOT_COST" if (mean_move is not None and cost is not None and mean_move < cost) else "BEATS_NOISE_AND_COST" if mean_move is not None else "BEATS_NOISE"))
        out["setups"][s.name] = {"hypothesis": s.hypothesis, "expected": s.expected, "horizon_bars": s.horizon_bars, "n": n, "k": k, "p": round(p, 4) if p is not None else None,
                                 "p_null": round(p_null, 4) if p_null is not None else None, "diff": round(p - p_null, 4) if (p is not None and p_null is not None) else None,
                                 "z": round(z, 2) if z is not None else None, "clears_bonferroni": bool(z is not None and z >= zcrit),
                                 "mean_move_ref": round(mean_move, 3) if mean_move is not None else None, "t_move": round(t_move, 2) if t_move is not None else None,
                                 "cost_ref": cost, "edge_after_cost_ref": round(mean_move - cost, 3) if (mean_move is not None and cost is not None) else None,
                                 "verdict": verdict, "n_null": nn}
    return out
