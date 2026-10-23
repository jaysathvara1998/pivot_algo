"""Join the Dhan rolling-ATM option rows to the 1m stream rows by minute (Skill 26)."""
from __future__ import annotations

import csv
from pathlib import Path


def load_rolling(path: Path) -> dict:
    """{ 'YYYY-MM-DD HH:MM': {ce, pe, iv, strike, spot} } — both legs required; iv = mean of CE and PE IV."""
    legs = {}
    with open(path, newline="") as fh:
        for r in csv.DictReader(fh):
            k = r["ts"][:16]
            d = legs.setdefault(k, {})
            d[r["leg"]] = r
    out = {}
    for k, d in legs.items():
        if "CE" in d and "PE" in d and d["CE"]["strike"] == d["PE"]["strike"]:
            try:
                out[k] = {"ce": float(d["CE"]["close"]), "pe": float(d["PE"]["close"]), "iv": (float(d["CE"]["iv"]) + float(d["PE"]["iv"])) / 2,
                          "strike": float(d["CE"]["strike"]), "spot": float(d["CE"]["spot"])}
            except ValueError:
                continue
    return out


def minute_key(ts) -> str:
    return ts.strftime("%Y-%m-%d %H:%M")


def attach(stream: list, opts: dict) -> int:
    """Adds row['opt'] (or None) to each stream row; returns the number matched."""
    n = 0
    for row in stream:
        o = opts.get(minute_key(row["candle"].timestamp))
        row["opt"] = o
        n += o is not None
    return n
