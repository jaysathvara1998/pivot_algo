"""Split study: selected earlier findings recomputed by session phase, regime and expiry flag; stability verdicts;
clock-minute volatility profile per regime."""
from __future__ import annotations

from collections import defaultdict
from typing import Callable, Sequence

from engine.behaviour.study import OUTCOMES, pair_bars, binomial_z
from .labels import SessionEngine, DEFAULT_SESSION_PARAMS

STABILITY_RULE = {"min_split_n": 300, "max_abs_z_vs_pooled": 3.0, "same_sign_as_pooled": True, "min_pooled_abs_z": 3.0}


def split_study(items, finding_labels_of: Callable, null_items, split_keys: Sequence[str] = ("phase", "regime", "expiry"),
                outcome_keys: Sequence[str] = ("next_bullish", "next_large"), instrument: str = "nifty") -> dict:
    """For each finding label and outcome: pooled rate vs the random-walk conditional rate (direction) or the unconditional rate
    (size), then the same rate within each split (phase / regime / expiry-or-not). Verdict per (label, outcome, split): STABLE if
    every split with n >= min_split_n has the same sign of (rate - null) as the pooled result and differs from the pooled rate by
    |z| < max_abs_z_vs_pooled; SIGN_FLIP / HETEROGENEOUS otherwise; INSUFFICIENT with fewer than two qualifying splits."""
    outs = {o.key: o for o in OUTCOMES if o.key in outcome_keys}
    sess = SessionEngine(DEFAULT_SESSION_PARAMS)
    srecs = [sess.process(c, r) for c, r in items]
    labels = [finding_labels_of(r) for c, r in items]
    null_labels = [finding_labels_of(r) for c, r in null_items]
    pairs = list(pair_bars(items)); npairs = list(pair_bars(null_items))
    base_large = (sum(1 for i, j in pairs if outs["next_large"].fn(items[i][0], items[j][0], items[j][1])) / len(pairs)) if ("next_large" in outs and pairs) else None
    null_rate = {}
    for i, j in npairs:
        for lab in null_labels[i]:
            for ok, o in outs.items():
                if o.null_kind == "rw_direction":
                    d = null_rate.setdefault((lab, ok), [0, 0]); d[0] += o.fn(null_items[i][0], null_items[j][0], null_items[j][1]); d[1] += 1
    counts = defaultdict(lambda: [0, 0])          # (lab, ok, split_name, split_value) -> [k, n]
    for i, j in pairs:
        c0, r0 = items[i]; c1, r1 = items[j]; s0 = srecs[i]
        splits = {"phase": s0.phase, "regime": s0.regime, "expiry": "EXPIRY" if s0.flags.get(f"{instrument}_weekly_expiry") else "NON_EXPIRY"}
        for lab in labels[i]:
            for ok, o in outs.items():
                hit = o.fn(c0, c1, r1)
                d = counts[(lab, ok, "ALL", "ALL")]; d[0] += hit; d[1] += 1
                for sk in split_keys:
                    d = counts[(lab, ok, sk, splits[sk])]; d[0] += hit; d[1] += 1
    res = {"rule": STABILITY_RULE, "findings": {}}
    for lab in sorted(set(l for (l, ok, a, b) in counts)):
        for ok, o in outs.items():
            k, n = counts.get((lab, ok, "ALL", "ALL"), [0, 0])
            if not n:
                continue
            if o.null_kind == "rw_direction":
                nk, nn = null_rate.get((lab, ok), [0, 0]); p_null = nk / nn if nn else None
            else:
                p_null = base_large
            p = k / n
            entry = {"n": n, "p": round(p, 4), "p_null": round(p_null, 4) if p_null is not None else None,
                     "diff": round(p - p_null, 4) if p_null is not None else None, "z": round(binomial_z(k, n, p_null), 2) if p_null else None, "splits": {}}
            for sk in split_keys:
                cells = {}
                for (l, o2, a, b), (kk, nn2) in counts.items():
                    if l == lab and o2 == ok and a == sk and nn2:
                        pp = kk / nn2
                        cells[b] = {"n": nn2, "p": round(pp, 4), "diff_vs_null": round(pp - p_null, 4) if p_null is not None else None,
                                    "z_vs_pooled": round(binomial_z(kk, nn2, p), 2) if 0 < p < 1 else None}
                big = {b: v for b, v in cells.items() if v["n"] >= STABILITY_RULE["min_split_n"]}
                if p_null is None or entry["z"] is None or abs(entry["z"]) < STABILITY_RULE["min_pooled_abs_z"]:
                    verdict = "NO_POOLED_EFFECT"          # nothing to be stable about: the pooled finding itself is inside noise
                elif len(big) < 2:
                    verdict = "INSUFFICIENT"
                else:
                    pooled_sign = 1 if p > p_null else -1 if p < p_null else 0
                    same = all(((v["p"] - p_null) > 0) == (pooled_sign > 0) for v in big.values()) if pooled_sign else True
                    calm = all(abs(v["z_vs_pooled"]) < STABILITY_RULE["max_abs_z_vs_pooled"] for v in big.values())
                    verdict = "STABLE" if (same and calm) else ("SIGN_FLIP" if not same else "HETEROGENEOUS")
                entry["splits"][sk] = {"verdict": verdict, "cells": dict(sorted(cells.items()))}
            res["findings"][f"{lab}::{ok}"] = entry
    return res


def clock_profile_by_regime(items, bucket_minutes: int = 15) -> dict:
    """Median range (% of close) by clock bucket and regime (deseasonalisation reference, re-estimated per regime; first bar separate)."""
    sess = SessionEngine(DEFAULT_SESSION_PARAMS)
    rows = defaultdict(list); first = defaultdict(list)
    for c, r in items:
        s = sess.process(c, r)
        if s.first_bar:
            first[s.regime].append(100 * (c.high - c.low) / c.close); continue
        if s.static_window:
            continue
        rows[(s.regime, s.minutes_since_open // bucket_minutes)].append(100 * (c.high - c.low) / c.close)
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    regimes = sorted(set(k[0] for k in rows))
    return {"bucket_minutes": bucket_minutes,
            "median_range_pct_by_regime_and_bucket": {r: {str(b): round(med(rows[(r, b)]), 4) for (rr, b) in sorted(rows) if rr == r} for r in regimes},
            "first_bar_median_range_pct_by_regime": {r: round(med(v), 4) for r, v in sorted(first.items())},
            "sessions_by_regime": {r: len(v) for r, v in sorted(first.items())}}
