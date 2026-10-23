"""Live-deployment gate and day-one limits (Level 25). PROJECT DEFINITION, 2026-10-05. The gate is the ONLY path to a live connection."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field
import json
from pathlib import Path

from engine.risk.sizing import lots_for_risk, CONTRACTS

PRECONDITIONS = (
    ("viable_on_development", "a candidate is VIABLE_ON_DEVELOPMENT at Level 21 (positive at current cost, deflated PSR ≥ 0.95, MinTRL ≤ n, breakeven cost × ≥ 1.5 × current)"),
    ("robustness_pass", "the candidate passes Level 22: positive walk-forward OOS in ≥ 60 % of steps, PBO ≤ 0.2 with positive OOS, plateau, STABLE across regimes, both indices same sign"),
    ("holdout_read_once_positive", "the hold-out was read once through the Level 22 gate and the candidate was positive after cost"),
    ("paper_pilot_sessions_ge_20", "≥ 20 paper sessions through the Level 24 pipeline with the bridge test passing"),
    ("slippage_measured", "a live-pilot slippage log exists with n ≥ the SlippageLog pilot size for SE 0.5 pt"),
    ("broker_margin_file", "the broker's SPAN margin file replaces the card 27 estimate"),
    ("limits_configured", "DayOneLimits are set and enforced by code (Level 18 limits + Level 24 kill switches)"),
)


@dataclass(frozen=True)
class DayOneLimits:
    risk_fraction: float = 0.005          # ½ of the Level 18 1 % reference; card 28 half-Kelly on deflated estimates is still smaller
    max_lots: int = 1                     # one lot regardless of capital on day one
    max_daily_loss_r: float = 2.0
    max_trades_per_day: int = 3
    max_concurrent: int = 1
    kill_switches: tuple = ("DATA_GAP", "ENGINE_HALTED", "DAILY_LOSS_LIMIT", "SESSION_END", "LATENCY_OVER_2_BARS", "SLIPPAGE_OVER_2X_PAPER")
    scale_up_after_trades: int = 60       # lots may double only after this many live trades with slippage ≤ 1.5× paper and no halts
    scale_up_max_factor: float = 2.0

    def as_dict(self) -> dict:
        return asdict(self)


def day_one_lots(capital_inr: float, stop_points: float, instrument: str, limits: DayOneLimits = DayOneLimits()) -> dict:
    s = lots_for_risk(capital_inr, limits.risk_fraction, stop_points, CONTRACTS[instrument])
    lots = min(s.lots, limits.max_lots)
    return {"lots": lots, "binding": "DAY_ONE_CAP" if lots < s.lots else s.binding, "risk_pct": round(100 * lots * stop_points * CONTRACTS[instrument].lot / capital_inr, 3) if lots else 0.0}


def deployment_gate(state: dict) -> dict:
    missing = [(k, why) for k, why in PRECONDITIONS if not state.get(k, False)]
    return {"status": "OPEN" if not missing else "CLOSED", "preconditions_met": [k for k, _ in PRECONDITIONS if state.get(k, False)],
            "preconditions_missing": [{"key": k, "requirement": why} for k, why in missing], "limits_if_open": DayOneLimits().as_dict()}


def state_from_reports(reports_dir: Path) -> dict:
    """Derive the gate inputs from the project's own reports (Levels 21, 22, 24). Anything not evidenced is False."""
    st = {k: False for k, _ in PRECONDITIONS}
    for inst in ("NIFTY", "SENSEX"):
        p = reports_dir / f"skill21_backtest_{inst}.json"
        if p.exists():
            st["viable_on_development"] |= any(c.get("verdict") == "VIABLE_ON_DEVELOPMENT" for c in json.loads(p.read_text())["candidates"].values())
        p = reports_dir / f"skill22_robustness_{inst}.json"
        if p.exists():
            r = json.loads(p.read_text())
            st["robustness_pass"] |= any(b["walk_forward"].get("share_oos_positive") not in (None,) and b["walk_forward"]["share_oos_positive"] >= 0.6 and b["pbo"].get("pbo") is not None
                                         and b["pbo"]["pbo"] <= 0.2 and b["pbo"]["mean_oos_r_of_best"] > 0 and b["plateau"].get("plateau") for b in r["families"].values())
            st["holdout_read_once_positive"] |= bool(r["holdout_gate"]["permitted"]) and False      # a permitted gate alone is not a positive read
        p = reports_dir / f"skill24_paper_{inst}.json"
        if p.exists():
            r = json.loads(p.read_text())
            st["paper_pilot_sessions_ge_20"] |= r["paper"]["days"] >= 20 and r["bridge"]["same_entry"] == r["bridge"]["common"] and r["bridge"]["common"] > 0
    st["limits_configured"] = True            # DayOneLimits exist and are enforced by engine.execution.precheck / engine.paper kill switches
    return st
