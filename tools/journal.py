#!/usr/bin/env python3
"""Trading journal for the paper run. Builds paper/JOURNAL.md and paper/journal.csv from every paper/TRADES_<date>.csv (plus the day's bars for excursions), keeps the trader's own notes
(lines under "Trader's notes" for each date are preserved across rebuilds; add one with --note DATE "text"). Usage: python3 tools/journal.py [--note 2026-10-13 "text"]."""
from __future__ import annotations

import argparse, csv, json, math, re, statistics as st, sys
from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; PAPER = ROOT / "paper"; sys.path.insert(0, str(ROOT))
from engine.monitor import load_1m  # noqa: E402
from engine.session.labels import expiry_flags  # noqa: E402


def _f(x, default=0.0):
    """float() that tolerates blank fields (e.g. a voided trade without an index exit)."""
    try:
        return float(x)
    except (TypeError, ValueError):
        return default

CAPITAL = 50_000.0


def bars_for_day(d, cache):
    if d in cache:
        return cache[d]
    out = []
    for c in load_1m("NIFTY"):
        if c.timestamp.date() == d:
            out.append((c.timestamp.strftime("%H:%M"), c.open, c.high, c.low, c.close))
    if not out:
        fp = PAPER / f"FEED_{d}.json"
        if fp.exists():
            out = [tuple(b) for b in json.loads(fp.read_text())["bars"]]
    cache[d] = out; return out


def load_trades():
    rows = []
    for p in sorted(PAPER.glob("TRADES_*.csv")):
        d = date.fromisoformat(p.stem[7:])
        for r in csv.DictReader(open(p)):
            r["date"] = d; rows.append(r)
    return rows


def enrich(t, bars):
    e = float(t["index_entry"]); stop = float(t["stop_at_entry"]); side = 1 if t["leg"] == "CE" else -1; risk = side * (e - stop)
    seg = [b for b in bars if t["entry_time"] < b[0] <= t["exit_time"]]
    mfe = max((side * ((b[2] if side > 0 else b[3]) - e) for b in seg), default=0.0); mae = min((side * ((b[3] if side > 0 else b[2]) - e) for b in seg), default=0.0)
    hold = (int(t["exit_time"][:2]) * 60 + int(t["exit_time"][3:])) - (int(t["entry_time"][:2]) * 60 + int(t["entry_time"][3:]))
    net = float(t["net_inr"]); mv = float(t["index_move_pts"])
    return {"risk_pts": round(risk, 1), "r_multiple": round(mv / risk, 2) if risk else None, "mfe_pts": round(mfe, 1), "mae_pts": round(mae, 1), "mfe_R": round(mfe / risk, 2) if risk else None, "mae_R": round(mae / risk, 2) if risk else None,
            "hold_min": hold, "premium_in": round(float(t["opt_entry"]) * int(t["lot"])), "option_change_pct": round(100 * (float(t["opt_exit"]) - float(t["opt_entry"])) / float(t["opt_entry"]), 1), "net": net,
            "expiry_day": bool(expiry_flags(t["date"]).get("nifty_weekly_expiry")), "rule_checks": rule_checks(t)}


def rule_checks(t):
    issues = []
    if not ("09:20" <= t["entry_time"] <= "15:00"):
        issues.append("entry outside 09:20–15:00")
    if t["exit_time"] > "15:29":
        issues.append("exit after 15:29")
    if t["brain"].startswith("VWAP") and t["entry_time"] < "09:45":
        issues.append("VWAP entry before 09:45")
    return "; ".join(issues) or "ok"


def stats(nets):
    n = len(nets)
    if not n:
        return {"n": 0}
    wins = [x for x in nets if x > 0]; losses = [x for x in nets if x <= 0]; mu = st.mean(nets); sd = st.pstdev(nets) if n > 1 else 0.0
    eq = CAPITAL; peak = eq; dd = 0.0
    for x in nets:
        eq += x; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    return {"n": n, "win_rate": round(len(wins) / n, 3), "avg_win": round(st.mean(wins)) if wins else 0, "avg_loss": round(st.mean(losses)) if losses else 0, "expectancy": round(mu), "net": round(sum(nets)), "t": round(mu / (sd / math.sqrt(n)), 2) if sd and n > 1 else None,
            "profit_factor": round(sum(wins) / abs(sum(losses)), 2) if losses and sum(losses) else None, "max_dd_pct": round(100 * dd, 1), "best": round(max(nets)), "worst": round(min(nets))}


def read_notes(md_p):
    notes = {}
    if md_p.exists():
        cur = None
        for line in md_p.read_text().splitlines():
            m = re.match(r"^## Session \d+ — (\d{4}-\d{2}-\d{2})", line)
            if m:
                cur = m.group(1); continue
            if cur and line.startswith("> ") and not line.startswith("> _"):
                notes.setdefault(cur, []).append(line[2:])
    return notes


def load_regimes():
    p = PAPER / "REGIMES.csv"
    return {r["date"]: r for r in csv.DictReader(open(p))} if p.exists() else {}


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--note", nargs=2, metavar=("DATE", "TEXT")); a = ap.parse_args()
    import subprocess; subprocess.run([sys.executable, str(ROOT / "tools" / "regime.py")], capture_output=True, timeout=300); subprocess.run([sys.executable, str(ROOT / "tools" / "fk48_log.py")], capture_output=True, timeout=300); regimes = load_regimes()
    md_p = PAPER / "JOURNAL.md"; csv_p = PAPER / "journal.csv"; notes = read_notes(md_p)
    if a.note:
        notes.setdefault(a.note[0], []).append(a.note[1].strip())
    trades = load_trades(); cache = {}; rows = []
    for t in trades:
        x = enrich(t, bars_for_day(t["date"], cache)); rows.append({**t, **x})
    with open(csv_p, "w", newline="") as fh:
        cols = ["date", "brain", "entry_time", "leg", "strike", "lot", "index_entry", "stop_at_entry", "target", "opt_entry", "exit_time", "exit_reason", "index_exit", "opt_exit", "index_move_pts", "risk_pts", "r_multiple", "mfe_pts", "mae_pts", "mfe_R", "mae_R", "hold_min", "premium_in", "option_change_pct", "gross_inr", "cost_inr", "net_inr", "equity_after", "expiry_day", "rule_checks", "note"]
        w = csv.DictWriter(fh, fieldnames=cols, extrasaction="ignore"); w.writeheader()
        for r in rows:
            w.writerow({**r, "date": r["date"].isoformat()})
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["date"]].append(r)
    days = sorted(by_day); all_nets = [r["net"] for r in rows]; brains = sorted({r["brain"] for r in rows})
    L = ["# Trading journal — paper run (NIFTY weekly options, ₹50,000, no orders placed)", f"_Rebuilt {datetime.now().strftime('%Y-%m-%d %H:%M')} from paper/TRADES_*.csv. Trader's notes are kept; add one with `python3 tools/journal.py --note DATE \"text\"` or from the dashboard._", "",
         "## Running statistics", "", "| scope | trades | win rate | avg win | avg loss | expectancy / trade | profit factor | t | net ₹ | max DD |", "|---|---|---|---|---|---|---|---|---|---|"]
    def srow(name, nets):
        s = stats(nets)
        return f"| {name} | {s['n']} | {s.get('win_rate', '—')} | {s.get('avg_win', '—')} | {s.get('avg_loss', '—')} | {s.get('expectancy', '—')} | {s.get('profit_factor', '—')} | {s.get('t', '—')} | {s.get('net', 0):+,} | {s.get('max_dd_pct', '—')}% |" if s["n"] else f"| {name} | 0 | | | | | | | | |"
    L.append(srow("ALL", all_nets))
    for b in brains:
        L.append(srow(b, [r["net"] for r in rows if r["brain"] == b]))
    L += ["", f"Go-live rule: ≥ 60 trades, net > 0, t ≥ 2 per brain. Sessions so far: {len(days)} of 15 planned.", "", "## Equity by session (with the session's mood)", "", "| # | date | mood | vol | gap % | first-30 range | trades | day net ₹ | equity | " + " | ".join(brains) + " |", "|---|---|---|---|---|---|---|---|---|" + "---|" * len(brains)]
    eq = CAPITAL
    for i, d in enumerate(days, 1):
        dn = sum(r["net"] for r in by_day[d]); eq += dn; per = {b: sum(r["net"] for r in by_day[d] if r["brain"] == b) for b in brains}; rg = regimes.get(d.isoformat(), {})
        L.append(f"| {i} | {d}{' (expiry)' if expiry_flags(d).get('nifty_weekly_expiry') else ''} | {rg.get('day_type', '?')} | {rg.get('vol_bucket', '?')} | {rg.get('gap_pct', '?')} | {rg.get('first30_range', '?')} | {len(by_day[d])} | {dn:+,.0f} | {eq:,.0f} | " + " | ".join(f"{per.get(b, 0):+,.0f}" for b in brains) + " |")
    L += ["", "## Brain × mood (net ₹ / trades) — which brain earns in which conditions", "", "| brain | " + " | ".join(("TREND_UP", "TREND_DOWN", "MIXED", "RANGE", "expiry days", "LOW vol", "HIGH vol")) + " |", "|---|" + "---|" * 7]
    for b in brains:
        cells = []
        for key, fn in (("TREND_UP", lambda rg: rg.get("day_type") == "TREND_UP"), ("TREND_DOWN", lambda rg: rg.get("day_type") == "TREND_DOWN"), ("MIXED", lambda rg: rg.get("day_type") == "MIXED"), ("RANGE", lambda rg: rg.get("day_type") == "RANGE"), ("expiry", lambda rg: str(rg.get("expiry_day")) == "1"), ("LOW", lambda rg: rg.get("vol_bucket") == "LOW"), ("HIGH", lambda rg: rg.get("vol_bucket") == "HIGH")):
            tr = [r for r in rows if r["brain"] == b and fn(regimes.get(r["date"].isoformat(), {}))]
            cells.append(f"{sum(r['net'] for r in tr):+,.0f} / {len(tr)}" if tr else "—")
        L.append(f"| {b} | " + " | ".join(cells) + " |")
    L += ["", "## Patterns so far", ""]
    for name, key in (("by exit reason", "exit_reason"), ("by brain", "brain"), ("by entry hour", lambda r: r["entry_time"][:2] + ":xx"), ("by side", "leg")):
        g = defaultdict(list)
        for r in rows:
            g[key(r) if callable(key) else r[key]].append(r["net"])
        L.append(f"- **{name}:** " + "; ".join(f"{k} {len(v)} trades, {sum(1 for x in v if x > 0)} wins, net {sum(v):+,.0f}" for k, v in sorted(g.items())))
    mfes = [r["mfe_R"] for r in rows if r["mfe_R"] is not None]
    if mfes:
        L.append(f"- **excursions:** median best point {st.median(mfes):.2f} R, median worst {st.median([r['mae_R'] for r in rows if r['mae_R'] is not None]):.2f} R; trades that reached +1 R {sum(1 for x in mfes if x >= 1)}/{len(mfes)}")
    for i, d in enumerate(days, 1):
        tr = by_day[d]; dn = sum(r["net"] for r in tr)
        L += ["", f"## Session {i} — {d}{' — weekly expiry' if expiry_flags(d).get('nifty_weekly_expiry') else ''}", "", f"Day net ₹{dn:+,.0f} · {len(tr)} trades · " + " · ".join(f"{b}: {sum(r['net'] for r in tr if r['brain'] == b):+,.0f}" for b in brains if any(r['brain'] == b for r in tr)), "",
              "| brain | entry | leg | strike | premium | index in → out | move | R | best / worst (R) | hold | exit | option Δ | net ₹ | rule check | note |", "|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for r in tr:
            L.append(f"| {r['brain']} | {r['entry_time']} | {r['leg']} | {r['strike']} | ₹{r['opt_entry']} (₹{r['premium_in']:,}) | {float(r['index_entry']):.0f} → {float(r['index_exit']):.0f} | {float(r['index_move_pts']):+.0f} | {r['r_multiple']} | {r['mfe_R']} / {r['mae_R']} | {r['hold_min']} m | {r['exit_reason']} | {r['option_change_pct']:+}% | {r['net']:+,.0f} | {r['rule_checks']} | {r.get('note', '')} |")
        auto = []
        w = [r for r in tr if r["net"] > 0]
        auto.append(f"{len(w)} of {len(tr)} trades profitable; largest {max(tr, key=lambda r: r['net'])['brain']} {max(r['net'] for r in tr):+,.0f}, smallest {min(r['net'] for r in tr):+,.0f}.")
        slow = [r for r in tr if r["exit_reason"] == "STOP" and r["mfe_R"] is not None and r["mfe_R"] >= 1]
        if slow:
            auto.append(f"{len(slow)} stop(s) after being ≥ 1 R in profit: " + ", ".join(f"{r['brain']} {r['entry_time']} (best {r['mfe_R']} R)" for r in slow) + ".")
        rb = [r for r in tr if r["rule_checks"] != "ok"]
        auto.append("Rule adherence: " + ("all trades within the rules." if not rb else "; ".join(f"{r['brain']} {r['entry_time']}: {r['rule_checks']}" for r in rb)))
        L += ["", "**Auto-analysis.** " + " ".join(auto), "", "**Trader's notes**", ""]
        L += [f"> {n}" for n in notes.get(d.isoformat(), [])] or ["> _none yet_"]
    md_p.write_text("\n".join(L) + "\n"); print(f"journal: {len(rows)} trades over {len(days)} sessions → {md_p.name}, {csv_p.name}")


if __name__ == "__main__":
    main()
