"""Conditional base-rate study for single-candle interpretations (EMPIRICAL RESULT machinery).

For each label L and each pre-registered next-bar outcome O we compare P(O | bar i carries L)
with a null that answers "what would pure noise with the same geometry give?":

* direction outcomes ("next bar closes up") — null = the same conditional rate in a Brownian
  population passed through the identical anatomy + interpretation pipeline (≈ 0.5 for every
  label; direction is independent of volatility, so a constant-volatility walk is adequate);
* extension outcomes ("next bar trades above this bar's high") — null = the probability that a
  bar with the NEXT bar's realised range, starting at the next bar's open, reaches the distance
  to this bar's extreme, where the distribution of (high - open) / range is taken from the SAME
  population's bars (empirical shape-independence null). Conditioning on the realised next range
  removes volatility clustering, which otherwise masquerades as "follow-through" (lesson 2,
  2026-10-03); using the population's own path-shape distribution removes the dependence on
  print density that a Brownian table has (lesson 3: a 20-step walk leaves max == open on 13 %
  of bars, a 100-step walk on 6 %, so the Brownian null mis-states tiny distances);
* the size outcome ("next bar is LARGE/EXTREME") — null = the population's unconditional rate;
  the difference is volatility clustering itself (card 09), reported as such.

The unconditional rate is reported for every cell too, but is never the status-bearing null for
geometric outcomes: after a bar that closed on its low, "next bar trades above this bar's high"
is rare under noise as well (lesson 1, 2026-10-03).

Pairing rule: bars i and i+1 are consecutive completed bars of the same session; bar i is never
the first bar (card 15) nor in the static window; bar i+1 is never in the static window.
"""
from __future__ import annotations

import bisect
import math
import random
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Callable, Iterable, Optional, Sequence
from zoneinfo import ZoneInfo

from engine.ohlc import Candle
from engine.anatomy import AnatomyParams, AnatomyRecord, DEFAULT_PARAMS, replay_anatomy


@dataclass(frozen=True)
class Outcome:
    key: str
    description: str
    fn: Callable[[Candle, Candle, AnatomyRecord], bool]   # (bar_i, bar_i1, rec_i1) -> bool
    null_kind: str                                        # rw_direction | rw_extension_up | rw_extension_down | unconditional


OUTCOMES: tuple[Outcome, ...] = (
    Outcome("next_bullish", "next bar closes above its open", lambda a, b, rb: b.close > b.open, "rw_direction"),
    Outcome("next_close_above_close", "next bar closes above this bar's close", lambda a, b, rb: b.close > a.close, "rw_direction"),
    Outcome("next_high_exceeds_high", "next bar trades above this bar's high", lambda a, b, rb: b.high > a.high, "rw_extension_up"),
    Outcome("next_low_breaks_low", "next bar trades below this bar's low", lambda a, b, rb: b.low < a.low, "rw_extension_down"),
    Outcome("next_large", "next bar's size class is LARGE or EXTREME (volatility clustering)", lambda a, b, rb: rb.size_class in ("LARGE", "EXTREME"), "unconditional"),
)

STATUS_RULE = {
    "min_abs_z": 3.0,              # card 23 / 29 hurdle for a family of tests
    "min_abs_effect": 0.02,        # 2 percentage points: below this a difference is not material for the engine
    "same_sign_halves": True,      # first half vs second half of the sample
    "same_sign_indices": True,     # NIFTY and SENSEX (a construction check, not an independent sample)
}


def binomial_z(k: int, n: int, p0: float) -> float:
    if n == 0 or p0 <= 0 or p0 >= 1:
        return 0.0
    return (k / n - p0) / math.sqrt(p0 * (1 - p0) / n)


def two_sample_z(k1: int, n1: int, k2: int, n2: int) -> float:
    """z for p1 - p2 with both proportions estimated (the null population has sampling error too)."""
    if n1 == 0 or n2 == 0:
        return 0.0
    p1, p2 = k1 / n1, k2 / n2
    pooled = (k1 + k2) / (n1 + n2)
    se = math.sqrt(pooled * (1 - pooled) * (1 / n1 + 1 / n2))
    return (p1 - p2) / se if se else 0.0


class ExtensionTable:
    """Survival function of (max - open) / range for a Brownian bar; by symmetry also (open - min) / range.

    P(next bar reaches a level d above its open | its range is R) = S(d / R) under noise.
    """

    def __init__(self, n_bars: int = 100_000, steps: int = 100, seed: int = 20261003):
        rnd = random.Random(seed)
        vals = []
        for _ in range(n_bars):
            x = hi = lo = 0.0
            for _ in range(steps):
                x += rnd.gauss(0, 1)
                hi = max(hi, x); lo = min(lo, x)
            r = hi - lo
            vals.append(hi / r if r else 0.0)
        self._sorted = sorted(vals)
        self.n = len(vals)

    def p_exceed(self, ratio: float) -> float:
        """P((max - open)/range > ratio); at ratio 0 this is 1 - P(max == open)."""
        if ratio < 0:
            return 1.0
        if ratio >= 1:
            return 0.0
        return 1.0 - bisect.bisect_right(self._sorted, ratio) / self.n

    def p_zero(self) -> float:
        """P(max == open): the path never rose above the open (13 % for 20 steps, 6 % for 100)."""
        return bisect.bisect_right(self._sorted, 0.0) / self.n


class EmpiricalExtensionTable:
    """Survival functions of (high - open)/range and (open - low)/range taken from a population's own
    completed bars (first bars and static-window bars excluded), stratified by range decile because a
    bar's path shape depends on its range (large-range bars are more often one-directional, so their
    open sits near an extreme). Assumes only that a bar's internal path shape is independent of the
    previous bar's label given its range; matches the feed's print density by construction.

    p_exceed(0) is 1 - P(ratio == 0): a level exactly at the open is "exceeded" only if the path moves
    strictly beyond it (lesson 4, 2026-10-03: returning 1.0 for distance zero biased every retained-bar
    cell by ~6 points on a pure random walk)."""

    def __init__(self, items: Sequence[tuple[Candle, AnatomyRecord]], n_strata: int = 10):
        rows = []
        for c, r in items:
            if r.first_bar or r.static_window:
                continue
            rng = c.high - c.low
            if rng <= 0:
                continue
            rows.append((rng, (c.high - c.open) / rng, (c.open - c.low) / rng))
        rows.sort(key=lambda t: t[0])
        self.n = len(rows)
        self.n_strata = max(1, min(n_strata, self.n // 50)) if self.n else 1
        size = max(1, math.ceil(self.n / self.n_strata)) if self.n else 1
        self._edges = []            # upper range bound of each stratum (last is +inf)
        self._up, self._dn = [], []
        for i in range(self.n_strata):
            seg = rows[i * size:(i + 1) * size]
            if not seg:
                break
            self._edges.append(seg[-1][0])
            self._up.append(sorted(t[1] for t in seg))
            self._dn.append(sorted(t[2] for t in seg))
        if self._edges:
            self._edges[-1] = math.inf
        self.kind = f"empirical_same_population_{len(self._edges)}_range_strata"

    def _stratum(self, rng: float) -> int:
        return bisect.bisect_left(self._edges, rng) if self._edges else 0

    def p_exceed(self, ratio: float, up: bool = True, rng: Optional[float] = None) -> float:
        """P(shape ratio > ratio) within the range stratum of ``rng`` (whole population if None)."""
        if ratio < 0:
            return 1.0
        if ratio >= 1:
            return 0.0
        if not self._edges:
            return 0.0
        idx = self._stratum(rng) if rng is not None else None
        arrs = (self._up if up else self._dn)
        arr = arrs[min(idx, len(arrs) - 1)] if idx is not None else sorted(x for a in arrs for x in a)
        return 1.0 - bisect.bisect_right(arr, ratio) / len(arr)


_TABLE: Optional[ExtensionTable] = None


def extension_table() -> ExtensionTable:
    """Brownian table (100 steps) kept for sensitivity analysis only."""
    global _TABLE
    if _TABLE is None:
        _TABLE = ExtensionTable()
    return _TABLE


def extension_null_p(a: Candle, b: Candle, up: bool, table) -> float:
    """Null probability that bar b (open, realised range) trades strictly beyond bar a's extreme."""
    rng = b.high - b.low
    dist = (a.high - b.open) if up else (b.open - a.low)
    if dist < 0:
        return 1.0                      # b opened beyond a's extreme: exceeded at the open
    if rng == 0:
        return 0.0                      # flat bar cannot move strictly beyond anything
    if isinstance(table, EmpiricalExtensionTable):
        return table.p_exceed(dist / rng, up, rng)
    return table.p_exceed(dist / rng) if dist > 0 else 1.0 - table.p_zero()


def random_walk_items(n_sessions: int = 200, bars_per_session: int = 375, steps: int = 20, seed: int = 20261003,
                      interval_minutes: int = 1, p: AnatomyParams = DEFAULT_PARAMS,
                      vol_regimes: Optional[Sequence[float]] = None) -> list[tuple[Candle, AnatomyRecord]]:
    """Brownian sessions passed through replay_anatomy. ``vol_regimes`` (optional) cycles the step
    size per session to plant volatility clustering without any directional structure."""
    rnd = random.Random(seed)
    tz = ZoneInfo("Asia/Kolkata")
    out: list[Candle] = []
    px = 20000.0
    day = datetime(2020, 1, 1, tzinfo=tz)
    k = timedelta(minutes=interval_minutes)
    for s in range(n_sessions):
        sigma = vol_regimes[s % len(vol_regimes)] if vol_regimes else 1.0
        start = (day + timedelta(days=s)).replace(hour=9, minute=15)
        for i in range(bars_per_session):
            hi = lo = px
            x = px
            for _ in range(steps):
                x += rnd.gauss(0, sigma)
                hi = max(hi, x); lo = min(lo, x)
            out.append(Candle(start + i * k, px, hi, lo, x, k))
            px = x
    return [(c, r) for _, c, r in replay_anatomy(out, p)]


def pair_bars(items: Sequence[tuple[Candle, AnatomyRecord]]) -> Iterable[tuple[int, int]]:
    for i in range(len(items) - 1):
        c0, r0 = items[i]
        c1, r1 = items[j := i + 1]
        if r0.first_bar or r0.static_window or r1.static_window:
            continue
        if r1.session_index != r0.session_index + 1 or c1.timestamp.date() != c0.timestamp.date():
            continue
        yield i, j


def _accumulate(items, labels_of, outcomes, table):
    """Per label -> outcome -> per half [k, n, E, V] where E/V are the extension-null expectation/variance
    (filled only for rw_extension outcomes); plus unconditional base hits per half."""
    pairs = list(pair_bars(items))
    n_all = len(pairs)
    half = n_all // 2
    base = {o.key: [0, 0] for o in outcomes}
    cond: dict[str, dict[str, list]] = {}
    for idx, (i, j) in enumerate(pairs):
        c0, r0 = items[i]
        c1, r1 = items[j]
        h = 0 if idx < half else 1
        hits = {o.key: o.fn(c0, c1, r1) for o in outcomes}
        pnull = {o.key: (extension_null_p(c0, c1, o.null_kind == "rw_extension_up", table) if o.null_kind.startswith("rw_extension") else None) for o in outcomes}
        for o in outcomes:
            base[o.key][h] += hits[o.key]
        for lab in labels_of(r0):
            d = cond.setdefault(lab, {o.key: [[0, 0, 0.0, 0.0], [0, 0, 0.0, 0.0]] for o in outcomes})
            for o in outcomes:
                cell = d[o.key][h]
                cell[0] += hits[o.key]; cell[1] += 1
                if pnull[o.key] is not None:
                    cell[2] += pnull[o.key]; cell[3] += pnull[o.key] * (1 - pnull[o.key])
    return n_all, half, base, cond


def conditional_study(items: Sequence[tuple[Candle, AnatomyRecord]], labels_of: Callable[[AnatomyRecord], list[str]],
                      outcomes: Sequence[Outcome] = OUTCOMES,
                      null_items: Optional[Sequence[tuple[Candle, AnatomyRecord]]] = None,
                      table=None) -> dict:
    """Per (label, outcome) cell: n, k, p, p_uncond, diff_uncond, z_uncond, null_kind, p_null, diff, z, halves.
    ``null_items`` (a random-walk population) is required for rw_direction outcomes; without it they fall
    back to the unconditional comparison and say so. ``table`` defaults to the empirical extension table
    built from ``items`` themselves; pass ``extension_table()`` for the Brownian sensitivity variant."""
    table = table or EmpiricalExtensionTable(items)
    n_all, half, base, cond = _accumulate(items, labels_of, outcomes, table)
    null_cond = None
    if null_items is not None:
        _, _, _, null_cond = _accumulate(null_items, labels_of, outcomes, table)
    n_h = [half, n_all - half]
    p_unc = {o.key: (base[o.key][0] + base[o.key][1]) / n_all if n_all else 0.0 for o in outcomes}
    p_unc_h = {o.key: [base[o.key][h] / n_h[h] if n_h[h] else 0.0 for h in (0, 1)] for o in outcomes}
    out = {"population_pairs": n_all, "unconditional": {k: round(v, 4) for k, v in p_unc.items()},
           "unconditional_by_half": {k: [round(x, 4) for x in v] for k, v in p_unc_h.items()}, "labels": {}}
    for lab, d in sorted(cond.items()):
        row = {}
        for o in outcomes:
            (k0, n0, e0, v0), (k1, n1, e1, v1) = d[o.key]
            n, k = n0 + n1, k0 + k1
            p = k / n if n else 0.0
            cell = {"n": n, "k": k, "p": round(p, 4), "p_uncond": round(p_unc[o.key], 4), "diff_uncond": round(p - p_unc[o.key], 4),
                    "z_uncond": round(binomial_z(k, n, p_unc[o.key]), 2), "null_kind": o.null_kind}
            if o.null_kind.startswith("rw_extension") and n:
                e, v = e0 + e1, v0 + v1
                p_null = e / n
                cell.update({"p_null": round(p_null, 4), "diff": round(p - p_null, 4), "z": round((k - e) / math.sqrt(v), 2) if v > 0 else 0.0,
                             "halves": [{"n": n0, "p": round(k0 / n0, 4) if n0 else None, "diff": round((k0 - e0) / n0, 4) if n0 else None},
                                        {"n": n1, "p": round(k1 / n1, 4) if n1 else None, "diff": round((k1 - e1) / n1, 4) if n1 else None}]})
            elif o.null_kind == "rw_direction" and null_cond is not None and lab in null_cond:
                (nk0, nn0, _, _), (nk1, nn1, _, _) = null_cond[lab][o.key]
                nn, nk = nn0 + nn1, nk0 + nk1
                p_null = nk / nn if nn else None
                cell.update({"p_null": round(p_null, 4) if p_null is not None else None, "n_null": nn,
                             "diff": round(p - p_null, 4) if p_null is not None else None, "z": round(two_sample_z(k, n, nk, nn), 2),
                             "halves": [{"n": n0, "p": round(k0 / n0, 4) if n0 else None, "diff": round(k0 / n0 - p_null, 4) if (n0 and p_null is not None) else None},
                                        {"n": n1, "p": round(k1 / n1, 4) if n1 else None, "diff": round(k1 / n1 - p_null, 4) if (n1 and p_null is not None) else None}]})
            else:
                kind = "unconditional" if o.null_kind == "unconditional" else "unconditional_fallback"
                cell.update({"null_kind": kind, "p_null": cell["p_uncond"], "diff": cell["diff_uncond"], "z": cell["z_uncond"],
                             "halves": [{"n": n0, "p": round(k0 / n0, 4) if n0 else None, "diff": round(k0 / n0 - p_unc_h[o.key][0], 4) if n0 else None},
                                        {"n": n1, "p": round(k1 / n1, 4) if n1 else None, "diff": round(k1 / n1 - p_unc_h[o.key][1], 4) if n1 else None}]})
            row[o.key] = cell
        row["share_of_pairs"] = round((d[outcomes[0].key][0][1] + d[outcomes[0].key][1][1]) / n_all, 4) if n_all else 0.0
        out["labels"][lab] = row
    out["family_size"] = len(cond) * len(outcomes)
    out["null"] = {o.key: o.null_kind for o in outcomes}
    out["extension_table"] = getattr(table, "kind", "brownian_100_steps")
    return out


def status_of(cells: Sequence[dict], rule: dict = STATUS_RULE) -> str:
    """Combine the same (label, outcome) cell across indices into one evidence status.

    SUPPORTED_DESCRIPTIVE : every cell |z| >= min_abs_z, |diff| >= min_abs_effect, diffs share a sign,
                            and each cell's two halves share that sign.
    STATISTICAL_ONLY      : every cell |z| >= min_abs_z with a shared sign but some |diff| < min_abs_effect.
    NOT_SUPPORTED         : otherwise (includes sign flips between halves or indices).
    """
    if not cells or any(c.get("diff") is None for c in cells):
        return "UNTESTED"
    signs = {1 if c["diff"] > 0 else -1 if c["diff"] < 0 else 0 for c in cells}
    big_z = all(abs(c["z"]) >= rule["min_abs_z"] for c in cells)
    if 0 in signs or len(signs) > 1 or not big_z:
        return "NOT_SUPPORTED"
    halves_ok = all(h["diff"] is not None and (h["diff"] > 0) == (c["diff"] > 0) for c in cells for h in c["halves"])
    if not halves_ok:
        return "NOT_SUPPORTED"
    material = all(abs(c["diff"]) >= rule["min_abs_effect"] for c in cells)
    return "SUPPORTED_DESCRIPTIVE" if material else "STATISTICAL_ONLY"
