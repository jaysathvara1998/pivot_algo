"""Study harness for the price-action reading (Skill 33): outcomes, nulls, STATUS_RULE, cost gate, conditioning splits."""
from __future__ import annotations

import math
import random
from collections import defaultdict

from engine.setups import bonferroni_z
from .detect import Bars, detect_all, SETUPS

HORIZONS_INDEX = (1, 5, 20); HORIZONS_STOCK = (1, 5, 10)
STATUS_RULE = {"min_abs_z": 3.0, "min_effect_rate": 0.02, "min_effect_move_ref": 0.1}
COST_REF_5M = 0.5


def outcomes(b: Bars, sigs, horizons):
    """{(setup, h): list of (hit, move_ref, direction, ctx, i)} with non-overlapping signals per (setup, h)."""
    out = defaultdict(list); last = defaultdict(lambda: -10 ** 9)
    for s in sorted(sigs, key=lambda x: x.i):
        for h in horizons:
            key = (s.setup, h)
            if s.i <= last[key] + h - 1 or s.i + h >= b.n or not b.same_session(s.i, s.i + h) or not b.ref[s.i]:
                continue
            sgn = 1 if s.direction == "UP" else -1
            mv = sgn * (b.c[s.i + h] - b.c[s.i]) / b.ref[s.i]
            out[key].append((mv > 0, mv, s.direction, s.ctx, s.i)); last[key] = s.i
    return out


def _mom(xs):
    n = len(xs)
    if n < 2:
        return n, (xs[0] if xs else None), None
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)); return n, m, sd


def _z_rate(k, n, p0):
    return (k / n - p0) / math.sqrt(p0 * (1 - p0) / n) if n and 0 < p0 < 1 else None


def summarise(real: dict, nulls: list, horizons, half_of, fills_real=None, fills_null=None, cost_ref: float | None = None, alpha=0.05, min_n=100) -> dict:
    """real: outcomes dict of the real series (merged across sessions/stocks); nulls: list of outcomes dicts (one per null series)."""
    n_cells = len(SETUPS) * len(horizons); zcrit = bonferroni_z(n_cells, alpha); cells = {}
    for setup in SETUPS:
        for h in horizons:
            rows = real.get((setup, h), []); n = len(rows)
            nk = sum(1 for d in nulls for r in d.get((setup, h), []) if r[0]); nn = sum(len(d.get((setup, h), [])) for d in nulls)
            p0 = nk / nn if nn else None
            k = sum(1 for r in rows if r[0]); rate = k / n if n else None
            z = _z_rate(k, n, p0) if (n and p0 is not None) else None
            mv = [r[1] for r in rows]; nmv = [r[1] for d in nulls for r in d.get((setup, h), [])]
            _, m, sd = _mom(mv); _, m0, sd0 = _mom(nmv)
            zmv = ((m - m0) / math.sqrt(sd * sd / n + sd0 * sd0 / len(nmv))) if n > 1 and len(nmv) > 1 and sd and sd0 else None
            halves = defaultdict(lambda: [0, 0])
            for r in rows:
                hname = half_of(r[4]); halves[hname][0] += r[0]; halves[hname][1] += 1
            hv = {hn: (round(v[0] / v[1], 4) if v[1] else None) for hn, v in halves.items()}
            eff = (rate - p0) if (rate is not None and p0 is not None) else None
            same_sign = all(x is not None and (x - p0 > 0) == (eff > 0) for x in hv.values()) if eff is not None and len(hv) >= 2 else False
            if n < min_n or z is None:
                status = "UNTESTED"
            elif z >= STATUS_RULE["min_abs_z"] and (eff >= STATUS_RULE["min_effect_rate"] or (m is not None and m0 is not None and m - m0 >= STATUS_RULE["min_effect_move_ref"])) and same_sign:
                status = "SUPPORTED_DESCRIPTIVE"
            elif abs(z) >= STATUS_RULE["min_abs_z"]:
                status = "STATISTICAL_ONLY" if z > 0 else "CONTRADICTED"
            else:
                status = "NOT_SUPPORTED"
            cond = {}
            for key in ("state", "pos"):
                grp = defaultdict(lambda: [0, 0])
                for r in rows:
                    g = r[3].get(key); grp[g][0] += r[0]; grp[g][1] += 1
                cond[key] = {str(g): {"n": v[1], "rate": round(v[0] / v[1], 4)} for g, v in grp.items() if v[1] >= 30}
            cell = {"n": n, "hit_rate": round(rate, 4) if rate is not None else None, "null_rate": round(p0, 4) if p0 is not None else None, "null_n": nn, "z_rate": round(z, 2) if z is not None else None,
                    "mean_move_ref": round(m, 4) if m is not None else None, "null_move_ref": round(m0, 4) if m0 is not None else None, "z_move": round(zmv, 2) if zmv is not None else None,
                    "median_move_ref": round(sorted(mv)[n // 2], 4) if n else None, "halves": hv, "same_sign_halves": same_sign, "status": status, "conditioning": cond}
            if cost_ref is not None and status == "SUPPORTED_DESCRIPTIVE":
                cell["cost_gate"] = "TRADEABLE_GROSS" if (m - (m0 or 0)) > cost_ref else "NOT_AFTER_COST"
            if setup == "P14_TIGHT_RANGE_BREAKOUT" and rows:
                cell["failed_within_5_rate"] = round(sum(1 for r in rows if r[3].get("failed_within_5")) / n, 4)
                nf = [r for d in nulls for r in d.get((setup, h), [])]
                cell["null_failed_within_5_rate"] = round(sum(1 for r in nf if r[3].get("failed_within_5")) / len(nf), 4) if nf else None
                cell["measured_move_hit_rate"] = round(sum(1 for r in rows if r[3].get("measured_move_hit")) / n, 4)
                cell["null_measured_move_hit_rate"] = round(sum(1 for r in nf if r[3].get("measured_move_hit")) / len(nf), 4) if nf else None
            cells[f"{setup}|h{h}"] = cell
    out = {"family": n_cells, "bonferroni_z": round(zcrit, 3), "cells": cells}
    if fills_real is not None:
        out["fvg_fill_rates"] = {k: {"real": round(v[0] / v[1], 4) if v[1] else None, "n": v[1], "null": round(fills_null[k][0] / fills_null[k][1], 4) if fills_null and fills_null[k][1] else None} for k, v in fills_real.items()}
    out["count"] = dict(defaultdict(int, {s: sum(1 for c in cells.values() if c["status"] == s) for s in {c["status"] for c in cells.values()}}))
    return out


def merge_outcomes(dicts):
    out = defaultdict(list)
    for d in dicts:
        for k, v in d.items():
            out[k].extend(v)
    return out


def block_shuffle_bars(o, h, l, c, v, rnd: random.Random, block: int = 20):
    """Stock null: shuffle 20-day blocks of (return, intraday shape, volume) and rebuild bars — keeps volatility and the range/volume relation inside blocks, destroys sequence."""
    n = len(c); days = []
    for i in range(1, n):
        if c[i] and c[i - 1] and o[i] and h[i] and l[i]:
            days.append((c[i] / c[i - 1], o[i] / c[i], h[i] / c[i], l[i] / c[i], v[i] if v else None))
    blocks = [days[k:k + block] for k in range(0, len(days), block)]; rnd.shuffle(blocks)
    seq = [d for b in blocks for d in b]; px = c[0] if c[0] else 100.0
    O, H, L, C, V = [o[0] or px], [h[0] or px], [l[0] or px], [px], [v[0] if v else None]
    for r, oc, hc, lc, vol in seq:
        px = px * r; C.append(px); O.append(px * oc); H.append(px * hc); L.append(px * lc); V.append(vol)
    return O, H, L, C, (V if v else None)
