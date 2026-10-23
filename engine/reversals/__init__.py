"""Level 12 — Reversal behaviour (Skill 12).

A reversal is a SEQUENCE of confirmed events, not a candle: WEAKENING of the trend (a deep or expired pullback, fading
momentum), a STRUCTURAL VIOLATION (change of character or failed continuation), a NEW SWING SEQUENCE (Level 6 state flips),
and CONFIRMATION (Level 10 trending state in the new direction) within a horizon; otherwise the reversal FAILS (the old
trending state returns) or EXPIRES. Each stage is stamped at its bar. No signals.
"""
from .params import ReversalParams, DEFAULT_REV_PARAMS
from .machine import ReversalEngine, ReversalRecord, ReversalEvent, replay_reversals
from .study import rev_labels_by_key, rev_resolution, weakening_study, rev_study

__all__ = ["ReversalParams", "DEFAULT_REV_PARAMS", "ReversalEngine", "ReversalRecord", "ReversalEvent", "replay_reversals",
           "rev_labels_by_key", "rev_resolution", "weakening_study", "rev_study"]
