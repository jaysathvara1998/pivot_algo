"""Backtest metric set (cards 28–29) over a list of BtTrade, with trial-count deflation and cost sensitivity (PROJECT DEFINITION, Skill 21)."""
from __future__ import annotations

import math
from collections import defaultdict

from engine.risk.ruin import bootstrap_drawdown

COST_MULTIPLIERS = (1.0, 1.5, 2.0, 3.0)     # 1× = recorded development-era cost; 2× ≈ the 2026-04 regime (card 26); 3× = stress
EULER_GAMMA = 0.5772156649


def _norm_cdf(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def _norm_ppf(p: float) -> float:
    lo, hi = -10.0, 10.0
    for _ in range(100):
        mid = (lo + hi) / 2
        if _norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2


def moments(xs):
    n = len(xs)
    if n < 2:
        return {"n": n, "mean": xs[0] if xs else None, "sd": None, "skew": None, "kurt": None}
    m = sum(xs) / n; v = sum((x - m) ** 2 for x in xs) / (n - 1); sd = math.sqrt(v)
    if sd == 0:
        return {"n": n, "mean": m, "sd": 0.0, "skew": 0.0, "kurt": 3.0}
    sk = sum(((x - m) / sd) ** 3 for x in xs) / n; ku = sum(((x - m) / sd) ** 4 for x in xs) / n
    return {"n": n, "mean": m, "sd": sd, "skew": sk, "kurt": ku}


def sharpe_per_trade(xs) -> float | None:
    mo = moments(xs)
    return (mo["mean"] / mo["sd"]) if mo.get("sd") else None


def probabilistic_sharpe(sr: float, n: int, skew: float, kurt: float, sr_bench: float = 0.0) -> float:
    """PSR: P(true SR > sr_bench) given the estimate, its sample size and the non-normality of the returns (Bailey & López de Prado)."""
    denom = math.sqrt(max(1e-12, 1 - skew * sr + (kurt - 1) / 4 * sr * sr))
    return _norm_cdf((sr - sr_bench) * math.sqrt(n - 1) / denom)


def expected_max_sharpe(n_trials: int, var_sr: float) -> float:
    """E[max SR] of n_trials zero-edge trials whose SR estimates have variance var_sr."""
    if n_trials <= 1 or var_sr <= 0:
        return 0.0
    return math.sqrt(var_sr) * ((1 - EULER_GAMMA) * _norm_ppf(1 - 1 / n_trials) + EULER_GAMMA * _norm_ppf(1 - 1 / (n_trials * math.e)))


def min_track_record(sr: float, skew: float, kurt: float, sr_bench: float = 0.0, alpha: float = 0.05):
    """Trades needed for PSR ≥ 1 − alpha; infinite when sr ≤ sr_bench."""
    if sr <= sr_bench:
        return math.inf
    z = _norm_ppf(1 - alpha)
    return 1 + (1 - skew * sr + (kurt - 1) / 4 * sr * sr) * (z / (sr - sr_bench)) ** 2


def quantiles(xs, qs=(0.05, 0.25, 0.5, 0.75, 0.95)):
    if not xs:
        return {}
    s = sorted(xs); n = len(s)
    return {f"q{int(q * 100):02d}": round(s[min(n - 1, int(q * (n - 1) + 0.5))], 4) for q in qs}


def _split(trades, key, cost_mult):
    groups = defaultdict(list)
    for t in trades:
        groups[getattr(t, key)].append(t.r_net_at(cost_mult))
    out = {}
    for k, xs in sorted(groups.items()):
        mo = moments(xs)
        out[k] = {"n": mo["n"], "mean_r_net": round(mo["mean"], 4) if mo["mean"] is not None else None,
                  "t": round(mo["mean"] / (mo["sd"] / math.sqrt(mo["n"])), 2) if mo.get("sd") else None}
    return out


def backtest_report(trades, n_trials: int, family_sharpes=None, cost_multipliers=COST_MULTIPLIERS, current_cost_multiplier: float = 2.0,
                    sessions_in_window: int | None = None, risk_fraction: float = 0.01, seed: int = 20261021) -> dict:
    """Full metric set for one candidate. `n_trials` is the family it was selected from; `family_sharpes` are the per-trade Sharpe ratios of the
    family's cells (for V[SR] in the deflated Sharpe); when absent, V[SR] = 1/n (the sampling variance of a zero-edge SR estimate)."""
    n = len(trades)
    rep = {"trades": n, "n_trials": n_trials, "current_cost_multiplier": current_cost_multiplier}
    if n < 2:
        rep["verdict"] = "NOT_ENOUGH_DATA"; return rep
    gross = [t.r_gross for t in trades]; cur = [t.r_net_at(current_cost_multiplier) for t in trades]
    mg = moments(gross); mc = moments(cur)
    rep["gross"] = {k: round(v, 4) for k, v in mg.items() if v is not None}
    rep["net_current_cost"] = {k: round(v, 4) for k, v in mc.items() if v is not None}
    rep["net_current_cost"]["t"] = round(mc["mean"] / (mc["sd"] / math.sqrt(n)), 2) if mc["sd"] else None
    rep["win_rate_current_cost"] = round(sum(1 for x in cur if x > 0) / n, 4)
    wins = [x for x in cur if x > 0]; losses = [x for x in cur if x <= 0]
    rep["profit_factor_current_cost"] = round(sum(wins) / -sum(losses), 3) if losses and sum(losses) < 0 else None
    rep["payoff_quantiles_current_cost"] = quantiles(cur)
    # Sharpe family: per-trade SR, PSR vs 0, deflated SR vs E[max SR] of the family, MinTRL
    sr = mc["mean"] / mc["sd"] if mc["sd"] else None
    if sr is not None:
        var_sr = (moments(family_sharpes)["sd"] ** 2 if family_sharpes and len(family_sharpes) > 1 and moments(family_sharpes)["sd"] else 1.0 / n)
        sr_star = expected_max_sharpe(n_trials, var_sr)
        rep["sharpe"] = {"per_trade": round(sr, 4), "psr_vs_zero": round(probabilistic_sharpe(sr, n, mc["skew"], mc["kurt"]), 4),
                         "expected_max_sr_of_family": round(sr_star, 4), "var_sr_used": round(var_sr, 6),
                         "deflated_psr": round(probabilistic_sharpe(sr, n, mc["skew"], mc["kurt"], sr_star), 4),
                         "min_track_record_trades": (round(min_track_record(sr, mc["skew"], mc["kurt"], sr_star)) if min_track_record(sr, mc["skew"], mc["kurt"], sr_star) != math.inf else "inf")}
    # cost sensitivity and breakeven multiplier
    mean_cost = sum(t.cost_r for t in trades) / n
    rep["cost_sensitivity"] = {f"{m:g}x": round(sum(t.r_net_at(m) for t in trades) / n, 4) for m in cost_multipliers}
    rep["breakeven_cost_multiplier"] = round(mg["mean"] / mean_cost, 3) if mean_cost > 0 else None
    rep["mean_cost_r_1x"] = round(mean_cost, 4)
    # MAE / MFE
    w = [t for t in trades if t.r_net_at(current_cost_multiplier) > 0]; l = [t for t in trades if t.r_net_at(current_cost_multiplier) <= 0]
    med = lambda xs: round(sorted(xs)[len(xs) // 2], 4) if xs else None
    rep["excursions"] = {"median_mae_winners": med([t.mae_r for t in w]), "median_mae_losers": med([t.mae_r for t in l]), "mfe_quantiles": quantiles([t.mfe_r for t in trades]),
                         "losers_that_reached_half_r": round(sum(1 for t in l if t.mfe_r >= 0.5) / len(l), 4) if l else None,
                         "median_bars_held": med([float(t.bars_held) for t in trades]), "exit_reasons": dict(sorted(defaultdict(int, {r: sum(1 for t in trades if t.exit_reason == r) for r in {t.exit_reason for t in trades}}).items()))}
    # equity in R and drawdown
    eq = 0.0; peak = 0.0; dd = 0.0; dd_len = 0; cur_len = 0
    for x in cur:
        eq += x
        if eq > peak:
            peak = eq; cur_len = 0
        else:
            cur_len += 1
        dd = min(dd, eq - peak); dd_len = max(dd_len, cur_len)
    rep["equity_r"] = {"final": round(eq, 2), "max_drawdown_r": round(dd, 2), "longest_drawdown_trades": dd_len}
    rep["bootstrap_drawdown_1pct"] = bootstrap_drawdown(cur, n, risk_fraction, paths=1000, seed=seed)
    # frequency and dependence
    months = sorted({t.month for t in trades})
    rep["frequency"] = {"months_active": len(months), "trades_per_month": round(n / len(months), 2) if months else None,
                        "trades_per_session": round(n / sessions_in_window, 4) if sessions_in_window else None,
                        "positive_months_share": round(sum(1 for m, v in _split(trades, "month", current_cost_multiplier).items() if v["mean_r_net"] > 0) / len(months), 4) if months else None}
    rep["by_regime"] = _split(trades, "regime", current_cost_multiplier)
    rep["by_phase"] = _split(trades, "phase", current_cost_multiplier)
    rep["by_direction"] = _split(trades, "direction", current_cost_multiplier)
    # verdict (card 29): positive at current cost, deflated PSR ≥ 0.95, track record long enough
    reasons = []
    if mc["mean"] <= 0:
        reasons.append("mean net R ≤ 0 at current cost")
    if "sharpe" in rep and rep["sharpe"]["deflated_psr"] < 0.95:
        reasons.append("deflated PSR < 0.95")
    if "sharpe" in rep and (rep["sharpe"]["min_track_record_trades"] == "inf" or rep["sharpe"]["min_track_record_trades"] > n):
        reasons.append("track record shorter than MinTRL")
    if rep["breakeven_cost_multiplier"] is not None and rep["breakeven_cost_multiplier"] < 1.5 * current_cost_multiplier:
        reasons.append("breakeven cost multiplier below 1.5× current cost")
    rep["verdict"] = "NOT_VIABLE" if reasons else "VIABLE_ON_DEVELOPMENT"
    rep["reasons"] = reasons
    return rep
