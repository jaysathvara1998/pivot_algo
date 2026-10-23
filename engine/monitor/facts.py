"""Monitored facts (PROJECT DEFINITION, Skill 28): each fact computes a per-session statistic from the engines, so the same code serves the frozen
development reference, trailing-window drift tests and the forward ledger. Rate facts return {date: (k, n)}; continuous facts return {date: [values]}."""
from __future__ import annotations

import statistics as st
from dataclasses import dataclass
from datetime import date

from engine.anatomy import replay_anatomy
from engine.behaviour import interpret
from engine.context import ContextEngine
from engine.daily import DayBar, label_bars
from engine.ohlc.aggregate import aggregate
from engine.options.bs import implied_range
from engine.session.labels import expiry_flags
from engine.setups import build_stream


@dataclass(frozen=True)
class Fact:
    name: str
    card: str
    kind: str              # rate | continuous | contrast
    null_value: float | None      # fixed null from the development studies (rate facts), or None when computed from the window
    direction: str         # ABOVE_NULL | BELOW_NULL | BAND
    min_n: int
    description: str


FACTS = (
    Fact("F01_streak_reversal_5m", "04, 14, 16, 19", "rate", 0.495, "ABOVE_NULL", 60, "after 5+ same-direction 5m closes the next 5m bar closes against the streak (null: random walk 0.495)"),
    Fact("F02_expansion_then_large_1m", "03, 14, 16", "rate", None, "ABOVE_NULL", 150, "a RANGE_EXPANSION 1m bar is followed by a LARGE bar (null: the window's own LARGE base rate, reported alongside)"),
    Fact("F03_gap_small_up_fill", "08, 15, 16, 20", "rate", 0.665, "ABOVE_NULL", 20, "a SMALL UP gap is filled within the session (null: gap-aware random walk 0.665)"),
    Fact("F04_relative_range_median", "02", "continuous", 1.0, "BAND", 2000, "median deseasonalised 1m relative range (reference 1.0 by construction; band ± 0.15)"),
    Fact("F05_expiry_ratio_vs_baseline", "26", "contrast", None, "BELOW_NULL", 8, "realised/implied 15-minute range ratio on expiry days vs non-expiry days (Level 26: expiry 0.45 vs 0.53)"),
    Fact("F06_daily_expansion_then_large", "27", "rate", None, "ABOVE_NULL", 8, "a daily RANGE_EXPANSION day is followed by a LARGE day (null: the window's daily LARGE base rate)"),
    Fact("F07_aligned_sweep_engulf_5m", "33", "rate", 0.50, "ABOVE_NULL", 60, "NIFTY/SENSEX 5m: sweep of the 20-bar low (P2) or bullish engulfing at support (P11) when the 15m structure is UP → higher close 5 bars later (Skill 33 lead; null 0.50)"),
    Fact("F08_overnight_hold", "34", "rate", 0.50, "ABOVE_NULL", 40, "share of sessions whose 09:20 print exceeds the previous session's 15:20 print (the executable overnight drift; null 0.50; Skill 34)"),
)


def build_bundle(candles, index: str, options: dict | None = None) -> dict:
    """Engines run once over the candles (which must include ≥ 25 warm-up sessions before the sessions of interest)."""
    items1 = [(c, r) for _, c, r in replay_anatomy(candles)]
    stream5 = build_stream([(c, r) for _, c, r in replay_anatomy(list(aggregate(candles, 5)))], 5, with_context=False)
    return {"index": index, "items1": items1, "stream5": stream5, "options": options or {}}


def f01(b):
    out = {}; s = b["stream5"]
    for i, row in enumerate(s[:-1]):
        nxt = s[i + 1]
        if nxt["session_index"] != row["session_index"] + 1:
            continue
        labs = set(row["labels"]); d = row["candle"].timestamp.date()
        if "RUN_DOWN_5+" in labs:
            k, n = out.get(d, (0, 0)); out[d] = (k + (nxt["candle"].close > nxt["candle"].open), n + 1)
        elif "RUN_UP_5+" in labs:
            k, n = out.get(d, (0, 0)); out[d] = (k + (nxt["candle"].close < nxt["candle"].open), n + 1)
    return out


def f02(b):
    out = {}; base = {}; items = b["items1"]; reg = {}
    for i in range(len(items) - 1):
        c, r = items[i]; c2, r2 = items[i + 1]
        if r2.session_index != r.session_index + 1 or r.first_bar:
            continue
        d = c.timestamp.date()
        large = r2.size_class in ("LARGE", "EXTREME")
        kb, nb = base.get(d, (0, 0)); base[d] = (kb + large, nb + 1)
        if any(it.label == "RANGE_EXPANSION" for it in interpret(r, register=reg)):
            k, n = out.get(d, (0, 0)); out[d] = (k + large, n + 1)
    return out, base


def f03(b):
    ctx = ContextEngine(); out = {}; cur = None
    for c, r in b["items1"]:
        cr = ctx.process(c, r)
        d = c.timestamp.date()
        if cr.session_index == 0:
            cur = (d, cr.gap_class == "SMALL" and cr.gap_sign == "UP")
        if cur and cur[1]:
            out[d] = (1 if cr.gap_filled else 0, 1)
    return out


def f04(b):
    out = {}
    for c, r in b["items1"]:
        if r.relative_range is not None and not r.first_bar and not r.static_window:
            out.setdefault(c.timestamp.date(), []).append(r.relative_range)
    return out


def f05(b, h=15):
    """Per session: list of ratios (realised/implied over 15 bars) and the expiry flag; contrast = expiry sessions vs non-expiry sessions."""
    items = b["items1"]; opts = b["options"]; out = {}
    idx = b["index"].lower()
    for i in range(0, len(items) - h, h + 1):
        c, r = items[i]; d = c.timestamp.date()
        if items[i + h][1].session_index != r.session_index + h:
            continue
        o = opts.get(c.timestamp.strftime("%Y-%m-%d %H:%M"))
        if not o or o["iv"] <= 0:
            continue
        hi = max(x[0].high for x in items[i + 1:i + h + 1]); lo = min(x[0].low for x in items[i + 1:i + h + 1])
        ir = implied_range(o["spot"], o["iv"], h)
        if ir > 0:
            out.setdefault(d, {"expiry": bool(expiry_flags(d).get(f"{idx}_weekly_expiry")), "ratios": []})["ratios"].append((hi - lo) / ir)
    return out


def daily_from_1m(items) -> list:
    days = {}
    for c, r in items:
        d = c.timestamp.date(); x = days.get(d)
        if x is None:
            days[d] = DayBar(d, c.open, c.high, c.low, c.close)
        else:
            x.high = max(x.high, c.high); x.low = min(x.low, c.low); x.close = c.close
    return label_bars([days[k] for k in sorted(days)])


def f06(b):
    bars = daily_from_1m(b["items1"]); out = {}; base = {}
    for i in range(len(bars) - 1):
        if bars[i].ref is None or bars[i + 1].rel_range is None:
            continue
        large = bars[i + 1].rel_range >= 2.0; d = bars[i].d
        base[d] = (int(large), 1)
        if "RANGE_EXPANSION" in bars[i].labels:
            out[d] = (int(large), 1)
    return out, base


def f07(b):
    """Per session: (hits, n) of the Skill 33 lead on 5m bars with 15m context, both built from the bundle's 1m candles."""
    from datetime import timedelta
    from engine.pa2 import Bars, detect_all
    cands = [c for c, r in b["items1"]]
    c5 = list(aggregate(cands, 5)); c15 = list(aggregate(cands, 15))
    def arrays(cs):
        sess = []; cur = None; k = -1
        for c in cs:
            if c.timestamp.date() != cur:
                cur = c.timestamp.date(); k += 1
            sess.append(k)
        return [c.open for c in cs], [c.high for c in cs], [c.low for c in cs], [c.close for c in cs], sess
    o, h, l, c_, sess = arrays(c5); b5 = Bars(o, h, l, c_, session=sess); sigs, _ = detect_all(b5)
    b15 = Bars(*arrays(c15)[:4]); ends = [(c.timestamp + timedelta(minutes=15), b15.state[i]) for i, c in enumerate(c15)]
    hs = []; j = 0; cur = "UNDEFINED"
    for c in c5:
        while j < len(ends) and ends[j][0] <= c.timestamp:
            cur = ends[j][1]; j += 1
        hs.append(cur)
    out = {}; last = -10 ** 9
    for s in sorted(sigs, key=lambda x: x.i):
        if s.setup not in ("P2_SWEEP_LOW", "P11_ENGULF_AT_SUPPORT") or hs[s.i] != "UP" or s.i <= last + 4 or s.i + 5 >= len(c5) or sess[s.i + 5] != sess[s.i]:
            continue
        d = c5[s.i].timestamp.date(); k, n = out.get(d, (0, 0)); out[d] = (k + (c_[s.i + 5] > c_[s.i]), n + 1); last = s.i
    return out


def f08(b):
    """Per session: (1 if 09:20 close > previous session's 15:20 close else 0, 1)."""
    from collections import defaultdict as _dd
    by = _dd(dict)
    for c, r in b["items1"]:
        hm = (c.timestamp.hour, c.timestamp.minute)
        if hm in ((15, 20), (9, 20)):
            by[c.timestamp.date()][hm] = c.close
    days = sorted(by); out = {}
    for a, d in zip(days, days[1:]):
        if (15, 20) in by[a] and (9, 20) in by[d]:
            out[d] = (int(by[d][(9, 20)] > by[a][(15, 20)]), 1)
    return out


COMPUTE = {"F01_streak_reversal_5m": f01, "F02_expansion_then_large_1m": f02, "F03_gap_small_up_fill": f03, "F04_relative_range_median": f04, "F05_expiry_ratio_vs_baseline": f05, "F06_daily_expansion_then_large": f06, "F07_aligned_sweep_engulf_5m": f07, "F08_overnight_hold": f08}


def compute_all(bundle: dict) -> dict:
    """{fact name: per-session statistics}; for F02/F06 also '<name>__base' with the per-session base-rate counts."""
    out = {}
    for f in FACTS:
        res = COMPUTE[f.name](bundle)
        if isinstance(res, tuple):
            out[f.name], out[f.name + "__base"] = res
        else:
            out[f.name] = res
    return out
