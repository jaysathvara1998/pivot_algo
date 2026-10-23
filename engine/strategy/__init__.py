"""Level 20 — strategy formation: the pre-registered GAP_FADE family, session objects, gap-aware null, simulation and evaluation."""
from .rules import GapFadeRule, GRID, STOP_REF_Q90, STOP_FLOOR_REF, MIN_60_BAR, COST_REF_1M, grid_as_dict
from .sessions import Session, build_sessions, null_sessions, session_sigma
from .simulate import GapTrade, simulate_gap_fade, run_rule
from .evaluate import evaluate_gap_fade

__all__ = ["GapFadeRule", "GRID", "STOP_REF_Q90", "STOP_FLOOR_REF", "MIN_60_BAR", "COST_REF_1M", "grid_as_dict", "Session", "build_sessions",
           "null_sessions", "session_sigma", "GapTrade", "simulate_gap_fade", "run_rule", "evaluate_gap_fade"]
