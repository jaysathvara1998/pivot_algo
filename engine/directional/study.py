"""Skill 34 — directional hypotheses D1–D7 (HYPOTHESES.md), computed from the project's own data; nulls per the registration."""
from __future__ import annotations

import math
import random
import statistics as st
from collections import defaultdict
from datetime import date, time

COST_CURRENT = 0.0006; COST_DEV_ERA = 0.0003
ZCRIT_FAMILY = 14


def _mom(xs):
    n = len(xs)
    if n < 2:
        return n, (xs[0] if xs else None), None
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)); return n, m, sd


def cell_stats(rets, null_means, direction, halves, cost, zcrit, min_n=100, label=""):
    """rets: list of (date, signed return in the registered direction, points); null_means: list of null mean returns; halves: fn(date) → 'H1'|'H2'."""
    xs = [r for _, r, _ in rets]; n, m, sd = _mom(xs)
    nm = sum(null_means) / len(null_means) if null_means else 0.0; nsd = st.pstdev(null_means) if len(null_means) > 1 else None
    z = ((m - nm) / nsd) if (m is not None and nsd) else None; t = (m / (sd / math.sqrt(n))) if n > 1 and sd else None
    hv = {}
    for h in ("H1", "H2"):
        ys = [r for d, r, _ in rets if halves(d) == h]; hv[h] = {"n": len(ys), "mean_pct": round(100 * sum(ys) / len(ys), 4) if ys else None}
    same = all(v["mean_pct"] is not None and (v["mean_pct"] > 0) == (m > 0) for v in hv.values()) if m is not None else False
    hit = sum(1 for x in xs if x > 0) / n if n else None
    if n < min_n or z is None:
        status = "UNTESTED"
    elif z >= zcrit and same and m > 0:
        status = "SUPPORTED_DESCRIPTIVE"
    elif z >= zcrit:
        status = "STATISTICAL_ONLY"
    elif z <= -zcrit:
        status = "CONTRADICTED"
    else:
        status = "NOT_SUPPORTED"
    pts = [p for _, _, p in rets if p is not None]
    out = {"label": label, "n": n, "mean_pct": round(100 * m, 4) if m is not None else None, "sd_pct": round(100 * sd, 4) if sd else None, "t": round(t, 2) if t is not None else None,
           "hit_rate": round(hit, 4) if hit is not None else None, "null_mean_pct": round(100 * nm, 4), "null_sd_pct": round(100 * nsd, 4) if nsd else None, "z_vs_null": round(z, 2) if z is not None else None,
           "halves": hv, "same_sign_halves": same, "status": status, "mean_points": round(sum(pts) / len(pts), 2) if pts else None,
           "cost_pct": round(100 * cost, 3) if cost is not None else None, "net_pct": round(100 * (m - cost), 4) if (m is not None and cost is not None) else None}
    if cost is not None and status == "SUPPORTED_DESCRIPTIVE":
        out["cost_gate"] = "TRADEABLE_GROSS" if m > cost else "NOT_AFTER_COST"
    return out


# ---------------------------------------------------------------- daily index: D1a, D3
def block_shuffle_daily(opens, closes, rnd, block=20):
    """Shuffle 20-day blocks of (close/prev_close, open/close) and rebuild; keeps mean and volatility, destroys sequence and drift timing."""
    days = [(closes[i] / closes[i - 1], opens[i] / closes[i]) for i in range(1, len(closes)) if closes[i - 1] and closes[i] and opens[i]]
    blocks = [days[k:k + block] for k in range(0, len(days), block)]; rnd.shuffle(blocks)
    C = [closes[0]]; O = [opens[0]]
    for r, oc in (d for b in blocks for d in b):
        C.append(C[-1] * r); O.append(C[-1] * oc)
    return O, C


def overnight_returns(dates, opens, closes):
    """D1a: (date, close_{t-1} → open_t return, points)."""
    return [(dates[i], opens[i] / closes[i - 1] - 1, opens[i] - closes[i - 1]) for i in range(1, len(dates)) if closes[i - 1] and opens[i]]


def tsmom_trades(dates, opens, closes, lookback_months: int):
    """D3: at each month-end t, sign of close_t / close_{t-21·L} − 1; long → open_{t+1} → open_{t+22}; flat when negative (short leg reported separately)."""
    L = 21 * lookback_months; out = []; short = []
    month_ends = [i for i in range(1, len(dates) - 23) if dates[i].month != dates[i + 1].month]
    for i in month_ends:
        if i - L < 0 or not closes[i - L] or not opens[i + 1] or not opens[i + 22]:
            continue
        sig = closes[i] / closes[i - L] - 1; r = opens[i + 22] / opens[i + 1] - 1
        if sig > 0:
            out.append((dates[i], r, opens[i + 22] - opens[i + 1]))
        else:
            short.append((dates[i], -r, -(opens[i + 22] - opens[i + 1])))
    return out, short


# ---------------------------------------------------------------- intraday index: D1b, D2, D4, D6 (1m candles)
def _by_session(candles):
    out = defaultdict(list)
    for c in candles:
        out[c.timestamp.date()].append(c)
    return out


def _at(sess, hh, mm):
    for c in sess:
        if c.timestamp.hour == hh and c.timestamp.minute == mm:
            return c
    return None


def executable_overnight(candles):
    """D1b: buy the 15:20 close, sell the next session's 09:20 close. D2: short 09:20 → 15:20 the same day. Returns (d1b, d2) lists of (date, return, points)."""
    bs = _by_session(candles); days = sorted(bs); d1b = []; d2 = []
    for a, b in zip(days, days[1:]):
        e = _at(bs[a], 15, 20); x = _at(bs[b], 9, 20)
        if e and x:
            d1b.append((b, x.close / e.close - 1, x.close - e.close))
    for d in days:
        o = _at(bs[d], 9, 20); c = _at(bs[d], 15, 20)
        if o and c:
            d2.append((d, -(c.close / o.close - 1), -(c.close - o.close)))
    return d1b, d2


def intraday_momentum(candles):
    """D4: sign(09:45 close / 09:15 open − 1) → trade 14:59 close → 15:29 close in that direction."""
    bs = _by_session(candles); out = []
    for d, sess in bs.items():
        o = sess[0]; a = _at(sess, 9, 44); e = _at(sess, 14, 59); x = _at(sess, 15, 29)
        if not (a and e and x) or o.timestamp.hour != 9 or o.timestamp.minute != 15:
            continue
        s = 1 if a.close > o.open else -1 if a.close < o.open else 0
        if s:
            out.append((d, s * (x.close / e.close - 1), s * (x.close - e.close)))
    return out


def orb_rule(candles):
    """D6: 09:15–09:44 range; first 1m close outside → enter at that close in the break direction; stop at the other side (exit at the first close beyond it); else exit 15:29 close."""
    bs = _by_session(candles); out = []
    for d, sess in bs.items():
        first = [c for c in sess if c.timestamp.hour == 9 and c.timestamp.minute < 45]
        if len(first) < 25:
            continue
        hi = max(c.high for c in first); lo = min(c.low for c in first); rest = [c for c in sess if not (c.timestamp.hour == 9 and c.timestamp.minute < 45)]
        for k, c in enumerate(rest):
            if c.close > hi or c.close < lo:
                s = 1 if c.close > hi else -1; e = c.close; stop = lo if s > 0 else hi; xp = rest[-1].close
                for c2 in rest[k + 1:]:
                    if (c2.close < stop) if s > 0 else (c2.close > stop):
                        xp = c2.close; break
                out.append((d, s * (xp / e - 1), s * (xp - e))); break
    return out


def rw_sessions_1m(n_series, seed, n_sessions=10):
    """Random-walk 1m sessions (Level 3 construction) as candle lists, for the intraday nulls. n_sessions should match the real sample so null means are on the same scale."""
    from engine.behaviour import random_walk_items
    return [[c for c, r in random_walk_items(n_sessions=n_sessions, bars_per_session=375, steps=20, seed=seed + s, interval_minutes=1)] for s in range(n_series)]


def overnight_swap_null(dates, opens, closes, rnd, n_perm=200):
    """Null for D1a: within each day, randomly swap the overnight and intraday log-returns (H0: the two components have the same distribution).
    Returns the null distribution of (mean overnight − mean intraday)."""
    on = []; intra = []
    for i in range(1, len(closes)):
        if closes[i - 1] and opens[i] and closes[i]:
            on.append(math.log(opens[i] / closes[i - 1])); intra.append(math.log(closes[i] / opens[i]))
    out = []
    for _ in range(n_perm):
        d = 0.0
        for a, b in zip(on, intra):
            if rnd.random() < 0.5:
                a, b = b, a
            d += a - b
        out.append(d / len(on))
    return out, sum(a - b for a, b in zip(on, intra)) / len(on)


# ---------------------------------------------------------------- OI build-up: D5 (futures daily + index opens)
def oi_classes(fut_rows, index_open_by_date):
    """fut_rows: [(date, close, oi)] sorted. Class from (Δclose, ΔOI) on day t; outcome = index open_{t+1} → open_{t+2} in the class's registered direction."""
    dates = [d for d, _, _ in fut_rows]; idx_days = sorted(index_open_by_date)
    nxt = {idx_days[i]: (idx_days[i + 1], idx_days[i + 2]) for i in range(len(idx_days) - 2)}
    out = defaultdict(list); labels = []
    for i in range(1, len(fut_rows)):
        d, c, oi = fut_rows[i]; _, c0, oi0 = fut_rows[i - 1]
        if not (c and c0 and oi and oi0) or d not in nxt:
            continue
        dp = c > c0; op = oi > oi0
        cls = "LONG_BUILDUP" if dp and op else "SHORT_BUILDUP" if (not dp and op) else "SHORT_COVERING" if (dp and not op) else "LONG_UNWINDING"
        direction = 1 if cls in ("LONG_BUILDUP", "SHORT_COVERING") else -1
        a, b = nxt[d]; r = index_open_by_date[b] / index_open_by_date[a] - 1
        out[cls].append((d, direction * r, direction * (index_open_by_date[b] - index_open_by_date[a]))); labels.append((cls, r, index_open_by_date[b] - index_open_by_date[a], d))
    return out, labels


def oi_permutation_null(labels, rnd, n_perm=200):
    """Permute class labels across days; mean signed return per class under the null."""
    classes = [l[0] for l in labels]; nulls = defaultdict(list)
    for _ in range(n_perm):
        perm = classes[:]; rnd.shuffle(perm); acc = defaultdict(list)
        for cls, (c0, r, p, d) in zip(perm, labels):
            direction = 1 if cls in ("LONG_BUILDUP", "SHORT_COVERING") else -1; acc[cls].append(direction * r)
        for cls, xs in acc.items():
            nulls[cls].append(sum(xs) / len(xs))
    return nulls
