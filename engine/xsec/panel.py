"""Panel of daily stock bars aligned to the index calendar (Skill 31)."""
from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
STOCKS = ROOT / "datasets/raw/dhan/stocks_daily"


def calendar(path: Path = ROOT / "datasets/raw/dhan/nifty_index_daily.csv") -> list:
    return [date.fromisoformat(r["date"]) for r in csv.DictReader(open(path, newline=""))]


def load_universe_snapshot() -> dict:
    snap = sorted(STOCKS.glob("UNIVERSE_SNAPSHOT_*.csv"))[-1]
    return {r["security_id"]: r for r in csv.DictReader(open(snap, newline=""))}


class Stock:
    __slots__ = ("sid", "symbol", "fno", "idx", "o", "h", "l", "c", "v")

    def __init__(self, sid, symbol, fno, cal_index):
        self.sid = sid; self.symbol = symbol; self.fno = fno
        n = len(cal_index); self.o = [None] * n; self.h = [None] * n; self.l = [None] * n; self.c = [None] * n; self.v = [None] * n


NON_STOCK_MARKERS = ("ETF", " - ", "BEES", "LIQUID", " FUND", "MUTUAL", "GOLD", "SILVER", "NIFTY", "SENSEX", "INVIT", "REIT", "TRUST")   # funds, ETFs, InvITs/REITs inside the EQ series


def is_common_stock(meta: dict) -> bool:
    name = (meta.get("display_name") or "").upper(); sym = (meta.get("symbol") or "").upper()
    return not any(m in name for m in NON_STOCK_MARKERS) and not any(m in sym for m in ("ETF", "BEES", "IETF"))


def load_panel(cal: list, limit: int | None = None, only_fno: bool = False) -> list:
    """One Stock per file, aligned to `cal`; days absent from the calendar are dropped; DUPLICATED dates within a file are dropped entirely (data-quality rule);
    ETFs / fund schemes / InvITs / REITs excluded by name; missing days stay None."""
    cal_index = {d: i for i, d in enumerate(cal)}; snap = load_universe_snapshot(); out = []
    files = sorted(p for p in STOCKS.glob("*.csv") if not p.name.startswith("UNIVERSE"))
    for p in files[:limit] if limit else files:
        sid = p.stem; meta = snap.get(sid, {})
        if only_fno and meta.get("is_fno_underlying") != "True":
            continue
        if not is_common_stock(meta):
            continue
        s = Stock(sid, meta.get("symbol", sid), meta.get("is_fno_underlying") == "True", cal_index)
        seen = {}; dup = set()
        with open(p, newline="") as fh:
            rows = list(csv.DictReader(fh))
        for r in rows:
            if r["date"] in seen:
                dup.add(r["date"])
            seen[r["date"]] = r
        for r in rows:
            if r["date"] in dup:
                continue
            i = cal_index.get(date.fromisoformat(r["date"]))
            if i is None:
                continue
            try:
                s.o[i] = float(r["open"]); s.h[i] = float(r["high"]); s.l[i] = float(r["low"]); s.c[i] = float(r["close"]); s.v[i] = float(r["volume"])
            except ValueError:
                continue
        out.append(s)
    return out
