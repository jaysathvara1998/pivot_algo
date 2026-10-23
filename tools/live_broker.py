#!/usr/bin/env python3
"""Dhan order router for the LIVE mode of tools/paper_trade.py (stdlib only). One lot, market orders, INTRADAY product, NSE_FNO.
Never imported by the paper path. All order calls go through DhanBroker; LiveGuard holds the hard limits that the user agreed before going live.
Token: DHAN_ACCESS_TOKEN or ~/.dhan/access_token · client id: ~/.dhan/credentials.json (DHAN_CLIENT_ID). Dhan requires a whitelisted static IP for order APIs."""
from __future__ import annotations

import csv, json, os, socket, sys, time, urllib.error, urllib.request
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]; IST = ZoneInfo("Asia/Kolkata"); socket.setdefaulttimeout(20)

# Dhan whitelists the account's IPv4 address, but api.dhan.co is dual-stack and macOS prefers IPv6, so orders arrived from the IPv6 address and were
# rejected with DH-905 "Invalid IP" (2026-10-16). Force every connection from this process to IPv4.
_getaddrinfo = socket.getaddrinfo


def _ipv4_only(host, port, family=0, type=0, proto=0, flags=0):
    res = _getaddrinfo(host, port, socket.AF_INET, type, proto, flags)
    return res or _getaddrinfo(host, port, family, type, proto, flags)


socket.getaddrinfo = _ipv4_only
sys.path.insert(0, str(ROOT / "tools"))
from dhan_feed import scrip_options  # noqa: E402  (nearest-weekly NIFTY option security ids)

API = "https://api.dhan.co/v2"
WEEK_BRAKE = -10_000.0          # stop for the rest of the ISO week when the live week's realised P&L is at or below this
MAX_LOTS = 1


class BrokerError(Exception):
    pass


def token():
    return os.environ.get("DHAN_ACCESS_TOKEN") or (Path.home() / ".dhan" / "access_token").read_text().strip()


def client_id():
    p = Path.home() / ".dhan" / "credentials.json"; c = json.loads(p.read_text()) if p.exists() else {}
    cid = os.environ.get("DHAN_CLIENT_ID") or str(c.get("DHAN_CLIENT_ID") or "")
    if not cid:
        raise BrokerError("client id missing (~/.dhan/credentials.json DHAN_CLIENT_ID)")
    return cid


def _call(method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + path, data=data, method=method, headers={"access-token": token(), "client-id": client_id(), "Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            txt = r.read().decode()
            return json.loads(txt) if txt.strip() else {}
    except urllib.error.HTTPError as e:
        raise BrokerError(f"HTTP {e.code} {method} {path}: {e.read().decode()[:200]}")


class DhanBroker:
    """Market-order router. dry_run=True performs no order calls and returns synthetic fills at the reference price (used for replay tests)."""

    def __init__(self, d: date, dry_run: bool = False, say=print):
        self.d = d; self.dry_run = dry_run; self.say = say; self.ids, self.expiry = scrip_options(d); self.cid = None if dry_run else client_id()

    # ---- read-only checks
    def profile(self):
        return _call("GET", "/profile")

    def funds(self):
        f = _call("GET", "/fundlimit"); return float(f.get("availabelBalance") or f.get("availableBalance") or 0.0)

    def open_option_positions(self):
        """NSE_FNO positions with a non-zero net quantity (a stray position from a failed exit or a manual trade)."""
        pos = _call("GET", "/positions") or []
        return [p for p in pos if p.get("exchangeSegment") == "NSE_FNO" and int(float(p.get("netQty") or 0)) != 0]

    def resolve(self, strike: float, leg: str) -> str:
        sid = self.ids.get((float(strike), leg))
        if not sid:
            raise BrokerError(f"no NIFTY {self.expiry} {strike:.0f} {leg} in the scrip master")
        return str(sid)

    # ---- orders
    def _order(self, sid, side, qty, tag):
        body = {"dhanClientId": self.cid, "correlationId": tag[:30], "transactionType": side, "exchangeSegment": "NSE_FNO", "productType": "INTRADAY", "orderType": "MARKET", "validity": "DAY",
                "securityId": sid, "quantity": int(qty), "disclosedQuantity": 0, "price": 0, "triggerPrice": 0, "afterMarketOrder": False}
        r = _call("POST", "/orders", body); oid = r.get("orderId")
        if not oid:
            raise BrokerError(f"no orderId in response: {str(r)[:200]}")
        return str(oid)

    def _wait(self, oid, timeout=20):
        t0 = time.time(); last = None
        while time.time() - t0 < timeout:
            r = _call("GET", f"/orders/{oid}"); o = r[0] if isinstance(r, list) and r else (r if isinstance(r, dict) else {}); last = o
            st = str(o.get("orderStatus", "")).upper()
            if st == "TRADED":
                return {"order_id": oid, "price": float(o.get("averageTradedPrice") or 0.0), "qty": int(float(o.get("filledQty") or 0)), "status": st}
            if st in ("REJECTED", "CANCELLED", "EXPIRED"):
                raise BrokerError(f"order {oid} {st}: {o.get('omsErrorDescription') or ''}")
            time.sleep(1.0)
        raise BrokerError(f"order {oid} not filled within {timeout}s (last status {last and last.get('orderStatus')})")

    def market(self, side, sid, qty, ref_price, tag):
        if qty > MAX_LOTS * 65 * 2:                                   # hard cap: never more than MAX_LOTS lots (lot ≤ 130 safety)
            raise BrokerError(f"quantity {qty} exceeds the live cap")
        if self.dry_run:
            self.say(f"[DRY RUN] would {side} {qty} of security {sid} at market (ref ₹{ref_price:.2f}) tag {tag}")
            return {"order_id": f"DRY-{int(time.time())}", "price": float(ref_price) + (0.5 if side == "BUY" else -0.5), "qty": qty, "status": "TRADED"}
        oid = self._order(sid, side, qty, tag); self.say(f"LIVE {side} order {oid} placed for {qty} of {sid} (tag {tag})")
        return self._wait(oid)

    # ---- super orders: entry + broker-side target and stop-loss legs (exits are then executed by Dhan on every tick)
    def super_buy(self, sid, qty, ref_price, target_price, stop_price, tag, trailing_jump=0.0):
        tick = lambda x: round(round(x / 0.05) * 0.05, 2)
        if qty > MAX_LOTS * 65 * 2:
            raise BrokerError(f"quantity {qty} exceeds the live cap")
        if self.dry_run:
            self.say(f"[DRY RUN] would place SUPER BUY {qty} of {sid} at market, target ₹{tick(target_price):.2f}, stop ₹{tick(stop_price):.2f}")
            return {"order_id": f"DRY-S{int(time.time())}", "price": float(ref_price) + 0.5, "qty": qty, "target": tick(target_price), "stop": tick(stop_price)}
        body = {"dhanClientId": self.cid, "correlationId": tag[:30], "transactionType": "BUY", "exchangeSegment": "NSE_FNO", "productType": "INTRADAY", "orderType": "MARKET", "securityId": sid,
                "quantity": int(qty), "price": 0, "targetPrice": tick(target_price), "stopLossPrice": tick(stop_price), "trailingJump": float(trailing_jump)}
        r = _call("POST", "/super/orders", body); oid = str(r.get("orderId") or "")
        if not oid:
            raise BrokerError(f"no orderId in super order response: {str(r)[:200]}")
        self.say(f"LIVE SUPER order {oid} placed: BUY {qty} of {sid}, target ₹{tick(target_price):.2f}, stop ₹{tick(stop_price):.2f}")
        t0 = time.time()
        while time.time() - t0 < 25:
            st = self.super_status(oid); e = st.get("entry") or {}
            if str(e.get("orderStatus", "")).upper() == "TRADED":
                px = float(e.get("averageTradedPrice") or 0.0) or self.fill_price(sid, "BUY")
                return {"order_id": oid, "price": px, "qty": int(float(e.get("filledQty") or qty)), "target": tick(target_price), "stop": tick(stop_price)}
            if str(e.get("orderStatus", "")).upper() in ("REJECTED", "CANCELLED", "EXPIRED"):
                raise BrokerError(f"super order {oid} {e.get('orderStatus')}: {e.get('omsErrorDescription') or ''}")
            time.sleep(1.0)
        raise BrokerError(f"super order {oid} entry not filled within 25 s")

    def ltp(self, sid):
        """Fresh last traded price for one NSE_FNO security (REST, ~1 request/second allowed)."""
        q = _call("POST", "/marketfeed/ltp", {"NSE_FNO": [int(sid)]})["data"].get("NSE_FNO", {}).get(str(sid)) or {}
        return float(q.get("last_price") or 0.0)

    def fill_price(self, sid, side="BUY"):
        """Average traded price of today's position in this security from /positions (buyAvg or sellAvg); 0 if unknown."""
        for p in _call("GET", "/positions") or []:
            if str(p.get("securityId")) == str(sid):
                return float(p.get("buyAvg" if side == "BUY" else "sellAvg") or 0.0)
        return 0.0

    def modify_super_leg(self, oid, leg, price, trailing_jump=0.0):
        tick = round(round(price / 0.05) * 0.05, 2)
        body = {"dhanClientId": self.cid, "orderId": str(oid), "legName": leg, "targetPrice" if leg == "TARGET_LEG" else "stopLossPrice": tick}
        if leg == "STOP_LOSS_LEG":
            body["trailingJump"] = float(trailing_jump)
        return _call("PUT", f"/super/orders/{oid}", body)

    def super_status(self, oid):
        """{'entry': {...}, 'target': {...}, 'stop': {...}} for one super order from GET /super/orders (legDetails carry the exit legs)."""
        rows = _call("GET", "/super/orders") or []
        for o in rows:
            if str(o.get("orderId")) == str(oid):
                out = {"entry": o, "target": None, "stop": None}
                for leg in o.get("legDetails") or []:
                    nm = str(leg.get("legName", "")).upper()
                    if "TARGET" in nm:
                        out["target"] = leg
                    elif "STOP" in nm:
                        out["stop"] = leg
                return out
        return {}

    def super_cancel_exits(self, oid):
        """Cancel the pending target and stop legs (used before a time exit at market)."""
        out = []
        for leg in ("TARGET_LEG", "STOP_LOSS_LEG"):
            try:
                out.append(_call("DELETE", f"/super/orders/{oid}/{leg}"))
            except BrokerError as e:
                out.append(str(e)[:80])
        return out

    def buy(self, sid, qty, ref_price, tag):
        return self.market("BUY", sid, qty, ref_price, tag)

    def sell(self, sid, qty, ref_price, tag):
        return self.market("SELL", sid, qty, ref_price, tag)


class LiveGuard:
    """Hard limits for the live run, checked at start and before every entry."""

    def __init__(self, live_dir: Path, d: date):
        self.dir = live_dir; self.d = d

    def week_realised(self) -> float:
        iso = self.d.isocalendar()[:2]; total = 0.0
        for f in self.dir.glob("TRADES_*.csv"):
            try:
                fd = date.fromisoformat(f.stem.split("_")[1])
            except ValueError:
                continue
            if fd.isocalendar()[:2] == iso:
                with open(f, newline="") as fh:
                    for r in csv.DictReader(fh):
                        try:
                            total += float(r["net_inr"])
                        except (KeyError, ValueError):
                            pass
        return total

    def week_brake_hit(self) -> bool:
        return self.week_realised() <= WEEK_BRAKE


def preflight(d: date, dry_run: bool, say=print):
    """Read-only checks before a live session: token, funds, stray positions, weekly brake. Returns (ok, message)."""
    b = DhanBroker(d, dry_run=dry_run, say=say); g = LiveGuard(ROOT / "paper" / "live", d); msgs = [f"expiry {b.expiry}, {len(b.ids)} contracts mapped"]
    if g.week_brake_hit():
        return False, f"WEEK BRAKE: live P&L this week ₹{g.week_realised():+,.0f} ≤ ₹{WEEK_BRAKE:,.0f} — no live trading until next week"
    msgs.append(f"week realised ₹{g.week_realised():+,.0f}")
    if dry_run:
        return True, " · ".join(msgs + ["DRY RUN (no orders)"])
    p = b.profile(); msgs.append(f"token ok (client {p.get('dhanClientId', '?')}, valid to {str(p.get('tokenValidity', '?'))[:16]})")
    try:
        f = b.funds(); msgs.append(f"available funds ₹{f:,.0f}")
        if f < 12_000:
            return False, " · ".join(msgs + ["insufficient funds for one lot"])
    except BrokerError as e:
        msgs.append(f"funds check failed ({str(e)[:60]})")
    stray = b.open_option_positions()
    if stray:
        return False, " · ".join(msgs + [f"STRAY OPEN POSITION on the account: {[(x.get('tradingSymbol'), x.get('netQty')) for x in stray]} — close it manually before starting live"])
    return True, " · ".join(msgs + ["no open option positions"])


if __name__ == "__main__":
    ok, msg = preflight(date.today(), dry_run="--dry-run" in sys.argv); print(("OK " if ok else "BLOCKED ") + msg); sys.exit(0 if ok else 1)
