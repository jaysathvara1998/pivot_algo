"""Data quality checker for raw OHLC CSV files (Level 1).

Reports — never silently repairs. Standard library only.

    python3 -m engine.ohlc.quality path.csv --interval 1m [--tz Asia/Kolkata] [--max-examples 5]

Accepted column names (case-insensitive): timestamp|ts|time|datetime|date, open, high, low, close.
Timestamps: ISO-8601, tz-aware ("2022-04-29 09:15:00+05:30") or naive (then --tz is applied).
"""
from __future__ import annotations

import argparse
import csv
import json
import math
import sys
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo

from .candle import InvalidOHLCError, validate_ohlc
from .session import NSE_BSE_REGULAR, SessionSpec, candle_in_session

_TS_NAMES = ("timestamp", "ts", "time", "datetime", "date")
_INTERVALS = {"1m": 60, "3m": 180, "5m": 300, "15m": 900, "30m": 1800, "60m": 3600, "1h": 3600, "1d": 86400}


def parse_interval(s: str) -> timedelta:
    if s not in _INTERVALS:
        raise ValueError(f"unknown interval {s!r}; one of {sorted(_INTERVALS)}")
    return timedelta(seconds=_INTERVALS[s])


@dataclass
class QualityReport:
    path: str
    interval: str
    rows: int = 0
    valid_rows: int = 0
    first_timestamp: Optional[str] = None
    last_timestamp: Optional[str] = None
    invalid_ohlc: Counter = field(default_factory=Counter)     # code -> count
    unparseable_rows: int = 0
    duplicate_timestamps: int = 0
    non_increasing_timestamps: int = 0
    off_grid_timestamps: int = 0            # start not aligned to interval
    out_of_session: int = 0
    zero_range: int = 0
    zero_range_in_session: int = 0
    time_gaps_within_session: int = 0        # missing candles between in-session neighbours
    examples: dict = field(default_factory=lambda: {})

    def add_example(self, key: str, text: str, limit: int) -> None:
        lst = self.examples.setdefault(key, [])
        if len(lst) < limit:
            lst.append(text)

    def to_dict(self) -> dict:
        d = self.__dict__.copy()
        d["invalid_ohlc"] = dict(self.invalid_ohlc)
        return d


def _find_col(header: list[str], candidates: tuple[str, ...]) -> Optional[str]:
    lower = {h.lower().strip(): h for h in header}
    for c in candidates:
        if c in lower:
            return lower[c]
    return None


def _parse_ts(raw: str, tz: ZoneInfo) -> datetime:
    s = raw.strip().replace("Z", "+00:00")
    ts = datetime.fromisoformat(s)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=tz)
    return ts


def check_csv(
    path: str | Path,
    interval: str,
    tz: str = "Asia/Kolkata",
    session: SessionSpec = NSE_BSE_REGULAR,
    max_examples: int = 5,
) -> QualityReport:
    rep = QualityReport(path=str(path), interval=interval)
    step = parse_interval(interval)
    zone = ZoneInfo(tz)
    step_s = int(step.total_seconds())

    with open(path, newline="") as fh:
        reader = csv.DictReader(fh)
        header = reader.fieldnames or []
        ts_col = _find_col(header, _TS_NAMES)
        cols = {k: _find_col(header, (k,)) for k in ("open", "high", "low", "close")}
        if ts_col is None or any(v is None for v in cols.values()):
            raise ValueError(f"required columns missing; header={header}")

        prev_ts: Optional[datetime] = None
        prev_in_session = False
        for lineno, row in enumerate(reader, start=2):
            rep.rows += 1
            try:
                ts = _parse_ts(row[ts_col], zone)
                o, h, l, c = (float(row[cols[k]]) for k in ("open", "high", "low", "close"))
            except (ValueError, TypeError, KeyError) as e:
                rep.unparseable_rows += 1
                rep.add_example("unparseable", f"line {lineno}: {e}", max_examples)
                continue

            if rep.first_timestamp is None:
                rep.first_timestamp = ts.isoformat()
            rep.last_timestamp = ts.isoformat()

            # ordering
            if prev_ts is not None:
                if ts == prev_ts:
                    rep.duplicate_timestamps += 1
                    rep.add_example("duplicate", f"line {lineno}: {ts.isoformat()}", max_examples)
                elif ts < prev_ts:
                    rep.non_increasing_timestamps += 1
                    rep.add_example("non_increasing", f"line {lineno}: {ts.isoformat()} < {prev_ts.isoformat()}", max_examples)

            # grid alignment (relative to midnight in the session tz)
            local = ts.astimezone(session.tz)
            secs = local.hour * 3600 + local.minute * 60 + local.second
            if step_s < 86400 and secs % step_s != 0:
                rep.off_grid_timestamps += 1
                rep.add_example("off_grid", f"line {lineno}: {ts.isoformat()}", max_examples)

            in_sess = candle_in_session(ts, step, session)
            if not in_sess:
                rep.out_of_session += 1
                rep.add_example("out_of_session", f"line {lineno}: {ts.isoformat()}", max_examples)

            # OHLC validity
            try:
                validate_ohlc(o, h, l, c)
            except InvalidOHLCError as e:
                rep.invalid_ohlc[e.code] += 1
                rep.add_example(f"invalid_{e.code}", f"line {lineno}: o={o} h={h} l={l} c={c} @ {ts.isoformat()}", max_examples)
            else:
                rep.valid_rows += 1
                if h == l:
                    rep.zero_range += 1
                    if in_sess:
                        rep.zero_range_in_session += 1
                        rep.add_example("zero_range_in_session", f"line {lineno}: {ts.isoformat()} price={c}", max_examples)

            # missing candles between two in-session neighbours on the same session date
            if prev_ts is not None and in_sess and prev_in_session and ts > prev_ts:
                if local.date() == prev_ts.astimezone(session.tz).date() and (ts - prev_ts) > step:
                    rep.time_gaps_within_session += 1
                    rep.add_example("time_gap_within_session", f"line {lineno}: {prev_ts.isoformat()} → {ts.isoformat()}", max_examples)

            prev_ts, prev_in_session = ts, in_sess
    return rep


def main(argv: Optional[list[str]] = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("path")
    ap.add_argument("--interval", required=True, choices=sorted(_INTERVALS))
    ap.add_argument("--tz", default="Asia/Kolkata")
    ap.add_argument("--max-examples", type=int, default=5)
    a = ap.parse_args(argv)
    rep = check_csv(a.path, a.interval, tz=a.tz, max_examples=a.max_examples)
    json.dump(rep.to_dict(), sys.stdout, indent=2, default=str)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
