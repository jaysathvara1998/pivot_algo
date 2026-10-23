"""Skill 41 part 2 — the canonical ICT-2022 variant (HYPOTHESES_B.md): dealing-range bias, significant pools, body displacement + MSS through the pre-raid swing, CE limit entry in discount/premium,
T1 internal / T2 external targets with a break-even runner, kill zones."""
from __future__ import annotations

from datetime import time, timedelta

from engine.pa2 import Bars

DISP_MULT, BODY_SHARE, ORDER_BARS, LOOKBACK, EQ_TOL = 1.5, 0.60, 24, 150, 0.3
KZ = ((time(9, 20), time(10, 30)), (time(13, 30), time(14, 30)))
TIME_EXIT = time(15, 15)


def dealing_range(bars_k, n_days=10):
    """(H10, L10) of the prior n sessions at each k-bar (today excluded)."""
    out = []; days = []; cur = None; hi = lo = None
    for b in bars_k:
        if b.timestamp.date() != cur:
            if cur is not None:
                days.append((hi, lo)); days = days[-n_days:]
            cur = b.timestamp.date(); hi, lo = b.high, b.low
        hi = max(hi, b.high); lo = min(lo, b.low)
        out.append((max(h for h, _ in days), min(l for _, l in days)) if days else (None, None))
    return out


def session_levels(bars_k):
    """Per k-bar: (prev-day high, prev-day low, opening-range high, opening-range low) — OR levels valid after 09:30."""
    out = []; cur = None; hi = lo = None; phi = plo = None; orh = orl = None
    for b in bars_k:
        if b.timestamp.date() != cur:
            if cur is not None:
                phi, plo = hi, lo
            cur = b.timestamp.date(); hi, lo = b.high, b.low; orh = orl = None
        hi = max(hi, b.high); lo = min(lo, b.low)
        if b.timestamp.time() < time(9, 30):
            orh = b.high if orh is None else max(orh, b.high); orl = b.low if orl is None else min(orl, b.low)
        out.append((phi, plo, orh if b.timestamp.time() >= time(9, 30) else None, orl if b.timestamp.time() >= time(9, 30) else None))
    return out


def canon_signals_k(bars_k, cfg=None):
    """[(fill_k_index, side, entry_price, stop, t1, t2, raid_k, meta)] on k-bars. cfg toggles: bias, sig_pools, ce_entry, body, kill_zone."""
    c = {"bias": True, "sig_pools": True, "ce_entry": True, "body": True, "kill_zone": False}; c.update(cfg or {})
    B = Bars([b.open for b in bars_k], [b.high for b in bars_k], [b.low for b in bars_k], [b.close for b in bars_k]); n = len(bars_k)
    lows = sorted(B.sw_lo, key=lambda x: x[1]); highs = sorted(B.sw_hi, key=lambda x: x[1]); dr = dealing_range(bars_k); sl = session_levels(bars_k); out = []; used = set()
    for side in (1, -1):
        sw_same = lows if side > 0 else highs; sw_opp = highs if side > 0 else lows
        for s in range(30, n - 3):
            b = bars_k[s]; H10, L10 = dr[s]
            if H10 is None:
                continue
            eq = (H10 + L10) / 2
            if c["bias"] and ((side > 0 and b.close >= eq) or (side < 0 and b.close <= eq)):
                continue
            ref = B.ref[s]
            if not ref:
                continue
            # pools
            phi, plo, orh, orl = sl[s]; cands = []
            if c["sig_pools"]:
                if side > 0:
                    cands += [(plo, ("pdl", b.timestamp.date())), (orl, ("orl", b.timestamp.date()))]
                else:
                    cands += [(phi, ("pdh", b.timestamp.date())), (orh, ("orh", b.timestamp.date()))]
                recent = [(pi, lvl) for pi, ci, lvl in sw_same if ci <= s - 1 and s - pi <= LOOKBACK]
                for a_i in range(len(recent)):
                    for b_i in range(a_i + 1, len(recent)):
                        if abs(recent[a_i][1] - recent[b_i][1]) <= EQ_TOL * ref:
                            lvl = (min if side > 0 else max)(recent[a_i][1], recent[b_i][1]); cands.append((lvl, ("eq", round(lvl, 1))))
            else:
                cands += [(lvl, ("sw", pi)) for pi, ci, lvl in sw_same if ci <= s - 1 and s - pi <= LOOKBACK]
                cands += [(plo if side > 0 else phi, ("pd", b.timestamp.date()))]
            cands = [(lvl, key) for lvl, key in cands if lvl is not None and key not in used]
            prev_close = bars_k[s - 1].close
            if side > 0:
                cands = [(lvl, key) for lvl, key in cands if lvl < prev_close and b.low < lvl < b.close]
                if not cands:
                    continue
                lvl, key = max(cands)
            else:
                cands = [(lvl, key) for lvl, key in cands if lvl > prev_close and b.high > lvl > b.close]
                if not cands:
                    continue
                lvl, key = min(cands)
            # MSS reference: most recent confirmed opposing swing before the raid; fallback the 3-bar extreme
            pre = [l_ for pi, ci, l_ in sw_opp if ci <= s and s - pi <= 40]
            mss_lvl = pre[-1] if pre else (max(bars_k[x].high for x in range(s - 3, s)) if side > 0 else min(bars_k[x].low for x in range(s - 3, s)))
            found = None
            for j in range(s, min(s + 4, n - 1)):
                bj = bars_k[j]; rj = B.ref[j]
                if not rj:
                    continue
                rng = bj.high - bj.low; body = abs(bj.close - bj.open)
                big = rng >= DISP_MULT * rj and ((bj.close > bj.open) if side > 0 else (bj.close < bj.open)) and (not c["body"] or (rng > 0 and body / rng >= BODY_SHARE))
                if side > 0:
                    fvg = bars_k[j + 1].low > bars_k[j - 1].high; mss = bars_k[j + 1].close > mss_lvl; zone = (bars_k[j - 1].high, bars_k[j + 1].low)
                else:
                    fvg = bars_k[j + 1].high < bars_k[j - 1].low; mss = bars_k[j + 1].close < mss_lvl; zone = (bars_k[j + 1].high, bars_k[j - 1].low)
                if big and fvg and mss:
                    found = (j, zone); break
            if not found:
                continue
            j, (zlo, zhi) = found; ce = (zlo + zhi) / 2
            if c["bias"] and ((side > 0 and ce >= eq) or (side < 0 and ce <= eq)):
                continue                                                          # entry zone must be in discount / premium
            stop = b.low if side > 0 else b.high; fill = None; price = None
            for m in range(j + 2, min(j + 2 + ORDER_BARS, n)):
                bm = bars_k[m]
                if (side > 0 and bm.close < stop) or (side < 0 and bm.close > stop):
                    break
                if c["ce_entry"]:
                    if (side > 0 and bm.close < zlo) or (side < 0 and bm.close > zhi):
                        break                                                     # body close through the far side: FVG invalidated
                    if (side > 0 and bm.low <= ce) or (side < 0 and bm.high >= ce):
                        fill, price = m, ce; break
                else:
                    touch = (bm.low <= zhi and bm.close > zlo) if side > 0 else (bm.high >= zlo and bm.close < zhi)
                    if touch:
                        fill, price = m, bm.close; break
            if fill is None:
                continue
            t = bars_k[fill].timestamp.time()
            if not (time(9, 20) <= t <= time(15, 0)):
                continue
            if c["kill_zone"] and not any(a <= t <= b_ for a, b_ in KZ):
                continue
            risk = side * (price - stop)
            if risk <= 0:
                continue
            R1 = B.ref[fill] or ref
            opp = [l_ for pi, ci, l_ in sw_opp if ci <= fill and side * (l_ - price) >= R1]
            t1 = (min(opp) if side > 0 else max(opp)) if opp else price + side * 1.5 * risk
            draw = H10 if side > 0 else L10; pdx = phi if side > 0 else plo
            if side * (draw - t1) >= R1:
                t2 = draw
            elif pdx is not None and side * (pdx - t1) >= R1:
                t2 = pdx
            else:
                t2 = t1 + side * risk
            used.add(key); out.append((fill, side, price, stop, t1, t2, s, {"ce": ce, "eq": eq, "pool": key[0]}))
    return sorted(out)


def canon_trade(bars1m, refs, i_fill, side, price, stop, t1, t2, exit_rule):
    """Index trade from the fill minute at `price`. X1: exit at T1 / stop / 15:15. X2: runner — after T1 is touched the stop becomes break-even; exit at T2 / stop / 15:15. HALF: ½ at T1 + ½ as X2."""
    R = refs[i_fill]; d0 = bars1m[i_fill].timestamp.date()
    if not R:
        return None
    cur_stop = stop; t1_hit = False; half_pnl = None
    for j in range(i_fill + 1, len(bars1m)):
        b = bars1m[j]
        if b.timestamp.date() != d0:
            break
        mv = side * (b.close - price); why = None
        if side * (b.close - cur_stop) <= 0:
            why = "BE_STOP" if (t1_hit and cur_stop == price) else "STOP"
        elif exit_rule == "X1" and side * (b.close - t1) >= 0:
            why = "T1"
        elif exit_rule != "X1":
            if not t1_hit and side * (b.close - t1) >= 0:
                t1_hit = True; cur_stop = price; half_pnl = side * (t1 - price)
            if t1_hit and side * (b.close - t2) >= 0:
                why = "T2"
        if why is None and b.timestamp.time() >= TIME_EXIT:
            why = "TIME"
        if why:
            move = mv if exit_rule != "HALF" else (0.5 * (half_pnl if half_pnl is not None else mv) + 0.5 * mv)
            return {"date": d0.isoformat(), "entry": bars1m[i_fill].timestamp.strftime("%H:%M"), "exit": b.timestamp.strftime("%H:%M"), "dir": side, "move_pts": round(move, 2), "move_R": round(move / R, 3), "why": why,
                    "hold_min": j - i_fill, "risk_pts": round(side * (price - stop), 2), "risk_R": round(side * (price - stop) / R, 2), "exit_j": j, "t1_hit": t1_hit, "entry_price": round(price, 2)}
    return None
