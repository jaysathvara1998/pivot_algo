#!/usr/bin/env python3
"""Daily continuous-learning job (Skill 28). Usage:
  python3 tools/daily_update.py [--as-of YYYY-MM-DD] [--window 120] [--fetch] [--rebuild-reference]
--fetch appends new sessions (after the last stored session) from Dhan to datasets/raw/dhan/{idx}_index_1m_ext.csv and {idx}_atm_week_1m_ext.csv;
the token is read from DHAN_ACCESS_TOKEN only. Then the monitor runs: facts over the trailing window vs the frozen development reference, forward
ledger update, reports/monitor/<date>.json, knowledge/MONITOR_STATUS.md, research/MONITOR_LOG.md. Idempotent for a given --as-of."""
from __future__ import annotations

import argparse
import csv
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.monitor import run_update, load_1m, session_dates  # noqa: E402
from engine.monitor.data import PATHS  # noqa: E402

SEC = {"NIFTY": ("13", "NSE_FNO"), "SENSEX": ("51", "BSE_FNO")}


def fetch_new(index: str, until: date) -> int:
    sys.path.insert(0, str(ROOT / "datasets/raw/dhan"))
    from fetch_dhan import post, ts_iso, chunks, REQUIRED  # noqa: E402
    existing = load_1m(index); last = session_dates(existing)[-1]
    start = last + timedelta(days=1)
    if start > until:
        return 0
    sid, seg = SEC[index]; rows = []
    for a, b in chunks(start, until, 89):
        r = post("/charts/intraday", {"securityId": sid, "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "1", "oi": False, "fromDate": a.isoformat(), "toDate": b.isoformat()}); time.sleep(0.6)
        for i in range(len(r.get("timestamp") or [])):
            ts = ts_iso(r["timestamp"][i])
            if not ("09:15" <= ts[11:16] <= "15:29"):                        # Dhan occasionally returns a stray post-close print (e.g. 19:05 with zero volume)
                continue
            rows.append([ts, r["open"][i], r["high"][i], r["low"][i], r["close"][i], r["volume"][i]])
    p = PATHS[index]["ext"]; new = p.exists()
    with open(p, "a", newline="") as fh:
        w = csv.writer(fh)
        if not new:
            w.writerow(["ts", "open", "high", "low", "close", "volume"])
        w.writerows(rows)
    # options (ATM weekly, both legs) for the same dates
    orows = []
    for a, b in chunks(start, until, 28):
        for leg in ("CALL", "PUT"):
            body = {"exchangeSegment": seg, "interval": 1, "securityId": sid, "instrument": "OPTIDX", "expiryFlag": "WEEK", "expiryCode": 1, "strike": "ATM", "drvOptionType": leg,
                    "requiredData": REQUIRED, "fromDate": a.isoformat(), "toDate": (b + timedelta(days=1)).isoformat()}
            r = post("/charts/rollingoption", body); time.sleep(0.6)
            d = (r.get("data") or {}).get("ce" if leg == "CALL" else "pe") or {}
            for i in range(len(d.get("timestamp") or [])):
                orows.append([ts_iso(d["timestamp"][i]), "CE" if leg == "CALL" else "PE", d["open"][i], d["high"][i], d["low"][i], d["close"][i], d["iv"][i], d["volume"][i], d["oi"][i], d["strike"][i], d["spot"][i]])
    po = PATHS[index]["opt_ext"]; newo = po.exists()
    with open(po, "a", newline="") as fh:
        w = csv.writer(fh)
        if not newo:
            w.writerow(["ts", "leg", "open", "high", "low", "close", "iv", "volume", "oi", "strike", "spot"])
        w.writerows(orows)
    with open(ROOT / "datasets/raw/dhan/FETCH_LOG.md", "a") as log:
        log.write(f"{datetime.now().isoformat(timespec='seconds')} daily_update {index} {start}..{until} index_rows={len(rows)} option_rows={len(orows)}\n")
    return len(rows)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--as-of", default=date.today().isoformat()); ap.add_argument("--window", type=int, default=120)
    ap.add_argument("--fetch", action="store_true"); ap.add_argument("--rebuild-reference", action="store_true"); a = ap.parse_args()
    as_of = date.fromisoformat(a.as_of)
    if a.fetch:
        if not os.environ.get("DHAN_ACCESS_TOKEN"):
            tf = Path.home() / ".dhan" / "access_token"                      # written by tools/dhan_token.py (mode 600), never inside the repo
            if tf.exists() and tf.read_text().strip():
                os.environ["DHAN_ACCESS_TOKEN"] = tf.read_text().strip()
            else:
                sys.exit("--fetch needs DHAN_ACCESS_TOKEN in the environment or ~/.dhan/access_token (run tools/dhan_token.py)")
        for idx in ("NIFTY", "SENSEX"):
            print(idx, "new 1m rows", fetch_new(idx, as_of), flush=True)
    rep = run_update(as_of, a.window, rebuild_reference=a.rebuild_reference)
    for idx, r in rep["indices"].items():
        print(idx, r["first"], "→", r["last"], {k.split("_")[0]: (v["status"], v["edge"]) for k, v in r["facts"].items()})
    print("ledger", {k: v["verdict"] for k, v in rep["ledger"].items()})


if __name__ == "__main__":
    main()
