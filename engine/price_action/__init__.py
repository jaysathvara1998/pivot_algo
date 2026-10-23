"""Level 7 — Price action (Skill 07).

Integrates Levels 2–6 into one explainable, per-bar description of what price is doing, and adds
PRICE LOCATION: where the close sits inside the current structural range (between the last confirmed
swing low and high), inside the session range so far, and relative to the session open; and the
CURRENT LEG since the most recent confirmed pivot (direction, length, size, with/against the trend
state, retracement of the previous leg). Descriptions only; no pattern names, no decisions.
"""
from .params import PriceActionParams, DEFAULT_PA_PARAMS
from .describe import PriceActionEngine, PriceActionRecord, describe, replay_price_action, FORBIDDEN_LEVEL7
from .study import pa_labels_by_key, pa_study, coverage_stats

__all__ = ["PriceActionParams", "DEFAULT_PA_PARAMS", "PriceActionEngine", "PriceActionRecord", "describe",
           "replay_price_action", "FORBIDDEN_LEVEL7", "pa_labels_by_key", "pa_study", "coverage_stats"]
