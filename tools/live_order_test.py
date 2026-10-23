#!/usr/bin/env python3
"""One-off test of the Dhan order path used by LIVE mode. Run it yourself (it is deliberately not started by the engine):
  python3 tools/live_order_test.py                 → shows the contract it would use and the current LTP; places nothing
  python3 tools/live_order_test.py --probe         → places a LIMIT BUY at ₹0.05 (cannot fill) and CANCELS it: proves IP whitelist + order placement + cancel, zero cost
  python3 tools/live_order_test.py --roundtrip     → MARKET BUY 1 lot of a cheap far-OTM NIFTY option, then MARKET SELL it: proves the full path incl. fills (costs ≈ premium spread + ₹50–100 charges)
Uses the same DhanBroker as the live trader (INTRADAY, NSE_FNO, 1 lot)."""
import sys, time
from datetime import date
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
import live_broker as lb

STEP = 50; LOT = 65


def main():
    b = lb.DhanBroker(date.today(), dry_run=False); spot = lb._call("POST", "/marketfeed/ltp", {"IDX_I": [13]})["data"]["IDX_I"]["13"]["last_price"]
    atm = round(spot / STEP) * STEP; cands = [(atm + k * STEP, "CE") for k in range(8, 21, 2)]          # 400–1000 pts out of the money
    ids = {b.resolve(K, leg): (K, leg) for K, leg in cands if (float(K), leg) in b.ids}
    time.sleep(1.1); q = lb._call("POST", "/marketfeed/ltp", {"NSE_FNO": [int(s) for s in ids]})["data"].get("NSE_FNO", {})
    priced = sorted(((float(v["last_price"]), ids[s][0], ids[s][1], s) for s, v in q.items() if v.get("last_price")), key=lambda x: x[0])
    pick = next((p for p in priced if 0.5 <= p[0] <= 6.0), priced[0] if priced else None)
    if not pick:
        sys.exit("no priced far-OTM contract found (market closed?)")
    ltp, K, leg, sid = pick; print(f"spot {spot:.1f} · test contract NIFTY {b.expiry} {K:.0f} {leg} (security {sid}) · LTP ₹{ltp:.2f} · 1 lot = ₹{ltp * LOT:,.0f}")
    if "--probe" in sys.argv:
        body = {"dhanClientId": b.cid, "correlationId": "probe-limit", "transactionType": "BUY", "exchangeSegment": "NSE_FNO", "productType": "INTRADAY", "orderType": "LIMIT", "validity": "DAY",
                "securityId": sid, "quantity": LOT, "disclosedQuantity": 0, "price": 0.05, "triggerPrice": 0, "afterMarketOrder": False}
        r = lb._call("POST", "/orders", body); oid = r.get("orderId"); print("placed LIMIT BUY @ ₹0.05 →", r)
        time.sleep(1.5); o = lb._call("GET", f"/orders/{oid}"); o = o[0] if isinstance(o, list) and o else o; print("status:", o.get("orderStatus"), o.get("omsErrorDescription") or "")
        c = lb._call("DELETE", f"/orders/{oid}"); print("cancel →", c)
        time.sleep(1.5); o = lb._call("GET", f"/orders/{oid}"); o = o[0] if isinstance(o, list) and o else o; print("final status:", o.get("orderStatus"))
        print("PROBE RESULT:", "order path works (placed + cancelled, nothing filled)" if str(o.get("orderStatus")).upper() in ("CANCELLED", "REJECTED") else "check the order book on Dhan")
    elif "--roundtrip" in sys.argv:
        buy = b.buy(sid, LOT, ltp, "test-B"); print("BUY filled:", buy)
        time.sleep(2.0); sell = b.sell(sid, LOT, ltp, "test-S"); print("SELL filled:", sell)
        print(f"ROUNDTRIP RESULT: bought ₹{buy['price']:.2f} sold ₹{sell['price']:.2f} → gross ₹{(sell['price'] - buy['price']) * LOT:+,.0f} before ≈ ₹50–100 charges; open positions now: {b.open_option_positions()}")
    else:
        print("nothing placed. Add --probe (zero-cost limit+cancel) or --roundtrip (real buy+sell).")


if __name__ == "__main__":
    main()
