"""Drift and edge tests between a trailing window and the frozen development reference (PROJECT DEFINITION, Skill 28)."""
from __future__ import annotations

import math
import statistics as st

from .facts import FACTS

Z_WATCH, Z_DRIFT = 2.0, 3.0
BAND = 0.15


def _sum_rates(per_session: dict, dates) -> tuple:
    k = n = 0
    for d in dates:
        if d in per_session:
            kk, nn = per_session[d]; k += kk; n += nn
    return k, n


def _two_prop_z(k1, n1, k2, n2):
    if n1 == 0 or n2 == 0:
        return None
    p = (k1 + k2) / (n1 + n2); se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (k1 / n1 - k2 / n2) / se if se else None


def _one_prop_z(k, n, p0):
    if n == 0 or p0 is None or p0 <= 0 or p0 >= 1:
        return None
    return (k / n - p0) / math.sqrt(p0 * (1 - p0) / n)


def summarise(fact, stats: dict, dates, base: dict | None = None) -> dict:
    """Window summary of one fact over `dates`."""
    if fact.kind == "rate":
        k, n = _sum_rates(stats, dates); rate = k / n if n else None
        null = fact.null_value
        if base is not None:
            kb, nb = _sum_rates(base, dates); null = kb / nb if nb else None
        return {"k": k, "n": n, "rate": round(rate, 4) if rate is not None else None, "null": round(null, 4) if null is not None else None}
    if fact.kind == "continuous":
        xs = [v for d in dates for v in stats.get(d, [])]
        return {"n": len(xs), "median": round(st.median(xs), 4) if xs else None}
    # contrast
    ex = [v for d in dates if d in stats and stats[d]["expiry"] for v in stats[d]["ratios"]]
    ne = [v for d in dates if d in stats and not stats[d]["expiry"] for v in stats[d]["ratios"]]
    n_ex_days = sum(1 for d in dates if d in stats and stats[d]["expiry"])
    def mom(xs):
        return (len(xs), (sum(xs) / len(xs)) if xs else None, (st.pstdev(xs) if len(xs) > 1 else None))
    n1, m1, s1 = mom(ex); n2, m2, s2 = mom(ne)
    z = ((m1 - m2) / math.sqrt(s1 * s1 / n1 + s2 * s2 / n2)) if n1 > 1 and n2 > 1 and s1 and s2 else None
    return {"n": n_ex_days, "expiry_windows": n1, "expiry_mean_ratio": round(m1, 4) if m1 is not None else None, "baseline_mean_ratio": round(m2, 4) if m2 is not None else None,
            "ratio_of_means": round(m1 / m2, 4) if m1 is not None and m2 else None, "z_expiry_vs_baseline": round(z, 2) if z is not None else None}


def judge(fact, window: dict, reference: dict) -> dict:
    """Status vs the development reference (HOLDS / WATCH / DRIFTED / INSUFFICIENT) and edge vs null (EDGE_PRESENT / EDGE_ABSENT / NA)."""
    if window.get("n", 0) < fact.min_n:
        return {"status": "INSUFFICIENT", "edge": "NA", "z_vs_reference": None, "z_vs_null": None}
    if fact.kind == "rate":
        z_ref = _two_prop_z(window["k"], window["n"], reference["k"], reference["n"])
        z_null = _one_prop_z(window["k"], window["n"], window["null"])
        edge = "NA" if z_null is None else ("EDGE_PRESENT" if (z_null >= 2.0 if fact.direction == "ABOVE_NULL" else z_null <= -2.0) else "EDGE_ABSENT")
    elif fact.kind == "continuous":
        dev = window["median"] - reference["median"]; z_ref = dev / BAND * 3.0          # band scaled so that leaving ± BAND is DRIFTED
        edge = "NA"; z_null = None
    else:
        z_ref = None
        if window.get("ratio_of_means") is not None and reference.get("ratio_of_means"):
            z_ref = (window["ratio_of_means"] - reference["ratio_of_means"]) / 0.05 * 3.0 / 3.0 * 1.0   # 0.05 ratio units ≈ one WATCH step
            z_ref = (window["ratio_of_means"] - reference["ratio_of_means"]) / 0.05
        z_null = window.get("z_expiry_vs_baseline")
        edge = "NA" if z_null is None else ("EDGE_PRESENT" if z_null <= -2.0 else "EDGE_ABSENT")
    status = "HOLDS" if z_ref is None or abs(z_ref) < Z_WATCH else "WATCH" if abs(z_ref) < Z_DRIFT else "DRIFTED"
    return {"status": status, "edge": edge, "z_vs_reference": round(z_ref, 2) if z_ref is not None else None, "z_vs_null": round(z_null, 2) if z_null is not None else None}
