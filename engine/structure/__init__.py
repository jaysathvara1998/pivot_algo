"""Level 6 — Market structure (Skill 06).

Trend state from the last two confirmed swing highs and lows (card 18): UP (HH and HL), DOWN (LH and LL),
RANGE_EXPANDING (HH and LL), RANGE_CONTRACTING (LH and HL), UNDEFINED (fewer than two of either kind).
Break of structure (BOS) and change of character (CHOCH) are closes beyond the last confirmed swing of the
relevant kind, stamped at the bar of the close, once per swing. No signals.
"""
from .params import StructureParams, DEFAULT_STRUCTURE_PARAMS
from .state import StructureEngine, StructureRecord, StructEvent, replay_structure, trend_state, STATES
from .study import structure_labels_by_key, state_stats, structure_study, swing_relation_study

__all__ = ["StructureParams", "DEFAULT_STRUCTURE_PARAMS", "StructureEngine", "StructureRecord", "StructEvent",
           "replay_structure", "trend_state", "STATES", "structure_labels_by_key", "state_stats", "structure_study", "swing_relation_study"]
