"""Level 17 — Entry logic (Skill 17).

Entry mechanics as definitions: trigger (next open / confirming close / break of the signal bar's extreme), stop (reference
ranges or the signal bar's extreme), exit (time or R-multiple target). A bar-by-bar simulator turns a setup's signal bars
into trades measured in R with MAE / MFE and a cost in R. The full pre-registered grid of rules is evaluated on the
development window; the family size is the grid size. No live rules.
"""
from .rules import EntryRule, GRID, grid_as_dict
from .simulate import Trade, simulate, metrics, cost_in_r, MIN_STOP_REF, STOP_SLIPPAGE_REF
from .evaluate import evaluate_grid

__all__ = ["EntryRule", "GRID", "grid_as_dict", "Trade", "simulate", "metrics", "cost_in_r", "MIN_STOP_REF", "STOP_SLIPPAGE_REF", "evaluate_grid"]
