"""Session-level context study: gap fill, first-hour and day direction, session range by gap class — real vs a volatility-matched
random walk that starts at the real open (so it carries the real gap) and has the real session's per-bar volatility."""
from __future__ import annotations

import math
import random
from collections import defaultdict

from engine.behaviour.study import two_sample_z
from .engine import replay_context
from .params import ContextParams, DEFAULT_CTX_PARAMS, GAP_CLASSES


def session_table(items, p: ContextParams = DEFAULT_CTX_PARAMS) -> list:
    """One row per session with a previous session: gap fields, fill, first-hour and day direction, range multiple, per-bar sigma."""
    rows = []; cur = None; closes = []
    for i, c, r in replay_context(items, p):
        if r.session_index == 0:
            if cur is not None:
                cur["day_up"] = cur["last_close"] > cur["open"]; cur["bars"] = len(closes)
                rets = [math.log(closes[k] / closes[k - 1]) for k in range(1, len(closes))]
                m = sum(rets) / len(rets) if rets else 0.0
                cur["sigma_bar"] = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
                rows.append(cur)
            closes = []
            cur = None if r.gap_class is None else {"date": r.date, "regime": r.regime, "gap_pct": r.gap_pct, "gap_ref": r.gap_ref, "gap_class": r.gap_class,
                                                      "gap_sign": r.gap_sign, "open_vs_prev_range": r.open_vs_prev_range, "prev_close": r.prev_close,
                                                      "prev_range_pct": r.prev_range_pct, "prev_close_location": r.prev_close_location, "open": c.open,
                                                      "expiry": r.expiry_flags, "events": [e["kind"] for e in r.events]}
        if cur is not None:
            closes.append(c.close)
            cur["filled"] = r.gap_filled; cur["fill_bar"] = r.gap_fill_bar; cur["fh"] = r.first_hour_return_pct
            cur["range_pct"] = r.session_range_pct; cur["range_over_prev"] = r.session_range_over_prev; cur["last_close"] = c.close
    if cur is not None:
        cur["day_up"] = cur["last_close"] > cur["open"]; cur["bars"] = len(closes)
        rets = [math.log(closes[k] / closes[k - 1]) for k in range(1, len(closes))]
        m = sum(rets) / len(rets) if rets else 0.0
        cur["sigma_bar"] = math.sqrt(sum((x - m) ** 2 for x in rets) / (len(rets) - 1)) if len(rets) > 1 else 0.0
        rows.append(cur)
    return rows


def gap_null_table(rows, p: ContextParams = DEFAULT_CTX_PARAMS, seed: int = 20261015, first_hour_bars: int = 60) -> list:
    """For each real session, simulate paths from the real open with the real per-bar sigma and bar count; record fill, first-hour
    and day direction, range multiple. The gap is inherited from the real session, so the null is gap-aware and volatility-matched."""
    rnd = random.Random(seed); out = []
    for s in rows:
        for _ in range(p.null_paths_per_session):
            px = s["open"]; hi = lo = px; filled = px >= s["prev_close"] >= px; fill_bar = 0 if filled else None; fh = None
            for b in range(1, s["bars"]):
                nx = px * math.exp(rnd.gauss(0, s["sigma_bar"]))
                h, l = max(px, nx), min(px, nx)
                hi, lo = max(hi, h), min(lo, l)
                if not filled and l <= s["prev_close"] <= h:
                    filled = True; fill_bar = b
                if b == first_hour_bars - 1:
                    fh = (nx - s["open"]) / s["open"] * 100
                px = nx
            out.append({"gap_class": s["gap_class"], "gap_sign": s["gap_sign"], "open_vs_prev_range": s["open_vs_prev_range"], "filled": filled, "fill_bar": fill_bar,
                        "fh": fh, "day_up": px > s["open"], "range_over_prev": (hi - lo) / s["open"] * 100 / s["prev_range_pct"] if s["prev_range_pct"] else None, "regime": s["regime"]})
    return out


def _agg(rows, key_fn):
    g = defaultdict(lambda: {"n": 0, "filled": 0, "fh_fade": 0, "fh_n": 0, "day_fade": 0, "fill_bars": [], "ranges": []})
    for s in rows:
        k = key_fn(s)
        if k is None:
            continue
        a = g[k]; a["n"] += 1; a["filled"] += bool(s["filled"])
        if s["filled"] and s["fill_bar"] is not None:
            a["fill_bars"].append(s["fill_bar"])
        if s.get("range_over_prev") is not None:
            a["ranges"].append(s["range_over_prev"])
        if s["gap_sign"] in ("UP", "DOWN") and s.get("fh") is not None:
            a["fh_n"] += 1; a["fh_fade"] += (s["fh"] < 0) if s["gap_sign"] == "UP" else (s["fh"] > 0)
        if s["gap_sign"] in ("UP", "DOWN"):
            a["day_fade"] += (not s["day_up"]) if s["gap_sign"] == "UP" else s["day_up"]
    med = lambda xs: sorted(xs)[len(xs) // 2] if xs else None
    return {k: {"n": a["n"], "p_fill": round(a["filled"] / a["n"], 4), "median_fill_bar": med(a["fill_bars"]), "p_first_hour_fades_gap": round(a["fh_fade"] / a["fh_n"], 4) if a["fh_n"] else None,
                "p_day_fades_gap": round(a["day_fade"] / a["n"], 4) if a["n"] and k != "ALL" else None, "median_range_over_prev": round(med(a["ranges"]), 3) if a["ranges"] else None}
            for k, a in g.items()}


def context_study(items, p: ContextParams = DEFAULT_CTX_PARAMS) -> dict:
    rows = session_table(items, p)
    null = gap_null_table(rows, p)
    keys = {"ALL": lambda s: "ALL", "gap_class": lambda s: s["gap_class"], "gap_class_sign": lambda s: f"{s['gap_class']}_{s['gap_sign']}" if s["gap_sign"] != "NONE" else None,
            "open_vs_prev_range": lambda s: s["open_vs_prev_range"], "regime": lambda s: s["regime"]}
    res = {"sessions": len(rows), "null_paths": len(null), "real": {}, "null": {}, "z_fill": {}, "z_first_hour_fade": {}}
    for name, fn in keys.items():
        res["real"][name] = _agg(rows, fn); res["null"][name] = _agg(null, fn)
        for k, a in res["real"][name].items():
            b = res["null"][name].get(k)
            if not b:
                continue
            res["z_fill"][f"{name}:{k}"] = round(two_sample_z(round(a["p_fill"] * a["n"]), a["n"], round(b["p_fill"] * b["n"]), b["n"]), 2)
            if a["p_first_hour_fades_gap"] is not None and b["p_first_hour_fades_gap"] is not None:
                na = sum(1 for s in rows if fn(s) == k and s["gap_sign"] != "NONE" and s.get("fh") is not None)
                nb = sum(1 for s in null if fn(s) == k and s["gap_sign"] != "NONE" and s.get("fh") is not None)
                res["z_first_hour_fade"][f"{name}:{k}"] = round(two_sample_z(round(a["p_first_hour_fades_gap"] * na), na, round(b["p_first_hour_fades_gap"] * nb), nb), 2)
    # gap distribution
    gaps = sorted(abs(s["gap_pct"]) for s in rows)
    res["gap_abs_pct_quantiles"] = {q: round(gaps[int(q * (len(gaps) - 1))], 3) for q in (0.25, 0.5, 0.75, 0.9)} if gaps else {}
    res["gap_class_shares"] = {k: round(sum(1 for s in rows if s["gap_class"] == k) / len(rows), 4) for k in GAP_CLASSES} if rows else {}
    res["gap_up_share"] = round(sum(1 for s in rows if s["gap_sign"] == "UP") / len(rows), 4) if rows else None
    return res
