#!/usr/bin/env python3
"""Ask the running LIVE trader to open ONE test position now (real order), managed by its normal stop/target exits.
  python3 tools/live_manual.py CE --sl 15 --tp 15 [--itm 2]     → buy a CE 2 strikes ITM at the next completed bar; stop = entry − 15 pts, target = entry + 15 pts (index)
  python3 tools/live_manual.py PE --sl 20 --tp 30 --itm 0       → ATM put, stop +20 / target −30 index points
It only writes paper/live/COMMAND_<date>.json; the live trader (tools/paper_trade.py --live) reads it within ~5 s and places the order. Nothing happens if the live trader is not running."""
import argparse, json, subprocess, sys
from datetime import date, datetime
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]

a = argparse.ArgumentParser(); a.add_argument("leg", nargs="?", choices=["CE", "PE", "ce", "pe"]); a.add_argument("--exit", action="store_true", help="close the open manual position at market on the next check (within ~5 s)"); a.add_argument("--sl", type=float, default=15.0); a.add_argument("--tp", type=float, default=15.0); a.add_argument("--itm", type=int, default=2); a.add_argument("--sl-prem", type=float, default=None, help="stop distance in ₹ on the option premium (super-order leg); default = index stop × delta"); a.add_argument("--tp-prem", type=float, default=None, help="target distance in ₹ on the premium; default = index target × delta")
x = a.parse_args(); d = date.today().isoformat(); cmd = ROOT / "paper" / "live" / f"COMMAND_{d}.json"
if subprocess.run(["pgrep", "-f", "tools/paper_trade.py --live"], capture_output=True).returncode != 0:
    sys.exit("the LIVE trader is not running — start it first (dashboard → Start LIVE trader)")
if x.exit:
    cmd.parent.mkdir(parents=True, exist_ok=True); cmd.write_text(json.dumps({"action": "exit", "written": datetime.now().isoformat(timespec="seconds")}))
    sys.exit(f"EXIT command written → {cmd}\nthe live trader sells the open manual position at market on its next check (≤ 5 s) and reports LIVE EXIT MANUAL_EXIT on Telegram.")
if not x.leg:
    sys.exit("give CE or PE to open, or --exit to close")
try:
    j = json.loads((ROOT / "paper" / f"FEED_{d}.json").read_text()); bars = j.get("bars", []); ltp = j.get("ltp")
    if bars:
        closes = [b[4] for b in bars]; vwap = sum(closes) / len(closes); print(f"feed: index {ltp} · last bar {bars[-1][0]} close {closes[-1]} · session average {vwap:.1f} ({'above' if closes[-1] > vwap else 'below'} it)")
except Exception as e:
    print(f"feed snapshot not readable ({str(e)[:60]}) — the trader will still act on the next bar")
cmd.parent.mkdir(parents=True, exist_ok=True); cmd.write_text(json.dumps({"leg": x.leg.upper(), "sl": x.sl, "tp": x.tp, "itm": x.itm, "sl_prem": x.sl_prem, "tp_prem": x.tp_prem, "written": datetime.now().isoformat(timespec="seconds")}))
print(f"command written: BUY {x.leg.upper()} {x.itm} strike(s) ITM, stop {x.sl:g} pts, target {x.tp:g} pts → {cmd}\nthe live trader will place the order at the next completed 1-minute bar and report on Telegram (LIVE ENTRY, then LIVE EXIT STOP/TARGET/TIME).")
