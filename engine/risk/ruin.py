"""Drawdown and ruin arithmetic on an R distribution (card 28)."""
from __future__ import annotations

import math
import random


def kelly_fraction(p: float, b: float) -> float:
    """f* = (b p − q) / b for win probability p and payoff ratio b; 0 when negative."""
    return max(0.0, (b * p - (1 - p)) / b) if b > 0 else 0.0


def growth_fraction_of_max(c: float) -> float:
    """Growth at c × Kelly relative to the maximum: c (1 − c/2) / 0.5 (continuous approximation)."""
    return c * (1 - c / 2) / 0.5


def bootstrap_drawdown(r_list, n_trades: int, risk_fraction: float, paths: int = 2000, seed: int = 20261018, ruin_dd: float = 0.5, limit_dd: float = 0.2):
    """Resample n_trades R outcomes with replacement, compound at risk_fraction of equity per trade (fixed fractional), and record the
    maximum drawdown of each path. Returns quantiles of max drawdown, P(max drawdown ≥ limit_dd), P(ruin: ≥ ruin_dd), median terminal equity."""
    if not r_list or n_trades <= 0:
        return {}
    rnd = random.Random(seed)
    dds = []; hit_limit = 0; ruined = 0; terminals = []
    for _ in range(paths):
        eq = 1.0; peak = 1.0; dd = 0.0; dead = False
        for _ in range(n_trades):
            r = rnd.choice(r_list)
            eq *= (1 + risk_fraction * r)
            if eq <= 0:
                eq = 0.0; dead = True
            peak = max(peak, eq); dd = max(dd, 1 - eq / peak if peak else 1.0)
            if dead:
                break
        dds.append(dd); hit_limit += dd >= limit_dd; ruined += dd >= ruin_dd; terminals.append(eq)
    dds.sort(); terminals.sort()
    q = lambda xs, p: xs[min(len(xs) - 1, int(p * len(xs)))]
    mean_r = sum(r_list) / len(r_list); sd_r = math.sqrt(sum((x - mean_r) ** 2 for x in r_list) / (len(r_list) - 1)) if len(r_list) > 1 else 0.0
    return {"n_trades": n_trades, "risk_fraction": risk_fraction, "paths": paths, "mean_r": round(mean_r, 4), "sd_r": round(sd_r, 4),
            "max_drawdown_q50": round(q(dds, 0.5), 4), "max_drawdown_q90": round(q(dds, 0.9), 4), "max_drawdown_q99": round(q(dds, 0.99), 4),
            "p_drawdown_ge_limit": round(hit_limit / paths, 4), "limit_dd": limit_dd, "p_ruin": round(ruined / paths, 4), "ruin_dd": ruin_dd,
            "terminal_equity_q10_q50_q90": [round(q(terminals, 0.1), 4), round(q(terminals, 0.5), 4), round(q(terminals, 0.9), 4)]}
