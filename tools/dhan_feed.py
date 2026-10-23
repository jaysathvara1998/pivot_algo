#!/usr/bin/env python3
"""Dhan live market feed → local snapshot (stdlib only). Connects to wss://api-feed.dhan.co (the stream the Dhan chart uses), subscribes to the NIFTY index and the nearest-weekly
option contracts around the money (ATM±3, both legs), builds 1-minute candles from ticks (backfilled from the historical API at start), and writes paper/FEED_<date>.json every 2 s:
  {written, date, ltp, ltt, bars:[[HH:MM,o,h,l,c], ...completed], forming:[HH:MM,o,h,l,c], atm, expiry, options:{strike:{CE:ltp, PE:ltp}}, options_minute:{HH:MM:{strike:{CE,PE}}}}
Usage: python3 tools/dhan_feed.py [--date YYYY-MM-DD]. Reconnects on drop; resubscribes when the ATM strike moves. Token from ~/.dhan/access_token, client id from ~/.dhan/credentials.json."""
from __future__ import annotations

import argparse, base64, csv, json, os, socket, ssl, struct, sys, threading, time, urllib.request
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]; PAPER = ROOT / "paper"; IST = ZoneInfo("Asia/Kolkata")
SCRIP = ROOT / "datasets/raw/dhan/scrip/api-scrip-master.csv"; STEP = 50.0; BAND = 3


def token():
    return os.environ.get("DHAN_ACCESS_TOKEN") or (Path.home() / ".dhan" / "access_token").read_text().strip()


def client_id():
    c = Path.home() / ".dhan" / "credentials.json"
    return os.environ.get("DHAN_CLIENT_ID") or (json.loads(c.read_text()).get("DHAN_CLIENT_ID") if c.exists() else None) or sys.exit("client id missing (run dhan_token.py setup)")


def scrip_options(d):
    """{(strike, 'CE'|'PE'): security_id} for the nearest NIFTY weekly expiry on/after d, plus the expiry date."""
    if not SCRIP.exists() or time.time() - SCRIP.stat().st_mtime > 86400:
        SCRIP.parent.mkdir(parents=True, exist_ok=True); urllib.request.urlretrieve("https://images.dhan.co/api-data/api-scrip-master.csv", SCRIP)
    rows = [r for r in csv.DictReader(open(SCRIP, errors="replace")) if r.get("SEM_INSTRUMENT_NAME") == "OPTIDX" and r.get("SEM_EXM_EXCH_ID") == "NSE" and r.get("SEM_TRADING_SYMBOL", "").startswith("NIFTY-")]
    exps = sorted({r["SEM_EXPIRY_DATE"][:10] for r in rows if r["SEM_EXPIRY_DATE"][:10] >= d.isoformat()}); exp = exps[0]
    out = {}
    for r in rows:
        if r["SEM_EXPIRY_DATE"][:10] == exp:
            out[(float(r["SEM_STRIKE_PRICE"]), r["SEM_OPTION_TYPE"])] = r["SEM_SMST_SECURITY_ID"]
    return out, exp


def backfill(d):
    body = json.dumps({"securityId": "13", "exchangeSegment": "IDX_I", "instrument": "INDEX", "interval": "1", "oi": False, "fromDate": d.isoformat(), "toDate": d.isoformat()}).encode()
    req = urllib.request.Request("https://api.dhan.co/v2/charts/intraday", data=body, headers={"access-token": token(), "Content-Type": "application/json", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        j = json.loads(r.read())
    bars = {}
    for i, ts in enumerate(j.get("timestamp") or []):
        t = datetime.fromtimestamp(ts, IST)
        if t.date() == d and "09:15" <= t.strftime("%H:%M") <= "15:29":
            bars[t.strftime("%H:%M")] = [t.strftime("%H:%M"), j["open"][i], j["high"][i], j["low"][i], j["close"][i]]
    return bars


class WS:
    def __init__(self):
        host = "api-feed.dhan.co"; path = f"/?version=2&token={token()}&clientId={client_id()}&authType=2"
        ctx = ssl.create_default_context(); self.s = ctx.wrap_socket(socket.create_connection((host, 443), timeout=20), server_hostname=host)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall((f"GET {path} HTTP/1.1\r\nHost: {host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        resp = b""
        while b"\r\n\r\n" not in resp:
            chunk = self.s.recv(4096)
            if not chunk:
                raise ConnectionError("handshake failed")
            resp += chunk
        if b" 101 " not in resp.split(b"\r\n")[0]:
            raise ConnectionError(resp[:200].decode(errors="replace"))
        self.s.settimeout(30)

    def _send(self, op, data):
        mask = os.urandom(4); n = len(data); hdr = bytes([0x80 | op])
        hdr += bytes([0x80 | n]) if n < 126 else (bytes([0x80 | 126]) + struct.pack(">H", n) if n < 65536 else bytes([0x80 | 127]) + struct.pack(">Q", n))
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def send_json(self, obj):
        self._send(1, json.dumps(obj).encode())

    def recv(self):
        h = self._exact(2); op = h[0] & 0x0F; n = h[1] & 0x7F
        if n == 126:
            n = struct.unpack(">H", self._exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", self._exact(8))[0]
        return op, self._exact(n)

    def _exact(self, n):
        buf = b""
        while len(buf) < n:
            c = self.s.recv(n - len(buf))
            if not c:
                raise ConnectionError("socket closed")
            buf += c
        return buf

    def pong(self, data):
        self._send(10, data)

    def close(self):
        try:
            self.s.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser(); ap.add_argument("--date", default=date.today().isoformat()); a = ap.parse_args(); d = date.fromisoformat(a.date)
    out_p = PAPER / f"FEED_{d}.json"; PAPER.mkdir(exist_ok=True)
    opts, expiry = scrip_options(d); print(f"feed: nearest expiry {expiry}, {len(opts)} contracts in the scrip master", flush=True)
    try:
        bars = backfill(d); print(f"feed: backfilled {len(bars)} completed 1m bars from the historical API", flush=True)
    except Exception as e:
        bars = {}; print(f"feed: backfill failed ({str(e)[:80]}); building from ticks only", flush=True)
    state = {"ltp": None, "ltt": None, "forming": None, "options": {}, "options_minute": {}, "atm": None, "subscribed": set(), "sid2key": {}, "last_rx": time.time(), "ws": None, "fill_due": True}
    lock = threading.Lock(); ws = None

    def fill_gaps(reason):
        """Fill any completed minute missing from `bars` from the historical API (2026-10-21: a stale socket lost 64 minutes incl. an 11:05 pivot cross)."""
        try:
            now = datetime.now(IST); last_done = (now - timedelta(minutes=1)).strftime("%H:%M")
            t = datetime.combine(d, datetime.strptime("09:15", "%H:%M").time(), tzinfo=IST); want = []
            while t.strftime("%H:%M") <= min(last_done, "15:29"):
                if t.strftime("%H:%M") not in bars:
                    want.append(t.strftime("%H:%M"))
                t += timedelta(minutes=1)
            if not want:
                return 0
            hist = backfill(d); n = 0
            with lock:
                for k in want:
                    if k in hist and k not in bars:
                        bars[k] = hist[k]; n += 1
            print(f"feed: {now.strftime('%H:%M:%S')} gap backfill ({reason}): {n} of {len(want)} missing minutes filled from the historical API", flush=True)
            return n
        except Exception as e:
            print(f"feed: gap backfill failed: {str(e)[:100]}", flush=True); return 0

    def watchdog():
        """Force a reconnect when the index has sent no tick for 20 s during market hours (a half-dead socket never raises by itself), and check for gaps every 60 s."""
        last_fill = 0.0
        while True:
            time.sleep(5)
            now = datetime.now(IST); hm = now.strftime("%H:%M")
            if not ("09:16" <= hm <= "15:29"):
                continue
            if time.time() - state["last_rx"] > 20 and state["ws"] is not None:
                print(f"feed: {now.strftime('%H:%M:%S')} no index tick for {int(time.time() - state['last_rx'])} s — forcing reconnect", flush=True)
                try:
                    state["ws"].close()
                except Exception:
                    pass
                state["last_rx"] = time.time()
            if state["fill_due"] or time.time() - last_fill > 60:
                state["fill_due"] = False; last_fill = time.time(); fill_gaps("periodic")
    threading.Thread(target=watchdog, daemon=True).start()

    def subscribe(ws, keys):
        new = [k for k in keys if k not in state["subscribed"] and k in opts]
        for i in range(0, len(new), 100):
            batch = new[i:i + 100]; ws.send_json({"RequestCode": 17, "InstrumentCount": len(batch), "InstrumentList": [{"ExchangeSegment": "NSE_FNO", "SecurityId": opts[k]} for k in batch]})
            for k in batch:
                state["subscribed"].add(k); state["sid2key"][int(opts[k])] = k

    def want_keys(atm):
        return [(atm + i * STEP, leg) for i in range(-BAND, BAND + 1) for leg in ("CE", "PE")]

    def writer():
        while True:
            time.sleep(2)
            with lock:
                snap = {"written": datetime.now(IST).isoformat(timespec="seconds"), "date": d.isoformat(), "ltp": state["ltp"], "ltt": state["ltt"], "bars": [bars[k] for k in sorted(bars)], "forming": state["forming"],
                        "atm": state["atm"], "expiry": expiry, "options": {str(int(k)): v for k, v in state["options"].items()}, "options_minute": {m: {str(int(k)): v for k, v in sv.items()} for m, sv in list(state["options_minute"].items())[-60:]}}
            tmp = out_p.with_suffix(".tmp"); tmp.write_text(json.dumps(snap)); tmp.replace(out_p)
    threading.Thread(target=writer, daemon=True).start()
    while True:
        try:
            ws = WS(); state["subscribed"].clear(); state["ws"] = ws; state["last_rx"] = time.time(); state["fill_due"] = True; ws.send_json({"RequestCode": 17, "InstrumentCount": 1, "InstrumentList": [{"ExchangeSegment": "IDX_I", "SecurityId": "13"}]})
            if state["atm"]:
                subscribe(ws, want_keys(state["atm"]))
            print(f"feed: connected {datetime.now(IST).strftime('%H:%M:%S')}", flush=True)
            while True:
                op, data = ws.recv()
                if op == 9:
                    ws.pong(data); continue
                if op == 8:
                    raise ConnectionError("server closed")
                if op != 2 or len(data) < 8:
                    continue
                code, length, seg, sid = struct.unpack("<BHBI", data[:8]); body = data[8:]
                if code == 50:
                    raise ConnectionError(f"disconnect code {struct.unpack('<H', body[:2])[0] if len(body) >= 2 else '?'}")
                if code not in (2, 4):
                    continue
                ltp, = struct.unpack("<f", body[:4]); ltt = struct.unpack("<i", body[6:10])[0] if code == 4 else struct.unpack("<i", body[4:8])[0]
                t = datetime.fromtimestamp(ltt - 19800, IST) if ltt > 0 else datetime.now(IST)          # Dhan sends IST seconds as if UTC
                now = datetime.now(IST)
                if abs((t - now).total_seconds()) > 3600:
                    t = now
                with lock:
                    if seg == 0 and sid == 13:
                        state["last_rx"] = time.time(); state["ltp"] = round(ltp, 2); state["ltt"] = t.strftime("%H:%M:%S"); m = t.strftime("%H:%M")
                        if not ("09:15" <= m <= "15:29"):
                            continue
                        f = state["forming"]
                        if f is None or f[0] != m:
                            if f is not None and f[0] not in bars:
                                bars[f[0]] = f                                               # minute closed → completed bar
                                state["options_minute"][f[0]] = {k: dict(v) for k, v in state["options"].items()}
                            state["forming"] = [m, ltp, ltp, ltp, ltp]
                        else:
                            f[2] = max(f[2], ltp); f[3] = min(f[3], ltp); f[4] = ltp
                        atm = round(ltp / STEP) * STEP
                        if atm != state["atm"]:
                            state["atm"] = atm; want = want_keys(atm)
                            try:
                                subscribe(ws, want)
                            except Exception as e:
                                print("subscribe failed", e, flush=True)
                    else:
                        k = state["sid2key"].get(sid)
                        if k:
                            state["options"].setdefault(k[0], {})[k[1]] = round(ltp, 2)
        except Exception as e:
            print(f"feed: {datetime.now(IST).strftime('%H:%M:%S')} reconnecting after: {str(e)[:100]}", flush=True)
            if ws:
                ws.close()
            if datetime.now(IST).time() > datetime.strptime("15:35", "%H:%M").time():
                print("feed: session over", flush=True); return
            time.sleep(3)


if __name__ == "__main__":
    main()
