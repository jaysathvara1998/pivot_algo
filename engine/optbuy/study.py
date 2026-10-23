"""Option-buying study on the directional signals (PROJECT DEFINITION, Skill 30; see skills/30_option_buying/HYPOTHESES.md)."""
from __future__ import annotations

import math
from collections import defaultdict
from dataclasses import dataclass, asdict
from datetime import date, datetime, timedelta

from engine.backtest import BtTrade, backtest_report
from engine.premium.ironfly import lot_on
from engine.session.labels import expiry_flags, regime_of, phase_of
from engine.setups import bonferroni_z
from .pricer import price_at, STEP

CAPITAL = 50_000.0; MAX_PREMIUM_FRACTION = 0.25; STOP_FRACTION = 0.30; SLIP = 0.5
STREAK_EXITS = ("BAR_1", "BAR_3", "SESSION"); GAP_EXITS = ("TARGET", "MIN_60", "SESSION"); STRADDLE_EXITS = ("MIN_60", "SESSION")
STRIKES = ("ATM", "OTM1")


def leg_cost_per_unit(d: date, lot: int, buy_px: float, sell_px: float) -> float:
    brok = 2 * 20.0 * 1.18 / lot; stt = (0.0015 if d >= date(2024, 10, 1) else 0.0010) * sell_px; exch = 0.000353 * 1.18 * (buy_px + sell_px)
    return brok + stt + exch + 2 * SLIP


@dataclass
class OptTrade:
    date: str; index: str; signal: str; strike_rule: str; exit_rule: str; legs: str; K: float; entry_ts: str; exit_ts: str; premium_in: float; premium_out: float
    exit_reason: str; pnl_unit: float; cost_unit: float; net_unit: float; ret_on_premium: float; r_net: float; mark: str; lot: int; lots: int; pnl_inr: float; window: str

    def as_dict(self):
        return asdict(self)


def _minute(ts: datetime, k: int) -> str:
    return (ts + timedelta(minutes=k)).strftime("%Y-%m-%d %H:%M")


def _walk(index, legs, K_by_leg, start_ts, end_ts, atm, wings, target_fn=None):
    """Hold from start (entry price at start row) to end; −30 % stop on the combined premium; optional target_fn(minute_ts) → True to exit. Returns (p_in, p_out, exit_ts, reason, marks)."""
    marks = set(); p_in = 0.0
    for leg in legs:
        r = price_at(index, start_ts.strftime("%Y-%m-%d %H:%M"), K_by_leg[leg], leg, atm, wings)
        if r is None:
            return None
        p_in += r[0]; marks.add(r[1])
    t = start_ts + timedelta(minutes=1)
    while t <= end_ts:
        k16 = t.strftime("%Y-%m-%d %H:%M"); tot = 0.0; ok = True
        for leg in legs:
            r = price_at(index, k16, K_by_leg[leg], leg, atm, wings)
            if r is None:
                ok = False; break
            tot += r[0]; marks.add(r[1])
        if ok:
            if tot <= (1 - STOP_FRACTION) * p_in:
                return p_in, tot, k16, "STOP", marks
            if target_fn is not None and target_fn(t):
                return p_in, tot, k16, "TARGET", marks
            if t == end_ts:
                return p_in, tot, k16, "TIME", marks
        t += timedelta(minutes=1)
    return None


def _trade(index, d, signal, strike_rule, exit_rule, legs, K_by_leg, start_ts, end_ts, atm, wings, window, target_fn=None):
    w = _walk(index, legs, K_by_leg, start_ts, end_ts, atm, wings, target_fn)
    if w is None:
        return None
    p_in, p_out, xts, reason, marks = w
    lot = lot_on(index, d); n_legs = len(legs)
    cost = sum(leg_cost_per_unit(d, lot, p_in / n_legs, p_out / n_legs) for _ in legs)
    pnl = p_out - p_in; net = pnl - cost; risk = STOP_FRACTION * p_in
    lots = 1 if p_in * lot <= MAX_PREMIUM_FRACTION * CAPITAL and p_in > 0 else 0
    mark = "BS" if "BS" in marks else "PARITY" if "PARITY" in marks else "OBS"
    return OptTrade(d.isoformat(), index, signal, strike_rule, exit_rule, "+".join(legs), K_by_leg[legs[0]], start_ts.strftime("%H:%M"), xts[11:], round(p_in, 2), round(p_out, 2), reason,
                    round(pnl, 2), round(cost, 3), round(net, 2), round(pnl / p_in, 4) if p_in else 0.0, round(net / risk, 4) if risk else 0.0, mark, lot, lots, round(net * lot * lots, 2), window)


def _K(index, atm_row, leg, strike_rule):
    K0 = atm_row["strike"]
    if strike_rule == "ATM":
        return K0
    return K0 + STEP[index] if leg == "CE" else K0 - STEP[index]


def streak_trades(index, stream5, atm, wings, window_of, strike_rule, exit_rule, baseline=False):
    """Signal rows: RUN_DOWN_5+ → CE, RUN_UP_5+ → PE. baseline=True: unconditioned entries at stride (3 bars) alternating CE / PE."""
    out = []; last = -10 ** 9
    for i, row in enumerate(stream5):
        labs = set(row["labels"]); c = row["candle"]
        if baseline:
            if i <= last + 2:
                continue
            leg = "CE" if i % 2 == 0 else "PE"; sig = "BASE_STREAK"
        else:
            if "RUN_DOWN_5+" in labs:
                leg, sig = "CE", "S1_RUN_DOWN_5+"
            elif "RUN_UP_5+" in labs:
                leg, sig = "PE", "S2_RUN_UP_5+"
            else:
                continue
        last = i
        start = c.timestamp + timedelta(minutes=4)                      # option row ending at the 5m bar's close
        a = atm.get(start.strftime("%Y-%m-%d %H:%M"))
        if a is None:
            continue
        hold = {"BAR_1": 5, "BAR_3": 15}.get(exit_rule)
        end = start + timedelta(minutes=hold) if hold else c.timestamp.replace(hour=15, minute=28)
        if end.time() > datetime.min.replace(hour=15, minute=28).time() or end.date() != start.date():
            end = c.timestamp.replace(hour=15, minute=28)
        if end <= start:
            continue
        K = _K(index, a, leg, strike_rule)
        t = _trade(index, c.timestamp.date(), sig, strike_rule, exit_rule, (leg,), {leg: K}, start, end, atm, wings, window_of(c.timestamp.date()))
        if t:
            out.append(t)
    return out


def gap_trades(index, sessions, atm, wings, window_of, strike_rule, exit_rule, baseline=False):
    """sessions: engine.strategy.Session list (gap fields + 1m bars). SMALL UP → PE, SMALL DOWN → CE; baseline: every session, PE on even sessions, CE on odd."""
    out = []
    for n, s in enumerate(sessions):
        if s.prev_close is None or len(s.bars) < 70:
            continue
        if baseline:
            leg = "PE" if n % 2 == 0 else "CE"; sig = "BASE_GAP"
        elif s.gap_class == "SMALL" and s.gap_sign == "UP":
            leg, sig = "PE", "S3_GAP_SMALL_UP"
        elif s.gap_class == "SMALL" and s.gap_sign == "DOWN":
            leg, sig = "CE", "S4_GAP_SMALL_DOWN"
        else:
            continue
        start = s.bars[4].timestamp                                      # row 09:19 ≈ 09:20 price
        a = atm.get(start.strftime("%Y-%m-%d %H:%M"))
        if a is None:
            continue
        end = s.bars[0].timestamp.replace(hour=10, minute=14) if exit_rule == "MIN_60" else s.bars[0].timestamp.replace(hour=15, minute=28)
        if exit_rule == "TARGET":
            end = s.bars[0].timestamp.replace(hour=15, minute=28)
        touched = {}
        for b in s.bars[5:]:
            if b.low <= s.prev_close <= b.high:
                touched[b.timestamp.strftime("%Y-%m-%d %H:%M")] = True
        target_fn = (lambda t: t.strftime("%Y-%m-%d %H:%M") in touched) if (exit_rule == "TARGET" and not baseline) else ((lambda t: t.strftime("%Y-%m-%d %H:%M") in touched) if exit_rule == "TARGET" else None)
        K = _K(index, a, leg, strike_rule)
        t = _trade(index, s.date, sig, strike_rule, exit_rule, (leg,), {leg: K}, start, end, atm, wings, window_of(s.date), target_fn)
        if t:
            out.append(t)
    return out


def straddle_trades(index, sessions, atm, wings, window_of, exit_rule, baseline=False):
    out = []
    for s in sessions:
        if len(s.bars) < 70:
            continue
        is_exp = bool(expiry_flags(s.date).get(f"{index.lower()}_weekly_expiry"))
        if baseline == is_exp:
            continue
        start = s.bars[4].timestamp; a = atm.get(start.strftime("%Y-%m-%d %H:%M"))
        if a is None:
            continue
        end = s.bars[0].timestamp.replace(hour=10, minute=14) if exit_rule == "MIN_60" else s.bars[0].timestamp.replace(hour=15, minute=28)
        K = a["strike"]
        t = _trade(index, s.date, "BASE_STRADDLE" if baseline else "S5_EXPIRY_STRADDLE", "ATM", exit_rule, ("CE", "PE"), {"CE": K, "PE": K}, start, end, atm, wings, window_of(s.date))
        if t:
            out.append(t)
    return out


def _mom(xs):
    n = len(xs)
    if n < 2:
        return n, (xs[0] if xs else None), None
    m = sum(xs) / n; sd = math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1)); return n, m, sd


def cell_summary(trades, baseline, zcrit, min_n=100):
    rets = [t.ret_on_premium for t in trades]; nets = [t.net_unit for t in trades]; brets = [t.ret_on_premium for t in baseline]
    n, m, sd = _mom(rets); nb, mb, sb = _mom(brets); _, mn, sn = _mom(nets)
    z = ((m - mb) / math.sqrt(sd * sd / n + sb * sb / nb)) if n > 1 and nb > 1 and sd and sb else None
    t_net = (mn / (sn / math.sqrt(n))) if n > 1 and sn else None
    taken = [t for t in trades if t.lots]
    eq = CAPITAL; peak = eq; dd = 0.0
    for t in taken:
        eq += t.pnl_inr; peak = max(peak, eq); dd = min(dd, eq / peak - 1)
    verdict = ("NOT_ENOUGH_DATA" if n < min_n else "POSITIVE_AFTER_COST_DEFLATED" if mn is not None and mn > 0 and t_net is not None and t_net >= zcrit and z is not None and z >= zcrit
               else "BEATS_BASELINE_NOT_COST" if z is not None and z >= zcrit else "NEGATIVE_AFTER_COST" if mn is None or mn <= 0 else "POSITIVE_UNDEFLATED")
    return {"n": n, "mean_premium_in": round(sum(t.premium_in for t in trades) / n, 2) if n else None, "mean_pnl_unit": round(sum(t.pnl_unit for t in trades) / n, 3) if n else None,
            "mean_cost_unit": round(sum(t.cost_unit for t in trades) / n, 3) if n else None, "mean_net_unit": round(mn, 3) if mn is not None else None, "t_net": round(t_net, 2) if t_net is not None else None,
            "mean_return_on_premium": round(m, 4) if m is not None else None, "baseline_return_on_premium": round(mb, 4) if mb is not None else None, "baseline_n": nb, "z_vs_baseline": round(z, 2) if z is not None else None,
            "win_rate": round(sum(1 for t in trades if t.net_unit > 0) / n, 4) if n else None, "exits": dict(defaultdict(int, {r: sum(1 for t in trades if t.exit_reason == r) for r in {t.exit_reason for t in trades}})),
            "marks": dict(defaultdict(int, {r: sum(1 for t in trades if t.mark == r) for r in {t.mark for t in trades}})),
            "inr": {"trades_taken": len(taken), "net_inr": round(sum(t.pnl_inr for t in taken), 2), "final_equity": round(eq, 0), "max_drawdown_pct": round(100 * dd, 2),
                    "risk_per_trade_pct_of_capital_at_stop": round(100 * sum(STOP_FRACTION * t.premium_in * t.lot for t in taken) / len(taken) / CAPITAL, 2) if taken else None,
                    "premium_per_trade_pct_of_capital": round(100 * sum(t.premium_in * t.lot for t in taken) / len(taken) / CAPITAL, 2) if taken else None}, "verdict": verdict}


def option_buying_study(index, stream5, sessions, atm, wings, window_of, alpha=0.05):
    cells = {}; fam = 2 * len(STRIKES) * len(STREAK_EXITS) + 2 * len(STRIKES) * len(GAP_EXITS) + len(STRADDLE_EXITS); zcrit = bonferroni_z(fam, alpha)
    for sr in STRIKES:
        for ex in STREAK_EXITS:
            tr = streak_trades(index, stream5, atm, wings, window_of, sr, ex); base = streak_trades(index, stream5, atm, wings, window_of, sr, ex, baseline=True)
            for sig in ("S1_RUN_DOWN_5+", "S2_RUN_UP_5+"):
                leg = "CE" if sig.startswith("S1") else "PE"
                cells[f"{sig}|{sr}|{ex}"] = {w: cell_summary([t for t in tr if t.signal == sig and t.window == w], [t for t in base if t.legs == leg and t.window == w], zcrit) for w in ("DEVELOPMENT", "POST")}
                cells[f"{sig}|{sr}|{ex}"]["trades"] = [t.as_dict() for t in tr if t.signal == sig]
        for ex in GAP_EXITS:
            tr = gap_trades(index, sessions, atm, wings, window_of, sr, ex); base = gap_trades(index, sessions, atm, wings, window_of, sr, ex, baseline=True)
            for sig in ("S3_GAP_SMALL_UP", "S4_GAP_SMALL_DOWN"):
                leg = "PE" if sig.startswith("S3") else "CE"
                cells[f"{sig}|{sr}|{ex}"] = {w: cell_summary([t for t in tr if t.signal == sig and t.window == w], [t for t in base if t.legs == leg and t.window == w], zcrit, min_n=60) for w in ("DEVELOPMENT", "POST")}
                cells[f"{sig}|{sr}|{ex}"]["trades"] = [t.as_dict() for t in tr if t.signal == sig]
    for ex in STRADDLE_EXITS:
        tr = straddle_trades(index, sessions, atm, wings, window_of, ex); base = straddle_trades(index, sessions, atm, wings, window_of, ex, baseline=True)
        cells[f"S5_EXPIRY_STRADDLE|ATM|{ex}"] = {w: cell_summary([t for t in tr if t.window == w], [t for t in base if t.window == w], zcrit, min_n=60) for w in ("DEVELOPMENT", "POST")}
        cells[f"S5_EXPIRY_STRADDLE|ATM|{ex}"]["trades"] = [t.as_dict() for t in tr]
    counts = defaultdict(int)
    for c in cells.values():
        counts[c["DEVELOPMENT"]["verdict"]] += 1
    cand = [k for k, c in cells.items() if c["DEVELOPMENT"]["verdict"] == "POSITIVE_AFTER_COST_DEFLATED" and (c["POST"]["mean_net_unit"] or -1) > 0]
    return {"index": index, "family": fam, "bonferroni_z": round(zcrit, 3), "cells": cells, "count_development": dict(counts), "candidate_cells": cand,
            "outcome": "CANDIDATE" if cand else "NO_OPTION_BUYING_STRATEGY"}
