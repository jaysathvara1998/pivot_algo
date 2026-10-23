"""Data loading for the monitor: canonical Kite 1m plus the Dhan extension file for sessions after the Kite range (append-only, written by tools/daily_update.py)."""
from __future__ import annotations

import csv
from datetime import datetime, timedelta, date
from pathlib import Path
from zoneinfo import ZoneInfo

from engine.ohlc import Candle, candle_in_session
from engine.session.labels import SPECIAL_SESSION_DATES

IST = ZoneInfo("Asia/Kolkata"); M1 = timedelta(minutes=1)
ROOT = Path(__file__).resolve().parents[2]
PATHS = {"NIFTY": {"kite": ROOT / "datasets/raw/kite/nifty_1m.csv", "ext": ROOT / "datasets/raw/dhan/nifty_index_1m_ext.csv", "opt": ROOT / "datasets/raw/dhan/nifty_atm_week_1m.csv", "opt_ext": ROOT / "datasets/raw/dhan/nifty_atm_week_1m_ext.csv"},
         "SENSEX": {"kite": ROOT / "datasets/raw/kite/sensex_1m.csv", "ext": ROOT / "datasets/raw/dhan/sensex_index_1m_ext.csv", "opt": ROOT / "datasets/raw/dhan/sensex_atm_week_1m.csv", "opt_ext": ROOT / "datasets/raw/dhan/sensex_atm_week_1m_ext.csv"}}


def _parse(ts: str) -> datetime:
    t = datetime.fromisoformat(ts.replace(" ", "T"))
    return t if t.tzinfo else t.replace(tzinfo=IST)


def load_1m(index: str, until: date | None = None) -> list:
    """All regular-session 1m candles (special sessions excluded), Kite canonical first, then the extension for later sessions only."""
    p = PATHS[index]; out = {}
    for key in ("kite", "ext"):
        path = p[key]
        if not path.exists():
            continue
        kite_last = max(out).date() if (key == "ext" and out) else None
        with open(path, newline="") as fh:
            for r in csv.DictReader(fh):
                t = _parse(r["ts"])
                if kite_last is not None and t.date() <= kite_last:
                    continue
                if until is not None and t.date() > until:
                    continue
                if candle_in_session(t, M1) and t.date() not in SPECIAL_SESSION_DATES:
                    out[t] = Candle(t, float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), M1)
    return [out[k] for k in sorted(out)]


def session_dates(candles) -> list:
    seen = []; last = None
    for c in candles:
        d = c.timestamp.date()
        if d != last:
            seen.append(d); last = d
    return seen


def load_options(index: str) -> dict:
    from engine.options.data import load_rolling
    p = PATHS[index]; out = {}
    for key in ("opt", "opt_ext"):
        if p[key].exists():
            out.update(load_rolling(p[key]))
    return out
