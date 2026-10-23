#!/usr/bin/env python3
"""Session mood labels for the brain portfolio (stdlib). For a date, from 1m NIFTY bars (store or FEED snapshot) and the previous session:
  gap_pct, first30_range, first30_move, day_range, day_type (TREND_UP / TREND_DOWN / RANGE / MIXED), vol_bucket (LOW/MID/HIGH vs the trailing 60-session day-range terciles),
  open_vs_prev (ABOVE_PDH / BELOW_PDL / INSIDE), expiry_day, close_pos (0..1 in the day's range), vwap_side_close (ABOVE/BELOW).
Day type is known only at the close (used for attribution); the early labels (gap, first30, open_vs_prev, expiry, vol bucket) are known by 09:45 (usable for gating).
Usage: python3 tools/regime.py [YYYY-MM-DD]  → prints the labels; writes/updates paper/REGIMES.csv (one row per session)."""
from __future__ import annotations

import csv, json, statistics as st, sys
from collections import defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]; PAPER = ROOT / "paper"; sys.path.insert(0, str(ROOT))
from engine.monitor import load_1m  # noqa: E402
from engine.session.labels import expiry_flags  # noqa: E402
from engine.emavwap import session_twap  # noqa: E402
from engine.ohlc import Candle  # noqa: E402
IST = ZoneInfo("Asia/Kolkata")


def all_days():
    by = defaultdict(list)
    for c in load_1m("NIFTY"):
        by[c.timestamp.date()].append(c)
    for fp in sorted(PAPER.glob("FEED_*.json")):
        d = date.fromisoformat(fp.stem[5:])
        if d not in by:
            try:
                j = json.loads(fp.read_text()); by[d] = [Candle(datetime.combine(d, datetime.strptime(t, "%H:%M").time(), tzinfo=IST), o, h, l, c, timedelta(minutes=1)) for t, o, h, l, c in j["bars"]]
            except Exception:
                pass
    return by


def labels(d, by, days):
    b = by[d]; i = days.index(d); prev = by[days[i - 1]] if i > 0 else None
    o = b[0].open; c = b[-1].close; h = max(x.high for x in b); l = min(x.low for x in b); rng = h - l
    f30 = [x for x in b if x.timestamp.strftime("%H:%M") < "09:45"]; r30 = max(x.high for x in f30) - min(x.low for x in f30); m30 = f30[-1].close - o
    pdh = max(x.high for x in prev) if prev else None; pdl = min(x.low for x in prev) if prev else None; pdc = prev[-1].close if prev else None
    gap = 100 * (o - pdc) / pdc if pdc else 0.0; cl = (c - l) / rng if rng else 0.5
    day_type = ("TREND_UP" if cl >= 0.8 else "TREND_DOWN" if cl <= 0.2 else "MIXED") if rng >= 150 else "RANGE"
    hist = [max(x.high for x in by[dd]) - min(x.low for x in by[dd]) for dd in days[max(0, i - 60):i]]
    vol = "MID"
    if len(hist) >= 20:
        lo_t, hi_t = sorted(hist)[len(hist) // 3], sorted(hist)[2 * len(hist) // 3]; vol = "LOW" if rng < lo_t else "HIGH" if rng > hi_t else "MID"
    ovp = "ABOVE_PDH" if (pdh and o > pdh) else "BELOW_PDL" if (pdl and o < pdl) else "INSIDE"
    tw = session_twap(b)
    return {"date": d.isoformat(), "gap_pct": round(gap, 2), "first30_range": round(r30, 1), "first30_move": round(m30, 1), "day_range": round(rng, 1), "day_type": day_type, "vol_bucket": vol, "open_vs_prev": ovp,
            "expiry_day": int(bool(expiry_flags(d).get("nifty_weekly_expiry"))), "close_pos": round(cl, 2), "vwap_side_close": "ABOVE" if c > tw[-1] else "BELOW", "open_to_close": round(c - o, 1)}


def main():
    by = all_days(); days = sorted(by); want = [date.fromisoformat(sys.argv[1])] if len(sys.argv) > 1 else [dd for dd in days if dd >= date(2026, 4, 13)]
    rows = {}
    p = PAPER / "REGIMES.csv"
    if p.exists():
        for r in csv.DictReader(open(p)):
            rows[r["date"]] = r
    for d in want:
        if d in by and len(by[d]) > 60:
            rows[d.isoformat()] = labels(d, by, days)
    cols = ["date", "gap_pct", "first30_range", "first30_move", "day_range", "day_type", "vol_bucket", "open_vs_prev", "expiry_day", "close_pos", "vwap_side_close", "open_to_close"]
    with open(p, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=cols); w.writeheader()
        for k in sorted(rows):
            w.writerow({c: rows[k].get(c, "") for c in cols})
    for d in want[-3:]:
        if d.isoformat() in rows:
            print(rows[d.isoformat()])
    from collections import Counter
    print("day types since 2026-04-13:", dict(Counter(r["day_type"] for k, r in rows.items() if k >= "2026-04-13")), "| expiry days:", sum(1 for k, r in rows.items() if k >= "2026-04-13" and str(r["expiry_day"]) == "1"))


if __name__ == "__main__":
    main()
