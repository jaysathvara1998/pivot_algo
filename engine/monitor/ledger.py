"""Forward ledger (PROJECT DEFINITION, Skill 28): hypotheses registered with a date accumulate outcomes ONLY from sessions after that date. This is the
project's clean out-of-sample test from now on: nothing in the ledger has been seen by the author at registration."""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

from .facts import FACTS
from .drift import _one_prop_z

LEDGER_PATH = Path(__file__).resolve().parents[2] / "research/FORWARD_LEDGER.json"

DEFAULT_ENTRIES = [
    {"name": "L01_streak_reversal_5m_holds", "fact": "F01_streak_reversal_5m", "registered_on": "2026-10-05", "direction": "ABOVE_NULL", "null": 0.495, "min_n": 300,
     "hypothesis": "The 5m streak reversal (0.56 in development, replicated on validation) persists in sessions after 2026-10-05."},
    {"name": "L02_expiry_overpricing_holds", "fact": "F05_expiry_ratio_vs_baseline", "registered_on": "2026-10-05", "direction": "BELOW_NULL", "null": None, "min_n": 12,
     "hypothesis": "Expiry-day realised/implied range stays below the non-expiry baseline (Level 26: 0.45 vs 0.53) in sessions after 2026-10-05."},
    {"name": "L03_gap_small_up_fill_holds", "fact": "F03_gap_small_up_fill", "registered_on": "2026-10-05", "direction": "ABOVE_NULL", "null": 0.665, "min_n": 40,
     "hypothesis": "SMALL UP gaps keep filling more often than the gap-aware walk (0.81 in development) after 2026-10-05."},
    {"name": "L04_aligned_sweep_engulf_5m", "fact": "F07_aligned_sweep_engulf_5m", "registered_on": "2026-10-06", "direction": "ABOVE_NULL", "null": 0.50, "min_n": 150,
     "hypothesis": "Skill 33 lead (amendment A4): on 5m bars, a sweep of the 20-bar low or a bullish engulfing at support aligned with a 15m UP structure is followed by a higher close 5 bars later (development ≈ 0.58 on n 258, uncorrected). Expected to regress toward 0.50."},
    {"name": "L05_overnight_hold", "fact": "F08_overnight_hold", "registered_on": "2026-10-09", "direction": "ABOVE_NULL", "null": 0.50, "min_n": 120,
     "hypothesis": "Skill 34: the executable overnight drift persists — the 09:20 print exceeds the previous 15:20 print in more than half of sessions (development 0.60 NIFTY / 0.58 SENSEX, 2023-09 → 2025-06)."},
]


def load_ledger(path: Path = LEDGER_PATH) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    return {"entries": [dict(e, k=0, n=0, last_session=None, history=[], verdict="PENDING") for e in DEFAULT_ENTRIES]}


def save_ledger(ledger: dict, path: Path = LEDGER_PATH) -> None:
    path.write_text(json.dumps(ledger, indent=1, sort_keys=True, default=str))


def update_ledger(ledger: dict, stats: dict, index: str) -> dict:
    """Append per-session outcomes for sessions strictly after registration and after the last recorded session (idempotent)."""
    for e in ledger["entries"]:
        per = stats.get(e["fact"], {}); reg = date.fromisoformat(e["registered_on"])
        last = date.fromisoformat(e["last_session"]) if e.get("last_session") else None
        for d in sorted(per):
            if d <= reg or (last is not None and d <= last):
                continue
            v = per[d]
            if isinstance(v, tuple):
                k, n = v
            elif isinstance(v, dict) and "ratios" in v:       # contrast facts: count an expiry day as a success if its mean ratio < 0.5 (Level 26 baseline midpoint)
                if not v["expiry"]:
                    continue
                m = sum(v["ratios"]) / len(v["ratios"]); k, n = int(m < 0.5), 1
            else:
                continue
            e["k"] += k; e["n"] += n; e["last_session"] = d.isoformat(); e["history"].append({"date": d.isoformat(), "k": k, "n": n, "index": index})
        null = e["null"] if e["null"] is not None else 0.5
        z = _one_prop_z(e["k"], e["n"], null)
        e["rate"] = round(e["k"] / e["n"], 4) if e["n"] else None; e["z_vs_null"] = round(z, 2) if z is not None else None
        e["verdict"] = "PENDING" if e["n"] < e["min_n"] else ("CONFIRMED_FORWARD" if (z >= 2.0 if e["direction"] == "ABOVE_NULL" else z <= -2.0) else "NOT_CONFIRMED_FORWARD")
    return ledger
