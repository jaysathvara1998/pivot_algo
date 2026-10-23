"""One monitor run: load data, build engines over the trailing window, compute facts, compare with the frozen development reference, update the
forward ledger, write the daily report and the status page (Skill 28)."""
from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

from engine.setups.split import SPLIT
from .data import load_1m, load_options, session_dates
from .facts import FACTS, build_bundle, compute_all
from .drift import summarise, judge
from .ledger import load_ledger, save_ledger, update_ledger

ROOT = Path(__file__).resolve().parents[2]
REFERENCE_PATH = ROOT / "research/MONITOR_REFERENCE.json"
WARMUP_SESSIONS = 25


def _window_sessions(candles, as_of: date, n: int):
    dates = [d for d in session_dates(candles) if d <= as_of]
    return dates[-n:], dates[-(n + WARMUP_SESSIONS):]


def build_reference(indices=("NIFTY", "SENSEX"), path: Path = REFERENCE_PATH) -> dict:
    """Frozen development reference: facts over the DEVELOPMENT window (2023-09-11 → 2025-06-30). Rebuilt only on request."""
    a, b = SPLIT["DEVELOPMENT"]; ref = {"window": [a.isoformat(), b.isoformat()], "built_on": date.today().isoformat(), "indices": {}}
    for idx in indices:
        candles = load_1m(idx, until=b)
        bundle = build_bundle(candles, idx, load_options(idx)); stats = compute_all(bundle)
        dates = [d for d in session_dates(candles) if a <= d <= b]
        ref["indices"][idx] = {f.name: summarise(f, stats[f.name], dates, stats.get(f.name + "__base")) for f in FACTS}
    path.write_text(json.dumps(ref, indent=1, default=str))
    return ref


def run_update(as_of: date, window: int = 120, indices=("NIFTY", "SENSEX"), rebuild_reference: bool = False, write: bool = True) -> dict:
    ref = build_reference(indices) if (rebuild_reference or not REFERENCE_PATH.exists()) else json.loads(REFERENCE_PATH.read_text())
    ledger = load_ledger()
    report = {"as_of": as_of.isoformat(), "window_sessions": window, "reference_window": ref["window"], "indices": {}}
    for idx in indices:
        candles = load_1m(idx, until=as_of)
        wdates, wdates_warm = _window_sessions(candles, as_of, window)
        sub = [c for c in candles if c.timestamp.date() >= wdates_warm[0]]
        bundle = build_bundle(sub, idx, load_options(idx)); stats = compute_all(bundle)
        facts = {}
        for f in FACTS:
            w = summarise(f, stats[f.name], wdates, stats.get(f.name + "__base"))
            facts[f.name] = {"card": f.card, "description": f.description, "window": w, "reference": ref["indices"][idx][f.name], **judge(f, w, ref["indices"][idx][f.name])}
        ledger = update_ledger(ledger, stats, idx)
        report["indices"][idx] = {"sessions": len(wdates), "first": wdates[0].isoformat(), "last": wdates[-1].isoformat(), "facts": facts}
    report["ledger"] = {e["name"]: {k: e[k] for k in ("registered_on", "k", "n", "rate", "z_vs_null", "verdict", "last_session")} for e in ledger["entries"]}
    if write:
        save_ledger(ledger)
        (ROOT / "reports/monitor").mkdir(parents=True, exist_ok=True)
        (ROOT / f"reports/monitor/{as_of.isoformat()}.json").write_text(json.dumps(report, indent=1, default=str))
        write_status(report)
        with open(ROOT / "research/MONITOR_LOG.md", "a") as fh:
            fh.write(f"| {date.today().isoformat()} | as_of {as_of} | window {window} | " + "; ".join(f"{i}: " + ", ".join(f"{k.split('_')[0]} {v['status']}/{v['edge']}" for k, v in r["facts"].items()) for i, r in report["indices"].items()) + " |\n")
    return report


def write_status(report: dict, path: Path = ROOT / "knowledge/MONITOR_STATUS.md") -> None:
    L = [f"# MONITOR STATUS — as of {report['as_of']} (trailing {report['window_sessions']} sessions; reference = development {report['reference_window'][0]} → {report['reference_window'][1]})", "",
         "Status: HOLDS / WATCH / DRIFTED compare the window with the frozen development reference; edge: EDGE_PRESENT / EDGE_ABSENT compare the window with the fact's null. "
         "Descriptive monitoring only — no trading implication is derived here (see PROJECT_STATE.md).", ""]
    for idx, r in report["indices"].items():
        L += [f"## {idx} — {r['sessions']} sessions {r['first']} → {r['last']}", "", "| fact | cards | window value | reference | null | z vs ref | z vs null | status | edge |", "|---|---|---|---|---|---|---|---|---|"]
        for k, f in r["facts"].items():
            w = f["window"]; ref = f["reference"]
            val = w.get("rate", w.get("median", w.get("ratio_of_means"))); rv = ref.get("rate", ref.get("median", ref.get("ratio_of_means")))
            L.append(f"| {k} | {f['card']} | {val} (n {w.get('n')}) | {rv} (n {ref.get('n')}) | {w.get('null', '—')} | {f['z_vs_reference']} | {f['z_vs_null']} | {f['status']} | {f['edge']} |")
        L.append("")
    L += ["## Forward ledger (sessions after registration only)", "", "| hypothesis | registered | k / n | rate | z vs null | verdict | last session |", "|---|---|---|---|---|---|---|"]
    for k, e in report["ledger"].items():
        L.append(f"| {k} | {e['registered_on']} | {e['k']} / {e['n']} | {e['rate']} | {e['z_vs_null']} | {e['verdict']} | {e['last_session']} |")
    path.write_text("\n".join(L) + "\n")
