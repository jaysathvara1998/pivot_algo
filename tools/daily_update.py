#!/usr/bin/env python3
"""Daily continuous-learning job (Skill 28). Usage:
  python3 tools/daily_update.py [--as-of YYYY-MM-DD] [--window 120] [--fetch] [--rebuild-reference]
--fetch appends new sessions (after the last stored session) from Dhan to datasets/raw/dhan/{idx}_index_1m_ext.csv and {idx}_atm_week_1m_ext.csv;
the token is read from DHAN_ACCESS_TOKEN only. Then the monitor runs: facts over the trailing window vs the frozen development reference, forward
ledger update, reports/monitor/<date>.json, knowledge/MONITOR_STATUS.md, research/MONITOR_LOG.md. Idempotent for a given --as-of."""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import time
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from engine.monitor import run_update, load_1m, session_dates  # noqa: E402
from engine.monitor.data import PATHS  # noqa: E402

SEC = {"NIFTY": ("13", "NSE_FNO"), "SENSEX": ("51", "BSE_FNO")}
IST = ZoneInfo("Asia/Kolkata"); BASE = "https://api.dhan.co/v2"; REQUIRED = ["open", "high", "low", "close", "iv", "volume", "strike", "oi", "spot"]


# ---- Dhan REST helpers (self-contained; the research project's datasets/raw/dhan/fetch_dhan.py is not part of this deployment)
def post(path: str, body: dict, retries: int = 5):
    tok = os.environ.get("DHAN_ACCESS_TOKEN")
    if not tok:
        sys.exit("DHAN_ACCESS_TOKEN not set")
    for k in range(retries):
        req = urllib.request.Request(BASE + path, data=json.dumps(body).encode(), headers={"access-token": tok, "Content-Type": "application/json", "Accept": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=180) as r:
                return json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = e.read().decode()[:200]
            if e.code in (429, 500, 502, 503, 504) and k < retries - 1:
                time.sleep(2 * (k + 1)); continue
            raise RuntimeError(f"{path} {e.code} {msg} body={body}")
        except (urllib.error.URLError, TimeoutError, OSError):
            if k < retries - 1:
                time.sleep(3 * (k + 1)); continue
            raise


def ts_iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, IST).strftime("%Y-%m-%d %H:%M:%S")


def chunks(start: date, end: date, days: int):
    a = start
    while a <= end:
        b = min(end, a + timedelta(days=days - 1)); yield a, b; a = b + timedelta(days=1)


def _last_date_in(path: Path):
    """Date of the last row of an append-only CSV (None if missing/empty)."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2); n = fh.tell(); fh.seek(max(0, n - 4096)); tail = fh.read().decode(errors="replace").strip().splitlines()
        return date.fromisoformat(tail[-1][:10]) if tail and tail[-1][:4].isdigit() else None
    except Exception:
        return None


def complete_until(until: date) -> date:
    """Never append today's session before it is over: a half-day in the store would give tomorrow's pivots a wrong high/low/close."""
    now = datetime.now(IST)
    return min(until, now.date() - timedelta(days=1)) if now.time() < datetime.strptime("15:35", "%H:%M").time() else until


def fetch_new(index: str, until: date) -> int:
    until = complete_until(until)
    existing = load_1m(index); dates = session_dates(existing)
    if not dates:
        print(f"{index}: no stored sessions, nothing to extend", flush=True); return 0
    start = dates[-1] + timedelta(days=1)
    ostart = (_last_date_in(PATHS[index]["opt_ext"]) or (start - timedelta(days=1))) + timedelta(days=1)   # the option file can lag the index file (e.g. after a manual index backfill)
    if start > until and ostart > until:
        return 0
    sid, seg = SEC[index]; rows = []
    for a, b in chunks(start, until, 89) if start <= until else ():
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
    for a, b in chunks(ostart, until, 28) if ostart <= until else ():
        for leg in ("CALL", "PUT"):
            body = {"exchangeSegment": seg, "interval": 1, "securityId": sid, "instrument": "OPTIDX", "expiryFlag": "WEEK", "expiryCode": 1, "strike": "ATM", "drvOptionType": leg,
                    "requiredData": REQUIRED, "fromDate": a.isoformat(), "toDate": (b + timedelta(days=1)).isoformat()}
            r = post("/charts/rollingoption", body); time.sleep(0.6)
            d = (r.get("data") or {}).get("ce" if leg == "CALL" else "pe") or {}
            for i in range(len(d.get("timestamp") or [])):
                ts = ts_iso(d["timestamp"][i])
                if not (ostart.isoformat() <= ts[:10] <= until.isoformat()):   # the rolling endpoint's toDate is inclusive and we pass until+1 → drop rows past `until` (today's half session)
                    continue
                orows.append([ts, "CE" if leg == "CALL" else "PE", d["open"][i], d["high"][i], d["low"][i], d["close"][i], d["iv"][i], d["volume"][i], d["oi"][i], d["strike"][i], d["spot"][i]])
    po = PATHS[index]["opt_ext"]; newo = po.exists()
    with open(po, "a", newline="") as fh:
        w = csv.writer(fh)
        if not newo:
            w.writerow(["ts", "leg", "open", "high", "low", "close", "iv", "volume", "oi", "strike", "spot"])
        w.writerows(orows)
    with open(ROOT / "datasets/raw/dhan/FETCH_LOG.md", "a") as log:
        log.write(f"{datetime.now().isoformat(timespec='seconds')} daily_update {index} index {start}..{until} rows={len(rows)} · options {ostart}..{until} rows={len(orows)}\n")
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
            if not (PATHS[idx]["ext"].exists() or PATHS[idx]["kite"].exists()):
                continue                                                      # this deployment carries NIFTY only
            print(idx, "new 1m rows", fetch_new(idx, as_of), flush=True)
    if not PATHS["NIFTY"]["kite"].exists():
        print("monitor skipped: the research datasets (Kite canonical, reference) are not part of this deployment", flush=True); return
    rep = run_update(as_of, a.window, rebuild_reference=a.rebuild_reference)
    for idx, r in rep["indices"].items():
        print(idx, r["first"], "→", r["last"], {k.split("_")[0]: (v["status"], v["edge"]) for k, v in r["facts"].items()})
    print("ledger", {k: v["verdict"] for k, v in rep["ledger"].items()})


if __name__ == "__main__":
    main()
