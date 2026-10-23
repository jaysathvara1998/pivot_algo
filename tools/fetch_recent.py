#!/usr/bin/env python3
"""Nightly data maintenance for the cloud deployment: append the latest sessions of NIFTY 1m index bars and rolling ATM option rows from Dhan
to datasets/raw/dhan/nifty_index_1m_ext.csv and nifty_atm_week_1m_ext.csv (the pivot brain needs yesterday's session for its levels; the
journal's mood labels use the trailing 60 sessions). Token: DHAN_ACCESS_TOKEN or ~/.dhan/access_token."""
import os, sys
from datetime import date
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
if not os.environ.get("DHAN_ACCESS_TOKEN"):
    p = Path.home() / ".dhan" / "access_token"
    if p.exists():
        os.environ["DHAN_ACCESS_TOKEN"] = p.read_text().strip()
from daily_update import fetch_new  # noqa: E402
n = fetch_new("NIFTY", date.today()); print(f"fetch_recent: {n} new session(s) appended")
