"""Point-in-time universe and signals (Skill 31, HYPOTHESES.md)."""
from __future__ import annotations

import math
import statistics as st

MIN_HISTORY = 250; MIN_PRICE = 10.0; MIN_TRADED_VALUE = 1e7; TOP_N = 500


def _last(arr, t, k):
    """Values arr[t-k+1..t] that are not None."""
    return [x for x in arr[max(0, t - k + 1):t + 1] if x is not None]


def eligible(s, t: int) -> bool:
    if t < MIN_HISTORY or s.c[t] is None or s.c[t] < MIN_PRICE:
        return False
    hist = sum(1 for x in s.c[t - MIN_HISTORY + 1:t + 1] if x is not None)
    if hist < MIN_HISTORY * 0.9:
        return False
    return True


def traded_value(s, t: int) -> float | None:
    tv = [c * v for c, v in zip(s.c[max(0, t - 59):t + 1], s.v[max(0, t - 59):t + 1]) if c is not None and v is not None]
    return st.median(tv) if len(tv) >= 40 else None


def universe_at(panel, t: int) -> list:
    cand = []
    for s in panel:
        if eligible(s, t):
            tv = traded_value(s, t)
            if tv is not None and tv >= MIN_TRADED_VALUE:
                cand.append((tv, s))
    cand.sort(key=lambda x: -x[0])
    return [s for _, s in cand[:TOP_N]]


def _ret(arr, a, b):
    return (arr[b] / arr[a] - 1) if arr[a] and arr[b] else None


def signals_at(s, t: int) -> dict:
    out = {}
    out["MOM_12_1"] = _ret(s.c, t - 252, t - 21) if t >= 252 else None
    out["STR_1M"] = _ret(s.c, t - 21, t)
    out["STR_1W"] = _ret(s.c, t - 5, t)
    hi = _last(s.h, t, 252); out["HI52"] = (s.c[t] / max(hi)) if hi and s.c[t] else None
    cl = [x for x in s.c[t - 60:t + 1]]
    rets = [cl[i] / cl[i - 1] - 1 for i in range(1, len(cl)) if cl[i] and cl[i - 1]]
    out["LOWVOL"] = -st.pstdev(rets) if len(rets) >= 40 else None
    v5 = _last(s.v, t, 5); v60 = _last(s.v, t, 60)
    out["VOLSHOCK"] = (sum(v5) / len(v5)) / (sum(v60) / len(v60)) if len(v5) == 5 and len(v60) >= 40 and sum(v60) > 0 else None
    return out


SIGNALS = ("MOM_12_1", "STR_1M", "STR_1W", "HI52", "LOWVOL", "VOLSHOCK")
HORIZON = {"MOM_12_1": 21, "STR_1M": 21, "STR_1W": 5, "HI52": 21, "LOWVOL": 21, "VOLSHOCK": 5}
EXPECTED = {"MOM_12_1": +1, "STR_1M": -1, "STR_1W": -1, "HI52": +1, "LOWVOL": +1, "VOLSHOCK": 0}
