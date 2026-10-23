"""Skill 39 — VWAP pullback entries (touch-and-hold / reclaim) with structural stops; index-side trades. Option pricing and summaries reuse engine.emavwap."""
from __future__ import annotations

from datetime import time

from engine.emavwap import session_twap

RUN_MIN, ROOM_R, TOUCH_R, RECLAIM_BARS, COOL_R, COOL_BARS = 20, 2.0, 0.5, 3, 1.0, 5
FIRST_ENTRY, LAST_ENTRY, TIME_EXIT = time(9, 45), time(15, 0), time(15, 15)


def pullback_signals(bars, refs, variant: str):
    """[(i, side, run_extreme, vwap_i, first_of_session)] on 1m bars. variant 'A' touch-and-hold or 'B' reclaim. Session state resets on date change.
    Episode rule: after an entry, no new signal until the close has been >= COOL_R * R from VWAP for COOL_BARS consecutive bars."""
    tw = session_twap(bars); out = []; cur = None
    side = 0; run = 0; room = 0.0; ext = None; broke_at = None; broke_side = 0; broke_ext = None; blocked = False; away = 0; taken_today = 0
    for i, b in enumerate(bars):
        if b.timestamp.date() != cur:
            cur = b.timestamp.date(); side = 0; run = 0; room = 0.0; ext = None; broke_at = None; blocked = False; away = 0; taken_today = 0
        v = tw[i]; R = refs[i]; s = 1 if b.close > v else (-1 if b.close < v else 0)
        if blocked:
            away = away + 1 if (R and abs(b.close - v) >= COOL_R * R) else 0
            if away >= COOL_BARS:
                blocked = False; away = 0
        ok_time = FIRST_ENTRY <= b.timestamp.time() <= LAST_ENTRY and bool(R)
        signal = None
        if variant == "B" and broke_at is not None:
            if i - broke_at <= RECLAIM_BARS and s == broke_side:
                signal = (broke_side, broke_ext); broke_at = None
            elif i - broke_at > RECLAIM_BARS:
                broke_at = None
        if s != 0 and s == side:
            run += 1; dist = (b.close - v) * s; room = max(room, dist)
            ext = (max(ext, b.high) if s > 0 else min(ext, b.low)) if ext is not None else (b.high if s > 0 else b.low)
            qualified = run >= RUN_MIN and R and room >= ROOM_R * R
            touched = (b.low <= v + TOUCH_R * R) if (s > 0 and R) else ((b.high >= v - TOUCH_R * R) if R else False)
            if variant == "A" and qualified and touched:
                signal = (s, ext)
        else:
            if side != 0 and run >= RUN_MIN and R and room >= ROOM_R * R and variant == "B" and s == -side:
                broke_at = i; broke_side = side; broke_ext = ext
            side = s; run = 1 if s != 0 else 0; room = (b.close - v) * s if s != 0 else 0.0; ext = (b.high if s > 0 else b.low) if s != 0 else None
        if signal and ok_time and not blocked:
            out.append((i, signal[0], signal[1], v, taken_today == 0)); taken_today += 1; blocked = True; away = 0
    return out


def pb_trade(bars, refs, i, side, run_ext, vwap_i, exit_rule):
    """Index trade from bar i's close. P1: stop VWAP ∓ 1 R, target 2× risk; P2: stop same, target run extreme (≥ 1 R); V: stop only; all 15:15."""
    c0 = bars[i]; R = refs[i]; e = c0.close; d0 = c0.timestamp.date()
    if not R:
        return None
    stop_lvl = (vwap_i - R) if side > 0 else (vwap_i + R); risk = side * (e - stop_lvl)
    if risk <= 0:
        return None
    if exit_rule == "P1":
        tgt = e + side * 2 * risk
    elif exit_rule == "P2":
        tgt = run_ext if side * (run_ext - e) >= R else e + side * R
    else:
        tgt = None
    for j in range(i + 1, len(bars)):
        b = bars[j]
        if b.timestamp.date() != d0:
            break
        mv = side * (b.close - e); why = None
        if side * (b.close - stop_lvl) <= 0:
            why = "STOP"
        elif tgt is not None and side * (b.close - tgt) >= 0:
            why = "TARGET"
        elif b.timestamp.time() >= TIME_EXIT:
            why = "TIME"
        if why:
            return {"date": d0.isoformat(), "entry": c0.timestamp.strftime("%H:%M"), "exit": b.timestamp.strftime("%H:%M"), "dir": side, "move_pts": round(mv, 2), "move_R": round(mv / R, 3), "why": why,
                    "hold_min": j - i, "risk_pts": round(risk, 2), "risk_R": round(risk / R, 2), "exit_j": j}
    return None
