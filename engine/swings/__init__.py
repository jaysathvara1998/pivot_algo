"""Level 5 — Swings (Skill 05).

Swing highs and lows as CONFIRMED events (card 18): an n-bar fractal pivot exists only from bar
i+n; a zigzag pivot exists only when price has moved T reference ranges away from it. Each swing
carries its confirmation lag, its size in deseasonalised units and its relation to the previous
swing of the same kind (HH/LH, HL/LL). Session-bounded by default. No signals.
"""
from .params import SwingParams, DEFAULT_SWING_PARAMS
from .detect import Swing, SwingRecord, SwingEngine, replay_swings
from .study import swing_labels_by_key, swing_stats, swing_study

__all__ = ["SwingParams", "DEFAULT_SWING_PARAMS", "Swing", "SwingRecord", "SwingEngine", "replay_swings",
           "swing_labels_by_key", "swing_stats", "swing_study"]
