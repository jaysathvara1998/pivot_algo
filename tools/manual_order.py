#!/usr/bin/env python3
"""Manual test order on Dhan (REAL money) to check the order API end to end: a LIMIT BUY of 1 lot of an out-of-the-money NIFTY weekly option a few rupees
below the last price (so it normally rests as PENDING and can be cancelled at no cost), plus cancel and square-off. Backs the dashboard's "Manual test order"
card; also runnable by hand. Every command prints ONE JSON object.
  python3 tools/manual_order.py quote     --leg CE --otm 2 --below 12   → contract, LTP, the limit it would use; places nothing
  python3 tools/manual_order.py place     --leg CE --otm 2 --below 12   → LIMIT BUY 1 lot at LTP − 12 (tick 0.05), INTRADAY, DAY validity
  python3 tools/manual_order.py orders                                   → today's orders from the order book
  python3 tools/manual_order.py cancel    --order-id ID
  python3 tools/manual_order.py squareoff --order-id ID                  → MARKET SELL of that order's filled quantity (only if the position is still open)
Guards: 1 lot only, 1–10 strikes OTM, ₹1–50 below LTP, market hours only (09:15–15:30 IST on a weekday; --force to bypass the clock check).
Same DhanBroker / IPv4-forced HTTP path as the live trader, so a passing test here means the live trader's orders will reach Dhan too."""
from __future__ import annotations

import argparse, json, math, sys, time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo
ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_broker as lb  # noqa: E402
from engine.premium.ironfly import lot_on  # noqa: E402

IST = ZoneInfo("Asia/Kolkata"); STEP = 50; TICK = 0.05; TAG = "manual"


def out(obj, code=0):
    print(json.dumps(obj, default=str)); sys.exit(code)


def market_open(now=None):
    now = now or datetime.now(IST); t = now.hour * 60 + now.minute
    return now.weekday() < 5 and 9 * 60 + 15 <= t <= 15 * 60 + 30


def spot():
    return float(lb._call("POST", "/marketfeed/ltp", {"IDX_I": [13]})["data"]["IDX_I"]["13"]["last_price"])


def quote(leg, otm, below):
    leg = leg.upper()
    if leg not in ("CE", "PE"):
        raise lb.BrokerError("leg must be CE or PE")
    if not 1 <= otm <= 10:
        raise lb.BrokerError("strikes OTM must be 1–10")
    if not 1 <= below <= 50:
        raise lb.BrokerError("₹ below LTP must be 1–50")
    b = lb.DhanBroker(date.today(), dry_run=False); s = spot(); atm = round(s / STEP) * STEP
    K = atm + otm * STEP if leg == "CE" else atm - otm * STEP; sid = b.resolve(K, leg)
    time.sleep(1.1); ltp = b.ltp(sid)                                                   # marketfeed allows ~1 request/second
    if ltp <= 0:
        raise lb.BrokerError(f"no last price for NIFTY {b.expiry} {K:.0f} {leg} (market closed or illiquid)")
    limit = max(TICK, math.floor((ltp - below) / TICK + 1e-9) * TICK); lot = lot_on("NIFTY", date.today())
    return {"leg": leg, "otm": otm, "below": below, "spot": round(s, 2), "atm": atm, "strike": int(K), "expiry": b.expiry, "sid": sid, "ltp": round(ltp, 2), "limit": round(limit, 2), "lot": lot,
            "cost": round(limit * lot), "market_open": market_open(), "warning": None if market_open() else "market is closed: Dhan will reject or hold the order", "cid": b.cid}


def order_row(o):
    return {"order_id": str(o.get("orderId", "")), "status": str(o.get("orderStatus", "")).upper(), "side": o.get("transactionType"), "type": o.get("orderType"), "product": o.get("productType"),
            "symbol": o.get("tradingSymbol") or o.get("securityId"), "sid": str(o.get("securityId", "")), "qty": int(float(o.get("quantity") or 0)), "filled": int(float(o.get("filledQty") or 0)),
            "price": float(o.get("price") or 0), "avg": float(o.get("averageTradedPrice") or 0) or None, "time": str(o.get("updateTime") or o.get("createTime") or "")[-8:],
            "tag": o.get("correlationId"), "error": o.get("omsErrorDescription") or None}


def get_order(oid):
    r = lb._call("GET", f"/orders/{oid}"); return r[0] if isinstance(r, list) and r else (r if isinstance(r, dict) else {})


def place(leg, otm, below, force):
    q = quote(leg, otm, below)
    if not q["market_open"] and not force:
        raise lb.BrokerError("market is closed (09:15–15:30 IST, Mon–Fri); use --force to send anyway")
    body = {"dhanClientId": q["cid"], "correlationId": f"{TAG}-{q['leg']}{q['strike']}-{datetime.now(IST).strftime('%H%M%S')}"[:30], "transactionType": "BUY", "exchangeSegment": "NSE_FNO", "productType": "INTRADAY",
            "orderType": "LIMIT", "validity": "DAY", "securityId": q["sid"], "quantity": int(q["lot"]), "disclosedQuantity": 0, "price": q["limit"], "triggerPrice": 0, "afterMarketOrder": False}
    if body["quantity"] > lb.MAX_LOTS * q["lot"]:
        raise lb.BrokerError("quantity exceeds the 1-lot cap")
    r = lb._call("POST", "/orders", body); oid = r.get("orderId")
    if not oid:
        raise lb.BrokerError(f"no orderId in response: {str(r)[:200]}")
    time.sleep(1.5); o = order_row(get_order(oid))
    return {"placed": True, "order_id": str(oid), "status": o["status"] or str(r.get("orderStatus", "")), "error": o["error"], "quote": q, "order": o}


def orders():
    rows = [order_row(o) for o in (lb._call("GET", "/orders") or []) if isinstance(o, dict)]
    rows.sort(key=lambda x: x["time"], reverse=True); return {"orders": rows, "count": len(rows)}


def cancel(oid):
    r = lb._call("DELETE", f"/orders/{oid}"); time.sleep(1.5); o = order_row(get_order(oid))
    return {"cancelled": o["status"] in ("CANCELLED", "REJECTED"), "order_id": str(oid), "status": o["status"] or str(r.get("orderStatus", "")), "response": r}


def squareoff(oid):
    o = order_row(get_order(oid))
    if o["side"] != "BUY" or o["filled"] <= 0:
        raise lb.BrokerError(f"order {oid} has no filled BUY quantity (status {o['status']}, filled {o['filled']})")
    b = lb.DhanBroker(date.today(), dry_run=False)
    net = next((int(float(p.get("netQty") or 0)) for p in b.open_option_positions() if str(p.get("securityId")) == o["sid"]), 0)
    if net <= 0:
        raise lb.BrokerError(f"no open long position in {o['symbol']} (net {net}); nothing to square off")
    qty = min(o["filled"], net); ref = o["avg"] or o["price"]; fill = b.market("SELL", o["sid"], qty, ref, f"{TAG}-sq-{oid}"[:30])
    return {"sold": True, "order_id": fill["order_id"], "qty": fill["qty"], "price": fill["price"], "bought_at": ref, "gross": round((fill["price"] - ref) * qty, 2)}


def main():
    ap = argparse.ArgumentParser(); sub = ap.add_subparsers(dest="cmd", required=True)
    for n in ("quote", "place"):
        s = sub.add_parser(n); s.add_argument("--leg", required=True); s.add_argument("--otm", type=int, default=2); s.add_argument("--below", type=float, default=12.0); s.add_argument("--force", action="store_true")
    sub.add_parser("orders")
    for n in ("cancel", "squareoff"):
        s = sub.add_parser(n); s.add_argument("--order-id", required=True)
    a = ap.parse_args()
    try:
        if a.cmd == "quote":
            out(quote(a.leg, a.otm, a.below))
        if a.cmd == "place":
            out(place(a.leg, a.otm, a.below, a.force))
        if a.cmd == "orders":
            out(orders())
        if a.cmd == "cancel":
            out(cancel(a.order_id))
        if a.cmd == "squareoff":
            out(squareoff(a.order_id))
    except SystemExit:
        raise
    except Exception as e:
        out({"error": str(e)[:300]}, 1)


if __name__ == "__main__":
    main()
