#!/usr/bin/env python3
"""Local control dashboard for the TradingAlgo knowledge base (stdlib only; binds to 127.0.0.1 only; nothing is published).
  python3 tools/dashboard.py [--port 8765]   → open http://127.0.0.1:8765
Panels: status (token, data, paper trader), token (verify / auto-renew / save pasted token / TOTP credentials / launchd), data (fetch new sessions), backtests (run a brain backtest over any
window with any variant; list and view reports), paper trading (start / stop the two-brain trader with options, live log, today's trades), Telegram (setup / test), catalogue comparison, resources (CPU/RAM of the algo processes and the machine; /api/resources)."""
from __future__ import annotations

import argparse, csv, json, os, re, signal, subprocess, sys, threading, time, urllib.parse
from datetime import date, datetime
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]; PAPER = ROOT / "paper"; LOGS = PAPER / "logs"; LOGS.mkdir(parents=True, exist_ok=True)
PY = sys.executable; JOBS: dict[str, dict] = {}; LOCK = threading.Lock()
TOKEN_FILE = Path.home() / ".dhan" / "access_token"; CRED_FILE = Path.home() / ".dhan" / "credentials.json"; TG_FILE = Path.home() / ".dhan" / "telegram.json"


def run_capture(args, timeout=120, env=None):
    try:
        r = subprocess.run(args, cwd=str(ROOT), capture_output=True, text=True, timeout=timeout, env={**os.environ, **(env or {})})
        return r.returncode, (r.stdout + r.stderr)[-6000:]
    except subprocess.TimeoutExpired:
        return 124, "timed out"


def start_job(name, args, log_name, env=None):
    log_p = LOGS / log_name; fh = open(log_p, "a"); fh.write(f"\n==== {datetime.now().isoformat(timespec='seconds')} {' '.join(args)}\n"); fh.flush()
    p = subprocess.Popen(args, cwd=str(ROOT), stdout=fh, stderr=subprocess.STDOUT, env={**os.environ, **(env or {})})
    with LOCK:
        JOBS[name] = {"pid": p.pid, "proc": p, "log": str(log_p), "args": args, "started": datetime.now().isoformat(timespec="seconds")}
    return p.pid


def job_status():
    out = []
    with LOCK:
        for n, j in JOBS.items():
            rc = j["proc"].poll(); out.append({"name": n, "pid": j["pid"], "running": rc is None, "rc": rc, "log": Path(j["log"]).name, "started": j["started"], "args": " ".join(j["args"][1:])})
    return out


def tail(path, n=60):
    try:
        lines = Path(path).read_text(errors="replace").splitlines(); return "\n".join(lines[-n:])
    except Exception:
        return ""


def _tg_channel():
    try:
        return json.loads(TG_FILE.read_text()).get("channel")
    except Exception:
        return None


def token_status():
    rc, out = run_capture([PY, "tools/dhan_token.py", "verify"], timeout=30)
    try:
        exp = TOKEN_FILE.read_text().strip().split(".")[1]; exp += "=" * (-len(exp) % 4); import base64; e = json.loads(base64.urlsafe_b64decode(exp)).get("exp"); hrs = round((e - time.time()) / 3600, 1) if e else None
    except Exception:
        hrs = None
    plist = Path.home() / "Library/LaunchAgents/com.tradingalgo.dhan-token.plist"
    return {"valid": rc == 0, "detail": out.strip()[-400:], "hours_left": hrs, "credentials_saved": CRED_FILE.exists(), "telegram_saved": TG_FILE.exists(), "telegram_channel": _tg_channel(), "launchd_installed": plist.exists(), "token_log": tail(Path.home() / ".dhan" / "token.log", 8)}


def data_status():
    out = {}
    for f in ("nifty_index_1m_ext.csv", "nifty_atm_week_1m_ext.csv", "nifty_wings_week_1m_ext.csv"):
        p = ROOT / "datasets/raw/dhan" / f
        try:
            last = subprocess.run(["tail", "-1", str(p)], capture_output=True, text=True).stdout[:16]
        except Exception:
            last = "?"
        out[f] = last
    poi = [p.name for p in (ROOT / "datasets/raw/nse/participant_oi").glob("fao_participant_oi_*.csv")]
    dates = sorted(f"{n[23:27]}-{n[21:23]}-{n[19:21]}" for n in poi); out["participant_oi_last"] = dates[-1] if dates else "none"
    return out


def _pids(pattern):
    try:
        out = subprocess.run(["pgrep", "-f", pattern], capture_output=True, text=True).stdout.split()
        return [int(x) for x in out if x.strip() and int(x) != os.getpid()]
    except Exception:
        return []


def paper_pids():
    return [p for p in _pids("tools/paper_trade.py") if p not in live_pids()]


def live_pids():
    return _pids("tools/paper_trade.py --live")


def _stop_pids(pids):
    """SIGTERM, wait up to 3 s, then SIGKILL whatever is still alive. Returns the pids that needed a kill."""
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except Exception:
            pass
    for _ in range(6):
        if not any(_alive(pid) for pid in pids):
            return []
        time.sleep(0.5)
    hard = [pid for pid in pids if _alive(pid)]
    for pid in hard:
        try:
            os.kill(pid, signal.SIGKILL)
        except Exception:
            pass
    return hard


def _alive(pid):
    try:
        os.kill(pid, 0); return True
    except Exception:
        return False


def paper_status():
    d = date.today().isoformat(); st_p = PAPER / f"STATE_{d}.json"; st = json.loads(st_p.read_text()) if st_p.exists() else None
    trades = list(csv.DictReader(open(PAPER / f"TRADES_{d}.csv"))) if (PAPER / f"TRADES_{d}.csv").exists() else []
    running = bool(paper_pids()); feed_running = subprocess.run(["pgrep", "-f", "tools/dhan_feed.py"], capture_output=True).returncode == 0
    return {"date": d, "running": running, "pids": paper_pids(), "feed_running": feed_running, "state": st, "trades": trades, "log": tail(LOGS / f"paper_{d}.log", 40), "sessions": sorted(p.name for p in PAPER.glob("SESSION_*.md"))[-15:]}


RES = {"cur": None, "hist": [], "prev": {}, "lock": threading.Lock()}
IS_LINUX = sys.platform.startswith("linux"); CLK = os.sysconf("SC_CLK_TCK") if hasattr(os, "sysconf") else 100


def _algo_procs():
    """(name, pid) of every process that belongs to the algo: this dashboard, the feed, the paper/live traders and running background jobs."""
    out = [("dashboard", os.getpid())] + [("feed", p) for p in _pids("tools/dhan_feed.py")] + [("paper", p) for p in paper_pids()] + [("live", p) for p in live_pids()]
    with LOCK:
        out += [(f"job:{n}", j["pid"]) for n, j in JOBS.items() if j["proc"].poll() is None]
    seen = set(); return [(n, p) for n, p in out if not (p in seen or seen.add(p))]


def _sys_sample():
    """Returns (cpu_ticks (total, idle) or None, (mem_total_mb, mem_avail_mb), temp_c or None)."""
    if IS_LINUX:
        v = [int(x) for x in open("/proc/stat").readline().split()[1:]]; cpu = (sum(v), v[3] + (v[4] if len(v) > 4 else 0))
        mi = {}
        for ln in open("/proc/meminfo"):
            k, _, rest = ln.partition(":"); mi[k] = int(rest.split()[0]) if rest.split() else 0
        temp = None
        try:
            temp = int(open("/sys/class/thermal/thermal_zone0/temp").read()) / 1000      # Raspberry Pi / most SBCs
        except Exception:
            pass
        return cpu, (mi["MemTotal"] / 1024, mi.get("MemAvailable", mi.get("MemFree", 0)) / 1024), temp
    total = int(subprocess.run(["sysctl", "-n", "hw.memsize"], capture_output=True, text=True).stdout or 0) / 1048576       # macOS
    vm = subprocess.run(["vm_stat"], capture_output=True, text=True).stdout; m = re.search(r"page size of (\d+)", vm); page = int(m.group(1)) if m else 16384
    pg = lambda k: int((re.search(rf"{k}:\s+(\d+)", vm) or [None, "0"])[1])
    return None, (total, (pg("Pages free") + pg("Pages inactive") + pg("Pages speculative")) * page / 1048576), None


def _res_sample():
    """One sample of system CPU/RAM and of each algo process; keeps 15 minutes of history (5 s steps). CPU% is instantaneous on Linux (delta of /proc counters), ps's recent average on macOS."""
    now = time.time(); cpu, (mt, ma), temp = _sys_sample(); prev = RES["prev"]; ncpu = os.cpu_count() or 1
    if cpu:
        pc = prev.get("sys"); prev["sys"] = cpu
        sys_pct = (max(0.0, (cpu[0] - pc[0] - (cpu[1] - pc[1])) / (cpu[0] - pc[0]) * 100) if pc and cpu[0] > pc[0] else None)
    else:
        sys_pct = sum(float(x) for x in subprocess.run(["ps", "-A", "-o", "%cpu="], capture_output=True, text=True).stdout.split()) / ncpu
    procs = _algo_procs(); rows = []
    if procs:
        out = subprocess.run(["ps", "-o", "pid=,rss=,etime=,%cpu=", "-p", ",".join(str(p) for _, p in procs)], capture_output=True, text=True).stdout; by = {}
        for ln in out.splitlines():
            f = ln.split()
            if len(f) >= 4:
                by[int(f[0])] = {"rss_mb": int(f[1]) / 1024, "elapsed": f[2], "cpu_pct": float(f[3])}
        for n, pid in procs:
            r = by.get(pid)
            if not r:
                continue
            if IS_LINUX:
                try:
                    st = open(f"/proc/{pid}/stat").read().rpartition(")")[2].split(); t = (int(st[11]) + int(st[12])) / CLK; pt = prev.get(pid)
                    if pt and now > pt[1]:
                        r["cpu_pct"] = max(0.0, (t - pt[0]) / (now - pt[1]) * 100)
                    prev[pid] = (t, now)
                except Exception:
                    pass
            rows.append({"name": n, "pid": pid, "cpu_pct": round(r["cpu_pct"], 1), "rss_mb": round(r["rss_mb"], 1), "elapsed": r["elapsed"]})
    live = {p for _, p in procs}
    for k in [k for k in prev if isinstance(k, int) and k not in live]:
        prev.pop(k)
    algo_cpu = round(sum(r["cpu_pct"] for r in rows), 1); algo_rss = round(sum(r["rss_mb"] for r in rows), 1)
    try:
        load = [round(x, 2) for x in os.getloadavg()]
    except Exception:
        load = None
    cur = {"t": now, "sys_cpu": None if sys_pct is None else round(sys_pct, 1), "ncpu": ncpu, "load": load, "mem_total_mb": round(mt), "mem_used_mb": round(mt - ma), "temp_c": temp, "algo_cpu": algo_cpu, "algo_rss_mb": algo_rss, "procs": rows, "platform": "linux" if IS_LINUX else sys.platform}
    with RES["lock"]:
        RES["cur"] = cur; RES["hist"].append([int(now), cur["sys_cpu"], algo_cpu, round(algo_rss)]); del RES["hist"][:-180]


def _res_loop():
    while True:
        try:
            _res_sample()
        except Exception:
            pass
        time.sleep(5)


def resources():
    with RES["lock"]:
        cur = RES["cur"]; hist = list(RES["hist"])
    if cur is None:
        try:
            _res_sample(); time.sleep(0.5); _res_sample()
        except Exception as e:
            return {"error": str(e)[:200]}
        with RES["lock"]:
            cur = RES["cur"]; hist = list(RES["hist"])
    return {**cur, "hist": hist}


CHART_CACHE = {"t": 0, "data": None}
FUNDS_CACHE = {"t": 0.0, "v": None}



def live_status():
    """State of the LIVE trader (real orders): running pids, today's live state/trades from paper/live, week P&L."""
    d = date.today().isoformat(); live_dir = ROOT / "paper" / "live"; st = None; trades = []
    try:
        sp = live_dir / f"STATE_{d}.json"; st = json.loads(sp.read_text()) if sp.exists() else None
        tp = live_dir / f"TRADES_{d}.csv"
        if tp.exists():
            with open(tp, newline="") as fh:
                trades = list(csv.DictReader(fh))
    except Exception:
        pass
    week = 0.0
    try:
        from live_broker import LiveGuard
        week = LiveGuard(live_dir, date.today()).week_realised()
    except Exception:
        pass
    funds = None
    try:                                                                   # Dhan available funds, cached 30 s (the live trader's capital is read from here, not from config)
        if time.time() - FUNDS_CACHE["t"] > 30:
            from live_broker import _call
            FUNDS_CACHE["v"] = float(_call("GET", "/fundlimit").get("availabelBalance") or 0.0); FUNDS_CACHE["t"] = time.time()
        funds = FUNDS_CACHE["v"]
    except Exception:
        pass
    return {"running": bool(live_pids()), "pids": live_pids(), "date": d, "state": st, "trades": trades, "week_realised": week, "funds": funds, "log": str(LOGS / f"live_{d}.log")}

def chart_data(tf=1):
    """Today's NIFTY bars (from the paper trader's LIVE snapshot if fresh, else fetched from Dhan at most once a minute), plus VWAP, EMA9, pivots, trades and open positions."""
    import time as _t
    sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / "tools"))
    from engine.ohlc import Candle; from engine.emavwap import session_twap, kbars, ema; from engine.pivots import daily_pivots
    from datetime import timedelta; from zoneinfo import ZoneInfo
    import paper_trade as pt
    d = date.today(); live_p = PAPER / f"LIVE_{d}.json"; bars = None; src = ""; forming = None; quotes = None; ltp = None
    feed_p = PAPER / f"FEED_{d}.json"
    if feed_p.exists():
        try:
            j = json.loads(feed_p.read_text()); age = (datetime.now(ZoneInfo("Asia/Kolkata")) - datetime.fromisoformat(j["written"])).total_seconds()
            if age < 15:
                IST = ZoneInfo("Asia/Kolkata"); bars = [Candle(datetime.combine(d, datetime.strptime(t, "%H:%M").time(), tzinfo=IST), o, h, l, c, timedelta(minutes=1)) for t, o, h, l, c in j["bars"]]
                forming = j.get("forming"); quotes = {"atm": j.get("atm"), "expiry": j.get("expiry"), "options": j.get("options")}; ltp = j.get("ltp"); src = f"live feed ({j.get('ltt')})"
        except Exception:
            bars = None
    if bars is None and live_p.exists():
        try:
            j = json.loads(live_p.read_text()); age = (datetime.now() - datetime.fromisoformat(j["written"]).replace(tzinfo=None)).total_seconds()
            if age < 150:
                IST = ZoneInfo("Asia/Kolkata"); bars = [Candle(datetime.combine(d, datetime.strptime(t, "%H:%M").time(), tzinfo=IST), o, h, l, c, timedelta(minutes=1)) for t, o, h, l, c in j["bars"]]; src = f"paper trader snapshot ({int(age)} s old)"
        except Exception:
            bars = None
    if bars is None:
        if CHART_CACHE["data"] and _t.time() - CHART_CACHE["t"] < 60:
            return CHART_CACHE["data"]
        try:
            bars = pt.bars_for(d); src = "fetched from Dhan"
        except Exception as e:
            return {"error": f"no live data: {str(e)[:80]}", "date": d.isoformat()}
    if not bars:
        return {"error": "no bars yet today", "date": d.isoformat()}
    prev = pt.prev_session_bars(d); series = prev + bars; tw = session_twap(bars)
    bk = kbars(series, tf) if tf > 1 else series; e9 = ema([b.close for b in bk], 9); pv = None
    if prev:                                                               # today's daily Fibonacci pivots straight from the previous session's H/L/C
        pH = max(b.high for b in prev); pL = min(b.low for b in prev); pC = prev[-1].close; P = (pH + pL + pC) / 3; rng = pH - pL
        pv = {"P": P, "R1": P + 0.382 * rng, "R2": P + 0.618 * rng, "S1": P - 0.382 * rng, "S2": P - 0.618 * rng}
    out_bars = []; today_idx = [i for i, b in enumerate(bk) if b.timestamp.date() == d]
    for i in today_idx:
        b = bk[i]; out_bars.append({"t": b.timestamp.strftime("%H:%M"), "o": b.open, "h": b.high, "l": b.low, "c": b.close, "ema9": round(e9[i], 2)})
    vw = {b.timestamp.strftime("%H:%M"): round(tw[i], 2) for i, b in enumerate(bars)}
    trades = list(csv.DictReader(open(PAPER / f"TRADES_{d}.csv"))) if (PAPER / f"TRADES_{d}.csv").exists() else []
    st_p = PAPER / f"STATE_{d}.json"; st = json.loads(st_p.read_text()) if st_p.exists() else None
    opens = [{"brain": n, **{k: b["open"][k] for k in ("entry_time", "leg", "strike", "index_entry", "stop", "target", "p_in", "last_px")}} for n, b in (st["brains"].items() if st else []) if b.get("open")]
    data = {"date": d.isoformat(), "tf": tf, "source": src, "bars": out_bars, "vwap": vw, "pivots": {k: round(v, 1) for k, v in pv.items()} if pv else None, "trades": trades, "open": opens, "last": {"t": bars[-1].timestamp.strftime("%H:%M"), "c": bars[-1].close},
            "forming": forming, "quotes": quotes, "ltp": ltp, "feed": forming is not None}
    if not data["feed"]:
        CHART_CACHE["t"] = _t.time(); CHART_CACHE["data"] = data
    return data


def backtest_list():
    rows = []
    for p in sorted(PAPER.glob("BACKTEST_*.md"), key=lambda x: x.stat().st_mtime, reverse=True):
        m = re.search(r"\*\*Result:\*\* (.*)", p.read_text(errors="replace"))
        rows.append({"file": p.name, "result": (m.group(1) if m else "")[:400], "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="minutes")})
    cat = ROOT / "knowledge/SETUP_CATALOGUE_NIFTY.md"
    return {"backtests": rows, "catalogue_exists": cat.exists()}


HTML = r"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>TradingAlgo Console</title>
<style>
:root{--bg:#0b0f14;--panel:#121820;--panel2:#171f2a;--line:#232e3c;--text:#e8edf3;--muted:#8fa0b3;--accent:#3b82f6;--accent2:#2563eb;--ok:#22c55e;--warn:#f59e0b;--bad:#ef4444;--radius:12px}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif}
.app{display:grid;grid-template-columns:230px 1fr;min-height:100vh}
aside{background:var(--panel);border-right:1px solid var(--line);padding:18px 14px;position:sticky;top:0;height:100vh}
.brand{display:flex;align-items:center;gap:10px;padding:4px 8px 18px}.brand .dot{width:12px;height:12px;border-radius:50%;background:var(--accent);box-shadow:0 0 14px var(--accent)}
.brand b{font-size:15px}.brand small{display:block;color:var(--muted);font-size:11px}
.nav a{display:flex;align-items:center;gap:10px;padding:10px 12px;border-radius:10px;color:var(--muted);text-decoration:none;cursor:pointer;margin-bottom:4px}
.nav a:hover{background:var(--panel2);color:var(--text)}.nav a.on{background:rgba(59,130,246,.15);color:#dbeafe;border:1px solid rgba(59,130,246,.35)}
.nav .ic{width:22px;text-align:center;font-size:15px}
.side-foot{position:absolute;bottom:16px;left:14px;right:14px;color:var(--muted);font-size:11px;line-height:1.4}
main{padding:22px 28px;max-width:1360px}
.topbar{display:flex;justify-content:space-between;align-items:center;margin-bottom:18px}.topbar h1{font-size:20px;margin:0}.topbar .sub{color:var(--muted);font-size:12px}
.pill{display:inline-flex;align-items:center;gap:6px;padding:4px 10px;border-radius:999px;font-size:12px;border:1px solid var(--line);background:var(--panel2)}
.pill.ok{color:#bbf7d0;border-color:rgba(34,197,94,.4)}.pill.bad{color:#fecaca;border-color:rgba(239,68,68,.4)}.pill.warn{color:#fde68a;border-color:rgba(245,158,11,.4)}.pill.muted{color:var(--muted)}
.grid{display:grid;gap:14px}.g4{grid-template-columns:repeat(4,1fr)}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:2fr 1fr}
@media(max-width:1100px){.g4{grid-template-columns:repeat(2,1fr)}.g2,.g3{grid-template-columns:1fr}}
.card{background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);padding:16px 18px;margin-bottom:14px}
.card h3{margin:0 0 10px;font-size:15px;display:flex;align-items:center;gap:8px}.card h3 .tag{font-size:11px;color:var(--muted);font-weight:normal}
.kpi{background:linear-gradient(180deg,var(--panel2),var(--panel));border:1px solid var(--line);border-radius:var(--radius);padding:14px 16px}
.kpi .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.4px}.kpi .v{font-size:22px;font-weight:600;margin:4px 0 2px}.kpi .d{font-size:12px;color:var(--muted)}
.help{background:rgba(59,130,246,.08);border:1px solid rgba(59,130,246,.25);border-radius:10px;padding:10px 14px;color:#cfe0ff;font-size:13px;margin:8px 0 12px}
.help b{color:#fff}.help ol,.help ul{margin:6px 0 0 18px;padding:0}.help li{margin:2px 0}
.row{display:flex;gap:10px;flex-wrap:wrap;align-items:center;margin:8px 0}
label.f{display:flex;flex-direction:column;gap:4px;font-size:12px;color:var(--muted)}label.f input,label.f select{min-width:120px}
input,select{background:#0e141b;color:var(--text);border:1px solid var(--line);border-radius:8px;padding:8px 10px;font-size:13px}input:focus,select:focus{outline:1px solid var(--accent)}
.chk{display:inline-flex;align-items:center;gap:6px;background:#0e141b;border:1px solid var(--line);border-radius:8px;padding:7px 10px;font-size:13px;cursor:pointer}
.btn{background:var(--accent);color:#fff;border:0;padding:9px 14px;border-radius:9px;cursor:pointer;font-size:13px;font-weight:500;display:inline-flex;align-items:center;gap:6px}
.btn:hover{background:var(--accent2)}.btn.ghost{background:var(--panel2);color:var(--text);border:1px solid var(--line)}.btn.danger{background:var(--bad)}.btn.ok{background:#16a34a}.btn:disabled{opacity:.5;cursor:default}
.btn.sm{padding:5px 10px;font-size:12px}
table{border-collapse:collapse;width:100%;font-size:13px}th{color:var(--muted);font-weight:500;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.3px}
td,th{border-bottom:1px solid var(--line);padding:8px 8px;vertical-align:top}tr:hover td{background:rgba(255,255,255,.02)}
pre{background:#0a0e13;border:1px solid var(--line);border-radius:10px;padding:12px;max-height:380px;overflow:auto;font-size:12px;white-space:pre-wrap;line-height:1.45;margin:8px 0 0}
.mono{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}.muted{color:var(--muted)}.ok{color:var(--ok)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.steps{display:grid;grid-template-columns:repeat(4,1fr);gap:10px}@media(max-width:1100px){.steps{grid-template-columns:1fr 1fr}}
.step{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:12px}.step .n{display:inline-block;width:22px;height:22px;border-radius:50%;background:var(--accent);color:#fff;text-align:center;font-size:12px;line-height:22px;margin-right:6px}
.step h4{margin:0 0 6px;font-size:13px}.step p{margin:0 0 8px;color:var(--muted);font-size:12px}
.toasts{position:fixed;right:18px;bottom:18px;display:flex;flex-direction:column;gap:8px;z-index:50}.toast{background:#1c2633;border:1px solid var(--line);border-left:4px solid var(--accent);border-radius:10px;padding:10px 14px;min-width:280px;max-width:460px;font-size:13px;box-shadow:0 8px 30px rgba(0,0,0,.4)}
.toast.ok{border-left-color:var(--ok)}.toast.bad{border-left-color:var(--bad)}.toast.warn{border-left-color:var(--warn)}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.55);display:none;align-items:center;justify-content:center;z-index:60}.modal.on{display:flex}.modal .box{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:20px;width:460px}
.modal h3{margin:0 0 8px}.modal p{color:var(--muted);margin:0 0 14px}
section{display:none}section.on{display:block}.spin{display:inline-block;width:12px;height:12px;border:2px solid #fff;border-right-color:transparent;border-radius:50%;animation:s .7s linear infinite}@keyframes s{to{transform:rotate(360deg)}}
.badge{font-size:11px;padding:2px 7px;border-radius:6px;background:var(--panel2);border:1px solid var(--line);color:var(--muted)}
.result-line{font-size:12.5px}.result-line b{color:#fff}
.meter{height:6px;background:var(--line);border-radius:3px;margin-top:6px;overflow:hidden}.meter i{display:block;height:100%;background:var(--accent);border-radius:3px;transition:width .4s}.meter i.warn{background:var(--warn)}.meter i.bad{background:var(--bad)}
.rgrid{display:grid;grid-template-columns:repeat(4,1fr);gap:10px;margin-bottom:10px}@media(max-width:1100px){.rgrid{grid-template-columns:1fr 1fr}}.rtile{background:var(--panel2);border:1px solid var(--line);border-radius:10px;padding:10px 12px}.rtile .l{color:var(--muted);font-size:11px;text-transform:uppercase;letter-spacing:.4px}.rtile .v{font-size:20px;font-weight:600;margin-top:2px}.rtile .d{font-size:11px;color:var(--muted)}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);margin:6px 0 2px}.legend i{display:inline-block;width:10px;height:3px;border-radius:2px;vertical-align:middle;margin-right:5px}
</style></head><body>
<div class="app"><aside>
<div class="brand"><div class="dot"></div><div><b>TradingAlgo</b><small>local console · NIFTY</small></div></div>
<div class="nav" id="nav"></div>
<div class="side-foot">Runs only on this machine (127.0.0.1). No orders are ever placed. Nothing is published.<br><br><span id="clock"></span></div>
</aside><main>
<div class="topbar"><div><h1 id="ttl">Overview</h1><div class="sub" id="sub">Daily routine and system health</div></div><div class="row" id="toppills"></div></div>

<section id="overview" class="on">
<div class="grid g4" id="kpis"></div>
<div class="card" style="margin-top:14px"><h3>Daily routine <span class="tag">what to do, in order</span></h3>
<div class="steps">
<div class="step"><h4><span class="n">1</span>Before 09:15</h4><p>Check the token is valid (the 08:30 agent usually handles it). If it shows invalid, press <b>Auto renew</b> on the Token page.</p><button class="btn sm" onclick="go('token')">Open Token</button></div>
<div class="step"><h4><span class="n">2</span>09:15 — start paper trading</h4><p>PIVOT (2 strikes in the money, cap ₹15,000) and EXPIRY (fires only on expiry days at 13:00). Leave it running until 15:30.</p><button class="btn sm ok" onclick="go('paper')">Open Paper trading</button></div>
<div class="step"><h4><span class="n">3</span>During the day</h4><p>Watch the live log or your Telegram. Do not change settings mid-session; a bad day under fixed rules is data.</p><button class="btn sm ghost" onclick="go('paper')">View live log</button></div>
<div class="step"><h4><span class="n">4</span>After 15:30</h4><p>Read the session report. The 20:30 agent fetches the day's data; backfill wings if you want to backtest that day.</p><button class="btn sm ghost" onclick="go('backtest')">Open Backtests</button></div>
</div></div>
<div class="card"><h3>Resources <span class="tag">CPU and RAM used by the algo processes · updated every 30 s, sampled every 5 s</span></h3><div class="rgrid" id="rtiles"></div><div class="legend"><span><i style="background:#3b82f6"></i>algo processes (sum, % of one core)</span><span><i style="background:#d97706"></i>whole machine (% of all cores)</span><span id="rspan" style="margin-left:auto"></span></div><canvas id="rchart" style="width:100%;height:120px;display:block"></canvas><div id="rprocs" style="margin-top:10px"></div></div>
<div class="grid g2"><div class="card"><h3>System health</h3><div id="health"></div></div><div class="card"><h3>Recent activity <span class="tag">token log</span></h3><pre id="tokenlog" style="max-height:220px"></pre></div></div>
</section>

<section id="token">
<div class="help"><b>How the token works.</b> Dhan tokens last 24 hours. The launchd agent runs <b>Auto renew</b> at 08:30 and 20:30. Use the buttons here only when the status pill is red.
<ol><li><b>Verify</b> asks Dhan whether the saved token is still accepted.</li><li><b>Auto renew / regenerate</b> renews it if it is close to expiry, or generates a new one with your saved PIN and TOTP secret.</li><li>If automation fails, generate a token on web.dhan.co and paste it in <b>Save a token</b>.</li></ol></div>
<div class="grid g2"><div class="card"><h3>Token status</h3><div id="tokstat"></div><div class="row"><button class="btn" onclick="act('/api/token/verify','Verifying token…')">Verify</button><button class="btn ok" onclick="act('/api/token/auto','Renewing or regenerating…')">Auto renew / regenerate</button><button class="btn ghost" onclick="confirmThen('Install the launchd agent?','It will run the token check at 08:30 and 20:30 every day and fetch data after the evening run.',()=>act('/api/token/launchd','Installing agent…'))">Install launchd agent</button></div></div>
<div class="card"><h3>Save a token generated on web.dhan.co</h3><p class="muted" style="margin:0 0 8px">My Profile → Access DhanHQ APIs → Generate. Paste it below; it is stored in ~/.dhan with owner-only permissions and verified immediately.</p>
<div class="row"><input id="tokpaste" type="password" placeholder="paste access token" style="flex:1;min-width:300px"><button class="btn" onclick="postAct('/api/token/save',{token:v('tokpaste')},'Saving token…')">Save token</button></div></div></div>
<div class="card"><h3>TOTP credentials <span class="tag">one-time, enables automatic generation</span></h3><p class="muted" style="margin:0 0 8px">On web.dhan.co enable TOTP under Access DhanHQ APIs and copy the base32 secret. Fields are masked and never echoed back.</p>
<div class="row"><label class="f">Client ID<input id="cid" placeholder="Dhan client ID"></label><label class="f">PIN<input id="pin" type="password"></label><label class="f">TOTP secret<input id="totp" type="password" style="min-width:280px"></label><button class="btn" style="align-self:flex-end" onclick="postAct('/api/token/credentials',{cid:v('cid'),pin:v('pin'),totp:v('totp')},'Saving credentials…')">Save credentials</button></div></div>
<div class="card"><h3>Output</h3><pre id="tokout" class="mono">—</pre></div>
</section>

<section id="data">
<div class="help"><b>Data.</b> The evening agent appends each new session to the extension files. Use <b>Fetch new sessions</b> only to catch up manually. <b>Backfill wings</b> adds the ATM±3 option rows for a date range; backtests that include a day need them to price held strikes after a roll.</div>
<div class="grid g2"><div class="card"><h3>Coverage</h3><div id="datcov"></div></div>
<div class="card"><h3>Actions</h3><div class="row"><label class="f">As of<input id="asof" type="date"></label><button class="btn" style="align-self:flex-end" onclick="confirmThen('Fetch new sessions?','Appends sessions after the last stored one up to the chosen date, then runs the monitor update. Takes 1–3 minutes.',()=>postAct('/api/data/fetch',{asof:v('asof')},'Fetch started'))">Fetch new sessions</button></div>
<div class="row"><label class="f">Wings from<input id="wa" type="date"></label><label class="f">to<input id="wb" type="date"></label><button class="btn ghost" style="align-self:flex-end" onclick="confirmThen('Backfill NIFTY wing rows?','Downloads ATM±1..3 option rows for the range (about 1 minute per week of data).',()=>postAct('/api/data/wings',{a:v('wa'),b:v('wb')},'Backfill started'))">Backfill wings</button></div></div></div>
<div class="card"><h3>Job log</h3><pre id="datlog" class="mono">—</pre></div>
</section>

<section id="backtest">
<div class="help"><b>Backtests run the brain exactly as the paper trader does</b> — one position at a time, max 4 entries a day, option fills with costs. Pick a window and a variant; the job runs in the background (a quarter ≈ 1 minute, the 22-month development window ≈ 8 minutes). Reports appear below with their headline result; <b>View</b> shows every trade.
<ul><li><b>Development</b> 2023-09-11 → 2025-06-30 is the long record; <b>last quarter</b> 2026-07-06 → today is the recent one. Judge on both.</li><li>Variants: ITM 2 strikes = V1 expression; break-even at 1R; confirmation bar; trailing stop (e.g. <span class="mono">1.0</span>, <span class="mono">1.0nt</span> no target, <span class="mono">0.5@1</span> after 1R).</li></ul></div>
<div class="card"><h3>Run a backtest</h3><div class="row">
<label class="f">Brain<select id="bbrain"><option>PIVOT</option><option>EXPIRY</option><option>VWAP</option></select></label><label class="f">From<input id="ba" type="date"></label><label class="f">To<input id="bb" type="date"></label><label class="f">R:R<input id="brr" value="3" style="width:70px"></label>
<label class="chk"><input type="checkbox" id="bitm"> ITM 2 strikes (V1)</label><label class="chk"><input type="checkbox" id="bbe"> break-even @1R</label><label class="chk"><input type="checkbox" id="bconf"> confirmation bar</label><label class="f">Trail<input id="btrail" placeholder="1.0 / 1.0nt / 0.5@1" style="width:140px"></label>
<button class="btn" style="align-self:flex-end" onclick="runBT()">Run backtest</button><button class="btn ghost sm" style="align-self:flex-end" onclick="preset('dev')">Preset: development</button><button class="btn ghost sm" style="align-self:flex-end" onclick="preset('q')">Preset: last quarter</button></div></div>
<div class="card"><h3>Reports <span class="tag" id="btcount"></span></h3><div id="btlist"></div></div>
<div class="grid g2"><div class="card"><h3>Report viewer</h3><pre id="btview" class="mono" style="max-height:520px">Select a report above.</pre></div><div class="card"><h3>Catalogue comparison <span class="tag">all setups on NIFTY</span></h3><button class="btn ghost sm" onclick="api('/api/file?name=catalogue').then(t=>el('catview').textContent=t)">Load catalogue</button><pre id="catview" class="mono" style="max-height:480px">—</pre></div></div>
</section>

<section id="paper">
<div class="card" style="border:1px solid #b91c1c;margin-bottom:14px"><h3 style="color:#fca5a5">Live trading — REAL ORDERS on Dhan</h3>
<p class="muted">Runs the PIVOT brain only (2 strikes in the money, 1:3, cap ₹15,000, 1 lot) with real market orders. Separate process and ledger (paper/live/) from the paper trader, which keeps running. Hard limits: 1 lot, max 4 entries, day stop −₹5,000, week brake −₹10,000, flat by 15:15, Dhan INTRADAY product (broker auto square-off as a backstop). Preflight refuses to start on a stale token, insufficient funds, a stray open position or a hit week brake.</p>
<div class="row"><span class="chip" id="livechip">live idle</span><span class="chip" id="liveweek">week ₹0</span><span class="chip" id="livefunds">Dhan funds —</span></div>
<div class="row"><input id="liveconfirm" placeholder="type LIVE to confirm" style="width:200px"><button class="btn danger" id="livestart" onclick="startLive()">💰 Start LIVE trader</button><button class="btn" id="livestop" onclick="confirmThen('Stop the LIVE trader?','If a live position is open it will NOT be closed by stopping — exit it on Dhan yourself.',()=>stopProc('/api/live/stop','Stopping live…'))">■ Stop live</button></div>
<div id="livetrades" class="muted" style="font-size:12px;margin-top:6px">—</div></div>
<div class="help"><b>Paper trading places no orders.</b> It polls Dhan every minute, applies the two brains, and books simulated fills at the option price ± ₹0.50 with brokerage, STT and exchange charges. Start it before 09:15 and leave it until 15:30; it closes everything by 15:15 and writes a session report. Settings are fixed for the 15-session run: PIVOT 2 strikes in the money, cap ₹15,000; EXPIRY brain on from 2026-10-15 (first possible trade 2026-10-19).
<ul><li><b>Stop</b> ends the process; any open paper position is simply dropped from the log, so stop only after 15:15 unless something is wrong.</li><li>Restarting during the day is safe: it reloads today's state and never re-enters an old signal.</li></ul></div>
<div class="grid g3"><div class="card"><h3>Controls</h3><div id="papstat"></div>
<div class="row"><label class="chk"><input type="checkbox" id="pvwap"> VWAP brain (not admitted: negative on 3 and 6 months)</label><label class="chk"><input type="checkbox" id="ppivot" checked> PIVOT brain</label><label class="chk"><input type="checkbox" id="pexpiry" checked> EXPIRY brain (expiry days only, 13:00)</label>
<label class="f">PIVOT ITM strikes<select id="pitm"><option value="2" selected>2 — in the money (V1)</option><option value="0">0 — at the money</option></select></label><label class="f">PIVOT R:R<input id="prr" value="3" style="width:70px"></label><label class="f">VWAP R (pts)<input id="pr" value="7" style="width:70px"></label><label class="f">Premium cap ₹ (0 = none)<input id="pcap" value="15000" style="width:100px"></label></div>
<div class="row"><label class="chk"><input type="checkbox" id="pfeed" checked> use live feed (tick data, fills at LTP)</label></div>
<div class="row"><button class="btn ok" id="startbtn" onclick="startPaper()">▶ Start paper trader</button><button class="btn danger" id="stopbtn" onclick="confirmThen('Stop the paper trader?','Any open paper position will not be closed in the log. Prefer stopping after 15:15.',()=>stopProc('/api/paper/stop','Stopping…'))">■ Stop</button></div></div>
<div class="card"><h3>Telegram alerts</h3><p class="muted" style="margin:0 0 8px">Create a bot with @BotFather, send it one message, paste the bot token here. Your private chat gets everything: signals, warnings and order failures.</p><div class="row"><input id="tgtok" type="password" placeholder="bot token" style="flex:1;min-width:200px"><button class="btn sm" onclick="postAct('/api/telegram/setup',{token:v('tgtok')},'Connecting Telegram…')">Setup</button><button class="btn ghost sm" onclick="act('/api/telegram/test','Sending test…')">Test</button></div>
<p class="muted" style="margin:10px 0 6px"><b>Channel for members</b> (optional): create a channel, add the bot as administrator with <b>Post messages</b>, then enter its @name (public) or -100… id (private). The channel receives only the signals: trader start, ENTRY, EXIT, day stop and the session summary. Current: <span id="tgchan" class="mono">—</span></p><div class="row"><input id="tgchanin" placeholder="@channelname or -100…" style="flex:1;min-width:200px"><button class="btn sm" onclick="postAct('/api/telegram/channel',{channel:v('tgchanin')},'Connecting channel…')">Add channel</button><button class="btn ghost sm" onclick="act('/api/telegram/test_channel','Sending channel test…')">Test channel</button><button class="btn ghost sm" onclick="confirmThen('Remove the channel?','Signals will go to your private chat only.',()=>postAct('/api/telegram/channel',{channel:'off'},'Removing channel…'))">Remove</button></div></div></div>
<div class="card"><h3>Live chart <span class="tag" id="chartsrc"></span><span style="margin-left:auto" class="row"><button class="btn ghost sm" id="tf1" onclick="setTf(1)">1m</button><button class="btn ghost sm" id="tf5" onclick="setTf(5)">5m</button><button class="btn ghost sm" onclick="loadChart()">↻</button></span></h3>
<div class="grid" style="grid-template-columns:3fr 1fr;gap:14px"><div><canvas id="chart" width="1000" height="420" style="width:100%;height:420px;background:#0a0e13;border:1px solid var(--line);border-radius:10px"></canvas><div class="muted" style="font-size:12px;margin-top:6px">Candles = NIFTY spot · <span style="color:#f59e0b">VWAP</span> · <span style="color:#a78bfa">EMA 9</span> · dashed = daily pivots P / R1 / R2 / S1 / S2 · ▲ CE entry · ▼ PE entry · ◆ exit (green win / red loss) · ○ open position with its stop (red) and target (green)</div></div>
<div><h4 style="margin:0 0 6px">Trade feed</h4><div id="feed" style="max-height:200px;overflow:auto;font-size:12.5px"></div><h4 style="margin:10px 0 6px">Option quotes</h4><div id="quotes" style="font-size:12px"></div></div></div></div>
<div class="grid g2"><div class="card"><h3>Today's trades</h3><div id="paptrades" class="muted">none yet</div></div><div class="card"><h3>Open positions</h3><div id="papopen" class="muted">—</div></div></div>
<div class="card"><h3>Live log <span class="tag">refreshes every 30 s</span></h3><pre id="paplog" class="mono">—</pre></div>
<div class="card"><h3>Past sessions</h3><div id="papsess"></div><pre id="papview" class="mono" style="max-height:420px">Select a session.</pre></div>
</section>

<section id="journal"><div class="help"><b>Trading journal.</b> Rebuilt automatically at the end of every live session from the paper trader's records: running statistics, equity by session, patterns, and a per-session page with every trade's R-multiple, best and worst excursion, hold time, option change and a rule check. Add your own note for a date below; notes are kept across rebuilds.</div>
<div class="card"><h3>Add a note</h3><div class="row"><label class="f">Date<input id="jdate" type="date"></label><input id="jtext" placeholder="what you saw, what you felt, what you would do differently" style="flex:1;min-width:300px"><button class="btn" style="align-self:flex-end" onclick="postAct('/api/journal/note',{date:v('jdate'),text:v('jtext')},'Saving note…').then(()=>{el('jtext').value='';loadJournal()})">Save note</button><button class="btn ghost" style="align-self:flex-end" onclick="api('/api/journal/rebuild').then(()=>loadJournal())">Rebuild</button></div></div>
<div class="card"><h3>Journal</h3><pre id="jview" class="mono" style="max-height:720px">—</pre></div></section>
<section id="jobs"><div class="help"><b>Background jobs</b> are backtests, data fetches and the paper trader. Each writes a log in paper/logs. Kill only if a job is stuck.</div><div class="card"><div id="jobs_t"></div></div><div class="card"><h3>Job log</h3><pre id="joblog" class="mono">—</pre></div></section>

<section id="guide"><div class="card"><h3>Guide</h3>
<div class="help"><b>What this console controls.</b> A research engine (44 skills, all verdicts recorded in the knowledge base), a two-brain paper trader, a Dhan data pipeline with automatic token refresh, and a backtester that replays the paper trader's exact logic.</div>
<h4>The brains</h4><ul><li><b>VWAP</b> — Skill 39: after ≥ 20 min on one side of VWAP with ≥ 2 R room, a pullback that touches VWAP and closes on the trend side → buy CE/PE; stop VWAP ∓ R (moves with VWAP); target the run's extreme; 09:45–15:00.</li>
<li><b>PIVOT</b> — Skill 44: on 5-minute bars, EMA 9 crossing a daily Fibonacci pivot (P, R1, R2, S1, S2) → CE on an up-cross, PE on a down-cross; stop at the previous 5m candle's extreme; target 1:3; 09:20–15:00. Expression for the current run: two strikes in the money.</li>
<li><b>EXPIRY</b> — Skill 48 (admitted 2026-10-14): only on NIFTY weekly-expiry days. At the 13:00 close, if the index is ≥ 15 pts from VWAP, buy CE (above) or PE (below), two strikes in the money; stop 25 index pts; no target; exit at 15:15. One trade per expiry day. Six months: 19 trades, +₹11,395; quarter +₹4,194; per-side check passed on the long window. Too few trades yet for a live decision — it runs as an observation brain.</li></ul>
<h4>Account rules</h4><ul><li>Paper: ₹50,000 per session. Live: the capital is the account's available funds read from Dhan at the start of each session (shown on the live card). One lot per trade if premium × lot ≤ cap; one position per brain; max 4 entries per brain per day; combined day stop −₹5,000; everything flat by 15:15; only the just-completed bar's signal is traded.</li></ul>
<h4>The decision rule</h4><ul><li>A brain goes live only after ≥ 60 paper trades with positive net and t ≥ 2. Current run: PIVOT with the in-the-money expression for ≥ 15 sessions from 2026-10-13, then review. No rule changes meanwhile.</li></ul>
<h4>Where things are</h4><ul><li>Session reports: paper/SESSION_&lt;date&gt;.md · Backtests: paper/BACKTEST_*.md · Catalogue: knowledge/SETUP_CATALOGUE_NIFTY.md · Plan: paper/PLAN_TWO_BRAINS.md · Source of truth: PROJECT_STATE.md</li></ul>
<h4>If something goes wrong</h4><ul><li>Token red → Token page → Auto renew; if that fails, paste a token from web.dhan.co.</li><li>Paper trader shows "fetch error" lines → Dhan is slow; it retries every minute on its own.</li><li>No signals all day → normal for the VWAP brain on trend days and for PIVOT on quiet days (2–4 signals a day on average).</li></ul></div></section>
</main></div>
<div class="toasts" id="toasts"></div>
<div class="modal" id="modal"><div class="box"><h3 id="mt"></h3><p id="mp"></p><div class="row" style="justify-content:flex-end"><button class="btn ghost" onclick="closeModal()">Cancel</button><button class="btn" id="mok">Confirm</button></div></div></div>
<script>
const TABS=[["overview","⌂","Overview","Daily routine and system health"],["paper","▶","Paper trading","Start, watch and review the two-brain paper trader"],["backtest","⟲","Backtests","Run and read brain backtests"],["data","▤","Data","Coverage, fetch and wings backfill"],["token","⚿","Token","Dhan access token and automation"],["journal","✎","Journal","Trade-by-trade record with your notes"],["jobs","≡","Jobs","Background processes and logs"],["guide","?","Guide","What everything is and how to use it"]];
const el=id=>document.getElementById(id);const v=id=>el(id).value;const esc=s=>String(s??'').replace(/[&<>]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));
const api=u=>fetch(u).then(r=>r.json());const post=(u,b)=>fetch(u,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(b)}).then(r=>r.json());
function toast(msg,kind='',ms=5000){const t=document.createElement('div');t.className='toast '+kind;t.textContent=msg;el('toasts').appendChild(t);setTimeout(()=>t.remove(),ms)}
let pending=null;function confirmThen(title,text,fn){el('mt').textContent=title;el('mp').textContent=text;pending=fn;el('modal').classList.add('on');el('mok').onclick=()=>{closeModal();fn()}}function closeModal(){el('modal').classList.remove('on')}
function fmtOut(d){if(typeof d==='string')return d;if(d.out!==undefined)return (d.rc===0?'✔ ok\n':'✖ rc '+d.rc+'\n')+d.out;return JSON.stringify(d,null,1)}
function act(u,msg){toast(msg,'',2500);return api(u).then(d=>{const ok=d.rc===undefined||d.rc===0;toast(ok?'Done':'Failed — see output','',4000);if(el('tokout'))el('tokout').textContent=fmtOut(d);if(u.includes('/paper/')||u.includes('/telegram/'))el('papstat').insertAdjacentHTML('beforeend','');refreshAll();return d})}
function postAct(u,b,msg){toast(msg,'',2500);return post(u,b).then(d=>{const ok=!d.error&&(d.rc===undefined||d.rc===0);toast(ok?(d.started?'Started (job '+(d.job||d.started)+')':'Done'):'Failed: '+(d.error||('rc '+d.rc)),ok?'ok':'bad',5000);if(el('tokout'))el('tokout').textContent=fmtOut(d);refreshAll();return d})}
el('nav').innerHTML=TABS.map(t=>`<a id="nb_${t[0]}" onclick="go('${t[0]}')"><span class="ic">${t[1]}</span>${t[2]}</a>`).join('');
function go(t){TABS.forEach(x=>{el(x[0]).classList.toggle('on',x[0]===t);el('nb_'+x[0]).classList.toggle('on',x[0]===t)});const m=TABS.find(x=>x[0]===t);el('ttl').textContent=m[2];el('sub').textContent=m[3];location.hash=t;refreshAll();if(t==='journal')loadJournal()}
function inr(x){return '₹'+Math.round(x).toLocaleString('en-IN')}
async function refreshAll(){let s;try{s=await api('/api/status')}catch(e){toast('Dashboard server not reachable','bad');return}
const tk=s.token,pp=s.paper,dj=s.jobs;
el('toppills').innerHTML=`<span class="pill ${tk.valid?'ok':'bad'}">● token ${tk.valid?'valid'+(tk.hours_left!=null?' · '+tk.hours_left+' h':''):'INVALID'}</span><span class="pill ${pp.running?'ok':'muted'}">● paper ${pp.running?'running':'idle'}</span><span class="pill ${pp.feed_running?'ok':'muted'}">● feed ${pp.feed_running?'live':'off'}</span><span class="pill ${dj.some(j=>j.running)?'warn':'muted'}">● ${dj.filter(j=>j.running).length} jobs</span>${s.res&&!s.res.error?`<span class="pill ${s.res.algo_cpu>=150?'bad':s.res.algo_cpu>=80?'warn':'muted'}" title="CPU and RAM of all algo processes">● algo cpu ${s.res.algo_cpu.toFixed(0)}% · ram ${Math.round(s.res.algo_rss_mb)} MB</span>`:''}`;
const rp=pid=>{const q=((s.res&&s.res.procs)||[]).find(x=>x.pid===pid);return q?{cpu:q.cpu_pct.toFixed(1)+'%',ram:Math.round(q.rss_mb)+' MB'}:{cpu:'—',ram:'—'}};drawRes(s.res);
el('kpis').innerHTML=`<div class="kpi"><div class="l">Dhan token</div><div class="v ${tk.valid?'ok':'bad'}">${tk.valid?'Valid':'Invalid'}</div><div class="d">${tk.hours_left!=null?tk.hours_left+' hours left':'—'} · ${tk.launchd_installed?'auto-renew on':'no agent'}</div></div>
<div class="kpi"><div class="l">Paper account</div><div class="v">${pp.state?inr(pp.state.equity):'₹50,000'}</div><div class="d">${pp.state?('today '+(pp.state.realised>=0?'+':'')+inr(pp.state.realised)+' · '+pp.trades.length+' trades'):'not started today'}</div></div>
<div class="kpi"><div class="l">Data through</div><div class="v" style="font-size:16px">${esc(s.data['nifty_index_1m_ext.csv'])}</div><div class="d">wings ${esc(s.data['nifty_wings_week_1m_ext.csv'])} · OI ${esc(s.data.participant_oi_last)}</div></div>
<div class="kpi"><div class="l">Paper trader</div><div class="v ${pp.running?'ok':'muted'}">${pp.running?'Running':'Idle'}</div><div class="d">${pp.state&&pp.state.halted?'DAY STOP HIT':(pp.running?'polling every minute':'start before 09:15')}</div></div>`;
el('health').innerHTML=`<table><tr><th>Item</th><th>State</th></tr><tr><td>Token</td><td class="${tk.valid?'ok':'bad'}">${tk.valid?'valid':'invalid — go to Token'}</td></tr><tr><td>TOTP credentials</td><td>${tk.credentials_saved?'<span class="ok">saved</span>':'<span class="warn">not saved — automation cannot regenerate</span>'}</td></tr><tr><td>Launchd agent</td><td>${tk.launchd_installed?'<span class="ok">installed (08:30 / 20:30)</span>':'<span class="warn">not installed</span>'}</td></tr><tr><td>Telegram</td><td>${tk.telegram_saved?'<span class="ok">connected</span>'+(tk.telegram_channel?' · channel <span class="mono">'+esc(String(tk.telegram_channel))+'</span>':' · <span class="muted">no channel</span>'):'<span class="muted">off</span>'}</td></tr><tr><td>Index 1m / ATM / wings</td><td class="mono">${esc(s.data['nifty_index_1m_ext.csv'])} / ${esc(s.data['nifty_atm_week_1m_ext.csv'])} / ${esc(s.data['nifty_wings_week_1m_ext.csv'])}</td></tr></table>`;
el('tokenlog').textContent=tk.token_log||'—';if(el('tgchan'))el('tgchan').textContent=tk.telegram_channel?String(tk.telegram_channel):'none';el('tokstat').innerHTML=`<pre class="mono" style="max-height:200px">${esc(tk.detail)}</pre>`;
el('datcov').innerHTML=`<table>${Object.entries(s.data).map(([k,x])=>`<tr><td class="mono">${esc(k)}</td><td>${esc(x)}</td></tr>`).join('')}</table>`;const dlog=dj.filter(j=>j.name==='data'||j.name==='wings').pop();if(dlog)api('/api/log?name='+dlog.log).then(d=>el('datlog').textContent=d.text||'—');
const bl=await api('/api/backtests');el('btcount').textContent=bl.backtests.length+' reports';
el('btlist').innerHTML='<table><tr><th>Report</th><th>Result</th><th></th></tr>'+bl.backtests.map(b=>{const m=b.result.match(/(\d+) trades.*?wins (\d+) \((\d+)%\).*?net ₹([+-][\d,]+)\/trade.*?t ([+-][\d.]+).*?→ ₹(-?[\d,]+) \(net ₹([+-][\d,]+)\).*?drawdown (-?[\d.]+)%/);const w=b.file.replace('BACKTEST_','').replace('.md','');const net=m?m[7]:'';return `<tr><td><b>${esc(w.split('_20')[0])}</b><br><span class="badge">${esc(w.slice(w.indexOf('_20')+1))}</span><br><small class="muted">${b.modified}</small></td><td class="result-line">${m?`<b class="${net.startsWith('+')?'ok':'bad'}">net ₹${m[7]}</b> · ${m[1]} trades · win ${m[3]}% · t ${m[5]} · ending ₹${m[6]} · max DD ${m[8]}%`:esc(b.result)}</td><td><button class="btn ghost sm" onclick="api('/api/file?name=${encodeURIComponent(b.file)}').then(t=>{el('btview').textContent=t;window.scrollTo({top:el('btview').offsetTop-80,behavior:'smooth'})})">View</button></td></tr>`}).join('')+'</table>';
el('papstat').innerHTML=`<div class="row"><span class="pill ${pp.running?'ok':'muted'}">● ${pp.running?'RUNNING':'idle'}</span><span class="pill">${pp.date}</span>${pp.state?`<span class="pill">equity ${inr(pp.state.equity)}</span><span class="pill ${pp.state.realised>=0?'ok':'bad'}">today ${(pp.state.realised>=0?'+':'')+inr(pp.state.realised)}</span>${pp.state.halted?'<span class="pill bad">DAY STOP</span>':''}`:'<span class="pill muted">no state yet</span>'}</div>`;
setProcButtons(pp.running,pp.pids||[],s.live?s.live.running:false,s.live?s.live.pids||[]:[]);
if(s.live){el('livechip').textContent=s.live.running?'LIVE RUNNING (real orders)':'live idle';el('livechip').style.color=s.live.running?'#fca5a5':'';el('liveweek').textContent='week ₹'+Math.round(s.live.week_realised).toLocaleString('en-IN');el('livefunds').textContent=s.live.funds!=null?('Dhan funds ₹'+Math.round(s.live.funds).toLocaleString('en-IN')+(s.live.state&&s.live.state.start_capital?' · session start ₹'+Math.round(s.live.state.start_capital).toLocaleString('en-IN'):'')):'Dhan funds —';el('livetrades').innerHTML=(s.live.trades&&s.live.trades.length)?s.live.trades.map(t=>`${t.entry_time} ${t.leg} ${t.strike} ₹${t.opt_entry}→₹${t.opt_exit} ${t.exit_reason} <b class="${(+t.net_inr)>=0?'ok':'bad'}">₹${t.net_inr}</b>`).join('<br>'):(s.live.state&&s.live.state.brains?Object.entries(s.live.state.brains).map(([n,b])=>b.open?`${n}: OPEN ${b.open.leg} ${b.open.strike} @ ₹${b.open.p_in} (order ${b.open.order_id||'—'})`:`${n}: no live trade yet today`).join('<br>'):'no live session today')}
el('papopen').innerHTML=pp.state?`<table><tr><th>Brain</th><th>Entries</th><th>Realised</th><th>Open position</th></tr>${Object.entries(pp.state.brains).map(([n,b])=>`<tr><td><b>${esc(n)}</b></td><td>${b.entries}/4</td><td class="${b.realised>=0?'ok':'bad'}">${(b.realised>=0?'+':'')+inr(b.realised)}</td><td>${b.open?`${b.open.leg} ${b.open.strike} @ ₹${b.open.p_in} · last ₹${b.open.last_px??'—'} · stop ${b.open.stop.toFixed(1)} · target ${b.open.target==null?'trail':b.open.target.toFixed(1)}`:'<span class="muted">none</span>'}</td></tr>`).join('')}</table>`:'<span class="muted">—</span>';
el('paptrades').innerHTML=pp.trades.length?'<table><tr>'+['brain','entry_time','leg','strike','opt_entry','exit_time','exit_reason','opt_exit','index_move_pts','net_inr'].map(k=>'<th>'+k.replace(/_/g,' ')+'</th>').join('')+'</tr>'+pp.trades.map(t=>'<tr>'+['brain','entry_time','leg','strike','opt_entry','exit_time','exit_reason','opt_exit','index_move_pts','net_inr'].map(k=>`<td class="${k==='net_inr'?(parseFloat(t[k])>=0?'ok':'bad'):''}">${esc(t[k])}</td>`).join('')+'</tr>').join('')+'</table>':'<span class="muted">none yet</span>';
el('paplog').textContent=pp.log||'—';el('papsess').innerHTML=pp.sessions.slice().reverse().map(f=>`<button class="btn ghost sm" onclick="api('/api/file?name=${encodeURIComponent(f)}').then(t=>el('papview').textContent=t)">${esc(f.replace('SESSION_','').replace('.md',''))}</button> `).join('');
el('jobs_t').innerHTML='<table><tr><th>Job</th><th>PID</th><th>Status</th><th>CPU</th><th>RAM</th><th>Started</th><th>Command</th><th></th></tr>'+(dj.length?dj.map(j=>`<tr><td><b>${esc(j.name)}</b></td><td class="mono">${j.pid}</td><td>${j.running?'<span class="pill ok">running</span>':'<span class="pill muted">done rc '+j.rc+'</span>'}</td><td class="mono">${rp(j.pid).cpu}</td><td class="mono">${rp(j.pid).ram}</td><td>${esc(j.started)}</td><td class="mono"><small>${esc(j.args)}</small></td><td><button class="btn ghost sm" onclick="api('/api/log?name=${encodeURIComponent(j.log)}').then(d=>el('joblog').textContent=d.text||'—')">Log</button> ${j.running?`<button class="btn danger sm" onclick="confirmThen('Kill job ${esc(j.name)}?','The process is terminated immediately.',()=>api('/api/job/kill?name=${esc(j.name)}').then(()=>{toast('Killed','warn');refreshAll()}))">Kill</button>`:''}</td></tr>`).join(''):'<tr><td colspan="6" class="muted">no jobs this session</td></tr>')+'</table>';
el('clock').textContent='updated '+new Date().toLocaleTimeString();if(el('paper').classList.contains('on'))loadChart()}
let TF=1;function setTf(t){TF=t;loadChart()}
function mb(x){return x>=1024?(x/1024).toFixed(2)+' GB':Math.round(x)+' MB'}
function drawRes(r){const tiles=el('rtiles');if(!tiles)return;if(!r||r.error){tiles.innerHTML=`<span class="muted">resource sampling unavailable${r&&r.error?': '+esc(r.error):''}</span>`;return}
const memPct=r.mem_total_mb?r.mem_used_mb/r.mem_total_mb*100:0,algoMem=r.mem_total_mb?r.algo_rss_mb/r.mem_total_mb*100:0,cls=p=>p>=90?'bad':p>=70?'warn':'';
tiles.innerHTML=`<div class="rtile"><div class="l">Algo CPU</div><div class="v">${r.algo_cpu.toFixed(1)}%</div><div class="d">of one core · ${r.procs.length} process${r.procs.length===1?'':'es'}</div><div class="meter"><i class="${cls(r.algo_cpu/r.ncpu)}" style="width:${Math.min(100,r.algo_cpu/r.ncpu)}%"></i></div></div>
<div class="rtile"><div class="l">Algo RAM</div><div class="v">${mb(r.algo_rss_mb)}</div><div class="d">${algoMem.toFixed(1)}% of ${mb(r.mem_total_mb)} (resident)</div><div class="meter"><i class="${cls(algoMem)}" style="width:${Math.min(100,algoMem)}%"></i></div></div>
<div class="rtile"><div class="l">Machine CPU</div><div class="v">${r.sys_cpu==null?'—':r.sys_cpu.toFixed(0)+'%'}</div><div class="d">${r.ncpu} cores${r.load?' · load '+r.load.join(' / '):''}${r.temp_c!=null?' · '+r.temp_c.toFixed(0)+' °C':''}</div><div class="meter"><i class="${cls(r.sys_cpu||0)}" style="width:${Math.min(100,r.sys_cpu||0)}%"></i></div></div>
<div class="rtile"><div class="l">Machine RAM</div><div class="v">${mb(r.mem_used_mb)}</div><div class="d">${memPct.toFixed(0)}% of ${mb(r.mem_total_mb)} used</div><div class="meter"><i class="${cls(memPct)}" style="width:${Math.min(100,memPct)}%"></i></div></div>`;
el('rprocs').innerHTML=r.procs.length?'<table><tr><th>Process</th><th>PID</th><th>CPU</th><th>RAM</th><th>Running for</th></tr>'+r.procs.map(p=>`<tr><td><b>${esc(p.name)}</b></td><td class="mono">${p.pid}</td><td class="mono">${p.cpu_pct.toFixed(1)}%</td><td class="mono">${mb(p.rss_mb)}</td><td class="mono">${esc(p.elapsed)}</td></tr>`).join('')+'</table>':'<span class="muted">no algo processes found</span>';
const h=r.hist||[];const span=h.length>1?h[h.length-1][0]-h[0][0]:0;el('rspan').textContent=span>=60?`last ${Math.round(span/60)} min`:span>0?`last ${span} s`:'';const cv=el('rchart');const W=cv.width=cv.clientWidth*2,H=cv.height=120*2;const g=cv.getContext('2d');g.clearRect(0,0,W,H);if(h.length<2)return;
const padL=8,padR=150,padT=14,padB=10;const top=Math.max(100,...h.map(x=>Math.max(x[1]||0,x[2]||0)))*1.05;const y=v=>padT+(1-v/top)*(H-padT-padB);const x=i=>padL+i/(h.length-1)*(W-padL-padR);
g.font='18px -apple-system,Helvetica';g.lineWidth=2;[0,50,100].forEach(v=>{if(v>top)return;g.strokeStyle='#1b2430';g.beginPath();g.moveTo(padL,y(v));g.lineTo(W-padR,y(v));g.stroke();g.fillStyle='#8fa0b3';g.fillText(v+'%',padL+6,y(v)-5)});
const line=(idx,color)=>{g.strokeStyle=color;g.lineWidth=3;g.beginPath();let f=true;h.forEach((p,i)=>{if(p[idx]==null)return;f?g.moveTo(x(i),y(p[idx])):g.lineTo(x(i),y(p[idx]));f=false});g.stroke()};
line(1,'#d97706');line(2,'#3b82f6');const last=h[h.length-1];g.font='bold 18px -apple-system,Helvetica';let ya=y(last[2]),ym=last[1]==null?null:y(last[1]);if(ym!=null&&Math.abs(ya-ym)<24){const mid=(ya+ym)/2;ya=mid+(ya>=ym?12:-12);ym=mid+(ya>=ym?-12:12)}g.fillStyle='#3b82f6';g.fillText(last[2].toFixed(0)+'% algo',W-padR+8,Math.max(padT+8,Math.min(H-padB,ya+6)));if(ym!=null){g.fillStyle='#d97706';g.fillText(last[1].toFixed(0)+'% machine',W-padR+8,Math.max(padT+8,Math.min(H-padB,ym+6)))}}
async function loadChart(){let d;try{d=await api('/api/chart?tf='+TF)}catch(e){return}el('tf1').classList.toggle('ghost',TF!==1);el('tf5').classList.toggle('ghost',TF!==5);
if(d.error){el('chartsrc').textContent=d.error;return}el('chartsrc').textContent=`${d.date} · ${d.tf}m · ${d.source} · ${d.ltp!=null?'LTP '+d.ltp.toFixed(2):'last '+d.last.t+' '+d.last.c.toFixed(1)}`;
if(d.forming&&d.tf===1){d.bars=d.bars.concat([{t:d.forming[0],o:d.forming[1],h:d.forming[2],l:d.forming[3],c:d.forming[4],ema9:d.bars.length?d.bars[d.bars.length-1].ema9:d.forming[4],forming:true}])}
drawChart(d);drawFeed(d);drawQuotes(d);if(d.feed&&!window._fast){window._fast=setInterval(()=>{if(el('paper').classList.contains('on'))loadChart()},5000)}}
function drawQuotes(d){const q=d.quotes;const box=el('quotes');if(!box)return;if(!q||!q.options){box.innerHTML='<span class="muted">Option quotes appear when the live feed is running (tools/dhan_feed.py).</span>';return}
const ks=Object.keys(q.options).map(Number).sort((a,b)=>a-b);box.innerHTML=`<div class="muted" style="font-size:12px;margin-bottom:4px">Weekly expiry ${q.expiry} · ATM ${q.atm} · live LTP</div><table><tr><th>CE</th><th>Strike</th><th>PE</th></tr>${ks.map(k=>`<tr style="${k===q.atm?'background:rgba(59,130,246,.12)':''}"><td class="${k<q.atm?'ok':''}">${q.options[k].CE??'—'}</td><td><b>${k}</b></td><td class="${k>q.atm?'ok':''}">${q.options[k].PE??'—'}</td></tr>`).join('')}</table>`}
function drawFeed(d){const ev=[];d.trades.forEach(t=>{ev.push({t:t.entry_time,k:'entry',brain:t.brain,txt:`${t.brain} BUY ${t.leg} ${t.strike} @ ₹${t.opt_entry} · index ${t.index_entry} · stop ${t.stop_at_entry} · target ${t.target}`});ev.push({t:t.exit_time,k:'exit',net:parseFloat(t.net_inr),brain:t.brain,txt:`${t.brain} ${t.exit_reason} SELL ${t.leg} ${t.strike} @ ₹${t.opt_exit} · index ${t.index_exit} (${t.index_move_pts} pts) · net ₹${t.net_inr}`})});
d.open.forEach(o=>ev.push({t:o.entry_time,k:'open',brain:o.brain,txt:`${o.brain} OPEN ${o.leg} ${o.strike} @ ₹${o.p_in} · last ₹${o.last_px??'—'} · stop ${(+o.stop).toFixed(1)} · target ${o.target==null?'trail':(+o.target).toFixed(1)}`}));
ev.sort((a,b)=>b.t.localeCompare(a.t));el('feed').innerHTML=ev.length?ev.map(e=>`<div style="padding:7px 9px;border-left:3px solid ${e.k==='entry'?'#3b82f6':e.k==='open'?'#f59e0b':(e.net>=0?'#22c55e':'#ef4444')};background:var(--panel2);border-radius:6px;margin-bottom:6px"><b>${e.t}</b> <span class="badge">${e.k}</span><br>${esc(e.txt)}</div>`).join(''):'<span class="muted">No entries or exits yet today. Signals appear here the minute they fire.</span>'}
function drawChart(d){const cv=el('chart');const W=cv.width=cv.clientWidth*2,H=cv.height=420*2;const g=cv.getContext('2d');g.scale(1,1);g.clearRect(0,0,W,H);const bars=d.bars;if(!bars.length)return;
const padL=12,padR=110,padT=16,padB=40;const n=bars.length,slot=(W-padL-padR)/Math.max(n,60);const lv=d.pivots?Object.values(d.pivots):[];let lo=Math.min(...bars.map(b=>b.l)),hi=Math.max(...bars.map(b=>b.h));
lv.forEach(x=>{if(x>lo-150&&x<hi+150){lo=Math.min(lo,x);hi=Math.max(hi,x)}});const pad=(hi-lo)*0.06||5;lo-=pad;hi+=pad;const y=p=>padT+(hi-p)/(hi-lo)*(H-padT-padB);const x=i=>padL+i*slot+slot/2;
g.font='22px -apple-system,Helvetica';g.lineWidth=2;for(let k=0;k<=6;k++){const p=lo+(hi-lo)*k/6;g.strokeStyle='#1b2430';g.beginPath();g.moveTo(padL,y(p));g.lineTo(W-padR,y(p));g.stroke();g.fillStyle='#8fa0b3';g.fillText(p.toFixed(0),W-padR+10,y(p)+8)}
if(d.pivots){Object.entries(d.pivots).forEach(([k,p])=>{if(p<lo||p>hi)return;g.setLineDash([10,8]);g.strokeStyle=k==='P'?'#9ca3af':(k[0]==='R'?'#f87171':'#4ade80');g.beginPath();g.moveTo(padL,y(p));g.lineTo(W-padR,y(p));g.stroke();g.setLineDash([]);g.fillStyle=g.strokeStyle;g.fillText(k+' '+p.toFixed(0),padL+6,y(p)-6)})}
const times=Object.keys(d.vwap);g.strokeStyle='#f59e0b';g.lineWidth=3;g.beginPath();let first=true;bars.forEach((b,i)=>{const tv=d.vwap[b.t]??d.vwap[times.reduce((a,t)=>t<=b.t?t:a,times[0])];if(tv==null)return;first?g.moveTo(x(i),y(tv)):g.lineTo(x(i),y(tv));first=false});g.stroke();
g.strokeStyle='#a78bfa';g.lineWidth=2.5;g.beginPath();bars.forEach((b,i)=>i?g.lineTo(x(i),y(b.ema9)):g.moveTo(x(i),y(b.ema9)));g.stroke();
bars.forEach((b,i)=>{const up=b.c>=b.o;g.strokeStyle=g.fillStyle=up?'#22c55e':'#ef4444';g.lineWidth=2;g.beginPath();g.moveTo(x(i),y(b.h));g.lineTo(x(i),y(b.l));g.stroke();const w=Math.max(3,slot*0.62);if(b.forming){g.strokeRect(x(i)-w/2,Math.min(y(b.o),y(b.c)),w,Math.max(2,Math.abs(y(b.o)-y(b.c))))}else{g.fillRect(x(i)-w/2,Math.min(y(b.o),y(b.c)),w,Math.max(2,Math.abs(y(b.o)-y(b.c))))}});
g.fillStyle='#8fa0b3';g.font='20px -apple-system,Helvetica';const step=Math.max(1,Math.ceil(80/slot));bars.forEach((b,i)=>{if(i%step===0)g.fillText(b.t,x(i)-26,H-10)});
const idx=t=>{let best=0;bars.forEach((b,i)=>{if(b.t<=t)best=i});return best};
d.trades.forEach(t=>{const i=idx(t.entry_time),j=idx(t.exit_time);const ce=t.leg==='CE';g.fillStyle=ce?'#22c55e':'#ef4444';g.beginPath();const yy=y(+t.index_entry);if(ce){g.moveTo(x(i),yy+16);g.lineTo(x(i)-12,yy+36);g.lineTo(x(i)+12,yy+36)}else{g.moveTo(x(i),yy-16);g.lineTo(x(i)-12,yy-36);g.lineTo(x(i)+12,yy-36)}g.closePath();g.fill();
const win=parseFloat(t.net_inr)>=0;g.fillStyle=win?'#22c55e':'#ef4444';const ye=y(+t.index_exit);g.beginPath();g.moveTo(x(j),ye-14);g.lineTo(x(j)+14,ye);g.lineTo(x(j),ye+14);g.lineTo(x(j)-14,ye);g.closePath();g.fill();g.strokeStyle=win?'rgba(34,197,94,.5)':'rgba(239,68,68,.5)';g.setLineDash([4,4]);g.beginPath();g.moveTo(x(i),yy);g.lineTo(x(j),ye);g.stroke();g.setLineDash([]);g.fillStyle='#e8edf3';g.font='18px -apple-system,Helvetica';g.fillText(`${t.brain} ${t.exit_reason} ₹${t.net_inr}`,x(j)+18,ye+6)});
d.open.forEach(o=>{const i=idx(o.entry_time);const yy=y(+o.index_entry);g.strokeStyle='#f59e0b';g.lineWidth=3;g.beginPath();g.arc(x(i),yy,12,0,Math.PI*2);g.stroke();g.setLineDash([6,6]);g.strokeStyle='#ef4444';g.beginPath();g.moveTo(x(i),y(+o.stop));g.lineTo(W-padR,y(+o.stop));g.stroke();if(o.target!=null){g.strokeStyle='#22c55e';g.beginPath();g.moveTo(x(i),y(+o.target));g.lineTo(W-padR,y(+o.target));g.stroke()}g.setLineDash([]);g.fillStyle='#f59e0b';g.font='18px -apple-system,Helvetica';g.fillText(`${o.brain} ${o.leg} ${o.strike} open`,x(i)+16,yy-16)});
g.fillStyle='#e8edf3';g.font='bold 22px -apple-system,Helvetica';g.fillText(d.last.c.toFixed(1),W-padR+10,y(d.last.c)+8)}
function loadJournal(){api('/api/file?name=journal').then(t=>el('jview').textContent=t)}
function preset(k){const t=new Date().toISOString().slice(0,10);if(k==='dev'){el('ba').value='2023-09-11';el('bb').value='2025-06-30'}else{el('ba').value='2026-07-06';el('bb').value=t}}
function runBT(){if(!v('ba')||!v('bb')){toast('Choose both dates (or use a preset)','warn');return}confirmThen('Run this backtest?',`${v('bbrain')} ${v('ba')} → ${v('bb')}, R:R ${v('brr')}${el('bitm').checked?', ITM2':''}${el('bbe').checked?', break-even':''}${el('bconf').checked?', confirmation':''}${v('btrail')?', trail '+v('btrail'):''}. Runs in the background.`,()=>postAct('/api/backtest/run',{brain:v('bbrain'),a:v('ba'),b:v('bb'),rr:v('brr'),itm:el('bitm').checked,be:el('bbe').checked,conf:el('bconf').checked,trail:v('btrail')},'Starting backtest…'))}
function setProcButtons(pr,ppids,lr,lpids){const sb=el('startbtn'),pb=el('stopbtn'),ls=el('livestart'),lp=el('livestop');
if(sb){sb.disabled=pr;sb.textContent=pr?'● Paper trader RUNNING (pid '+(ppids[0]||'?')+')':'▶ Start paper trader';sb.classList.toggle('ok',!pr);sb.classList.toggle('ghost',pr)}
if(pb){pb.disabled=!pr;pb.textContent=pr?'■ Stop paper trader':'■ Stop (not running)'}
if(ls){ls.disabled=lr;ls.textContent=lr?'● LIVE trader RUNNING (pid '+(lpids[0]||'?')+')':'💰 Start LIVE trader';ls.classList.toggle('danger',!lr);ls.classList.toggle('ghost',lr)}
if(lp){lp.disabled=!lr;lp.textContent=lr?'■ Stop LIVE trader':'■ Stop live (not running)'}
const lc=el('livechip');if(lc){lc.textContent=lr?'LIVE RUNNING (real orders)':'live idle';lc.style.color=lr?'#fca5a5':''}}
async function waitState(want,ms=12000){const t0=Date.now();while(Date.now()-t0<ms){await new Promise(r=>setTimeout(r,1500));let s;try{s=await api('/api/status')}catch(e){continue}
const cur={paper:s.paper.running,live:s.live?s.live.running:false};setProcButtons(s.paper.running,s.paper.pids||[],cur.live,s.live?s.live.pids||[]:[]);if(cur[want.key]===want.val){refreshAll();return true}}refreshAll();return false}
function stopProc(u,msg){toast(msg,'',2500);return post(u,{}).then(d=>{const key=u.includes('/live/')?'live':'paper';if(d.error){toast('Failed: '+d.error,'bad',5000);return d}toast(d.stopped?'Stop signal sent — waiting for the process to exit…':'Nothing was running','',4000);waitState({key,val:false}).then(ok=>toast(ok?(key==='live'?'LIVE trader stopped':'Paper trader stopped'):'Still running after 12 s — check the Jobs page','ok'));return d})}
function startLive(){const c=v('liveconfirm');if(c!=='LIVE'){toast('Type LIVE in the box to confirm','warn');return}
confirmThen('Start LIVE trading with REAL money?',`PIVOT brain, 1 lot, 2 strikes ITM, cap ₹${v('pcap')}, R:R ${v('prr')}. Real market orders will be placed on your Dhan account. Day stop −₹5,000, week brake −₹10,000.`,()=>{api('/api/feed/start').then(()=>postAct('/api/live/start',{confirm:'LIVE',cap:v('pcap'),rr:v('prr')},'Starting LIVE trader…').then(d=>{if(!d.error)waitState({key:'live',val:true})}))})}
function startPaper(){const brains=[el('pvwap').checked?'VWAP':null,el('ppivot').checked?'PIVOT':null,el('pexpiry').checked?'EXPIRY':null].filter(Boolean).join(',');if(!brains){toast('Select at least one brain','warn');return}
const feed=el('pfeed').checked;confirmThen('Start the paper trader?',`Brains: ${brains}. PIVOT ${v('pitm')==='2'?'2 strikes in the money':'at the money'}, R:R ${v('prr')}; VWAP R ${v('pr')}; cap ₹${v('pcap')}. ${feed?'Live feed: the feed daemon is started first.':'Polling mode.'} No orders are placed.`,()=>{(feed?api('/api/feed/start'):Promise.resolve()).then(()=>postAct('/api/paper/start',{brains,itm:v('pitm'),rr:v('prr'),r:v('pr'),cap:v('pcap'),feed},'Starting paper trader…').then(d=>{if(!d.error)waitState({key:'paper',val:true})}))})}
go(TABS.some(t=>t[0]===location.hash.slice(1))?location.hash.slice(1):'overview');setInterval(refreshAll,30000);
</script></body></html>"""


class H(BaseHTTPRequestHandler):
    def _json(self, obj, code=200):
        b = json.dumps(obj, default=str).encode(); self.send_response(code); self.send_header("Content-Type", "application/json"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b)

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0); return json.loads(self.rfile.read(n) or b"{}") if n else {}

    def log_message(self, *a):
        pass

    def do_GET(self):
        u = urllib.parse.urlparse(self.path); q = urllib.parse.parse_qs(u.query); p = u.path
        if p == "/":
            b = HTML.encode(); self.send_response(200); self.send_header("Content-Type", "text/html; charset=utf-8"); self.send_header("Content-Length", str(len(b))); self.end_headers(); self.wfile.write(b); return
        if p == "/api/status":
            return self._json({"token": token_status(), "data": data_status(), "paper": paper_status(), "live": live_status(), "jobs": job_status(), "res": resources()})
        if p == "/api/resources":
            return self._json(resources())
        if p == "/api/backtests":
            return self._json(backtest_list())
        if p == "/api/chart":
            try:
                return self._json(chart_data(int(q.get("tf", ["1"])[0])))
            except Exception as e:
                return self._json({"error": str(e)[:200]}, 500)
        if p == "/api/token/verify":
            rc, out = run_capture([PY, "tools/dhan_token.py", "verify"], 30); return self._json({"rc": rc, "out": out})
        if p == "/api/token/auto":
            rc, out = run_capture([PY, "tools/dhan_token.py", "auto"], 60); return self._json({"rc": rc, "out": out})
        if p == "/api/token/launchd":
            rc, out = run_capture([PY, "tools/dhan_token.py", "install-launchd", "--then-update"], 30); return self._json({"rc": rc, "out": out})
        if p == "/api/telegram/test":
            rc, out = run_capture([PY, "tools/paper_trade.py", "--telegram-test"], 30); return self._json({"rc": rc, "out": out})
        if p == "/api/telegram/test_channel":
            rc, out = run_capture([PY, "tools/paper_trade.py", "--telegram-test-channel"], 30); return self._json({"rc": rc, "out": out})
        if p == "/api/journal/rebuild":
            rc, out = run_capture([PY, "tools/journal.py"], 300); return self._json({"rc": rc, "out": out})
        if p == "/api/feed/start":
            if subprocess.run(["pgrep", "-f", "tools/dhan_feed.py"], capture_output=True).returncode == 0:
                return self._json({"started": False, "note": "already running"})
            dd = date.today().isoformat(); pid = start_job("feed", [PY, "tools/dhan_feed.py"], f"feed_{dd}.log"); return self._json({"started": pid})
        if p == "/api/feed/stop":
            subprocess.run(["pkill", "-f", "tools/dhan_feed.py"]); return self._json({"stopped": True})
        if p == "/api/paper/stop":
            pids = paper_pids(); _stop_pids(pids); return self._json({"stopped": bool(pids), "pids": pids, "running": bool(paper_pids()), "note": "" if pids else "not running"})
        if p == "/api/job/kill":
            n = q.get("name", [""])[0]
            with LOCK:
                j = JOBS.get(n)
            if j and j["proc"].poll() is None:
                j["proc"].terminate(); return self._json({"killed": n})
            return self._json({"killed": None})
        if p == "/api/log":
            n = q.get("name", [""])[0]; return self._json({"text": tail(LOGS / Path(n).name, 120)})
        if p == "/api/file":
            n = q.get("name", [""])[0]
            path = ROOT / "knowledge/SETUP_CATALOGUE_NIFTY.md" if n == "catalogue" else (PAPER / "JOURNAL.md" if n == "journal" else PAPER / Path(n).name)
            if not path.exists():
                return self._json({"error": "not found"}, 404)
            txt = path.read_text(errors="replace"); return self._json(txt[:200000])
        self._json({"error": "unknown"}, 404)

    def do_POST(self):
        p = urllib.parse.urlparse(self.path).path; b = self._body()
        if p in ("/api/paper/stop",):
            return self.do_GET()
        if p == "/api/live/start":
            if b.get("confirm") != "LIVE":
                return self._json({"error": "type LIVE to confirm"}, 400)
            if live_pids():
                return self._json({"error": "live trader already running", "pids": live_pids()}, 409)
            d = date.today().isoformat(); args = [PY, "tools/paper_trade.py", "--live", "--i-accept-live-risk", "--brains", "PIVOT", "--pivot-itm", "2", "--pivot-cap", str(b.get("cap") if b.get("cap") not in (None, "") else 15000), "--rr", str(b.get("rr") or 3), "--feed", "--log", str(LOGS / f"live_{d}.log")]
            pid = start_job("live", args, f"live_stdout_{d}.log"); return self._json({"started": pid, "args": args[1:]})
        if p == "/api/live/stop":
            pids = live_pids(); _stop_pids(pids); return self._json({"stopped": bool(pids), "pids": pids, "running": bool(live_pids())})
        if p == "/api/token/save":
            tok = (b.get("token") or "").strip()
            if len(tok) < 20:
                return self._json({"error": "token too short"}, 400)
            TOKEN_FILE.parent.mkdir(mode=0o700, exist_ok=True); TOKEN_FILE.write_text(tok); os.chmod(TOKEN_FILE, 0o600)
            rc, out = run_capture([PY, "tools/dhan_token.py", "verify"], 30); return self._json({"saved": True, "verify_rc": rc, "out": out})
        if p == "/api/token/credentials":
            env = {"DHAN_CLIENT_ID": b.get("cid", ""), "DHAN_PIN": b.get("pin", ""), "DHAN_TOTP_SECRET": b.get("totp", "")}
            rc, out = run_capture([PY, "tools/dhan_token.py", "setup", "--from-env"], 30, env={k: v for k, v in env.items() if v}); return self._json({"rc": rc, "out": out})
        if p == "/api/telegram/setup":
            rc, out = run_capture([PY, "tools/paper_trade.py", "--telegram-setup", b.get("token", "")], 30); return self._json({"rc": rc, "out": out})
        if p == "/api/telegram/channel":
            rc, out = run_capture([PY, "tools/paper_trade.py", "--telegram-channel", b.get("channel", "") or "off"], 30); return self._json({"rc": rc, "out": out})
        if p == "/api/journal/note":
            dd, txt = (b.get("date") or "").strip(), (b.get("text") or "").strip()
            if not (dd and txt):
                return self._json({"error": "date and text required"}, 400)
            rc, out = run_capture([PY, "tools/journal.py", "--note", dd, txt], 300); return self._json({"rc": rc, "out": out})
        if p == "/api/data/fetch":
            asof = b.get("asof") or date.today().isoformat(); pid = start_job("data", [PY, "tools/daily_update.py", "--fetch", "--as-of", asof], f"data_{asof}.log"); return self._json({"started": pid})
        if p == "/api/data/wings":
            a, bb = b.get("a"), b.get("b")
            if not (a and bb):
                return self._json({"error": "need both dates"}, 400)
            scratch = ROOT / "tools" / "wings_backfill.py"
            if not scratch.exists():
                return self._json({"error": "tools/wings_backfill.py missing"}, 500)
            pid = start_job("wings", [PY, str(scratch), a, bb], f"wings_{a}_{bb}.log", env={"DHAN_ACCESS_TOKEN": TOKEN_FILE.read_text().strip() if TOKEN_FILE.exists() else ""}); return self._json({"started": pid})
        if p == "/api/backtest/run":
            a, bb = b.get("a"), b.get("b")
            if not (a and bb):
                return self._json({"error": "need start and end dates"}, 400)
            args = [PY, "tools/backtest_brain.py", b.get("brain", "PIVOT"), a, bb, "--rr", str(b.get("rr") or 3)]
            if b.get("itm"): args.append("--itm2")
            if b.get("be"): args.append("--be")
            if b.get("conf"): args.append("--confirm")
            if b.get("trail"): args += ["--trail", str(b["trail"])]
            name = f"bt_{int(time.time())}"; pid = start_job(name, args, f"{name}.log", env={"TRADINGALGO_NO_TG": "1"}); return self._json({"started": pid, "job": name, "args": args[1:]})
        if p == "/api/paper/start":
            if paper_pids():
                return self._json({"error": "already running", "pids": paper_pids()}, 409)
            d = date.today().isoformat(); args = [PY, "tools/paper_trade.py", "--brains", b.get("brains") or "PIVOT,EXPIRY", "--pivot-itm", str(b.get("itm") or 0), "--rr", str(b.get("rr") or 3), "--r", str(b.get("r") or 7), "--log", str(LOGS / f"paper_{d}.log")]
            args += ["--pivot-cap", str(b.get("cap") if b.get("cap") not in (None, "") else 15000)]
            if b.get("feed"): args.append("--feed")
            pid = start_job("paper", args, f"paper_stdout_{d}.log"); return self._json({"started": pid, "args": args[1:]})
        self._json({"error": "unknown"}, 404)


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--port", type=int, default=8765); a = ap.parse_args()
    (ROOT / "tools" / "wings_backfill.py").exists() or None
    threading.Thread(target=_res_loop, daemon=True, name="resources").start()
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), H); print(f"dashboard: http://127.0.0.1:{a.port}  (local only; Ctrl-C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
