"""Level 9 — Breakouts, failures, rejections (Skill 09).

Events at Level 8 levels, each with parameters, a confirmation stamp and (where relevant) a horizon:
APPROACH (close comes within d of a level without touching it), TEST (bar spans the level, close stays on
the original side), TEST_RETURNED (test with the close at least d away from the level: the folk
"rejection"), BREAK (close crosses), ACCEPTANCE (k consecutive closes beyond), FAILED_BREAK (close back
across within h bars before acceptance), RECLAIM (break back through a level that had been accepted on
the other side this session). Studied against a random walk carrying the same levels. No signals.
"""
from .params import BreakoutParams, DEFAULT_BREAKOUT_PARAMS, GROUPS
from .machine import BreakoutEngine, BreakoutRecord, BreakEvent, replay_breakouts
from .study import breakout_labels_by_key, horizon_study, breakout_study

__all__ = ["BreakoutParams", "DEFAULT_BREAKOUT_PARAMS", "GROUPS", "BreakoutEngine", "BreakoutRecord", "BreakEvent",
           "replay_breakouts", "breakout_labels_by_key", "horizon_study", "breakout_study"]
