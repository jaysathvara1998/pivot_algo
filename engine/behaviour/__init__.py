"""Level 3 — Candlestick behaviour (Skill 03).

Single-candle INTERPRETATIONS built from a Level 2 AnatomyRecord. Each interpretation is a
project label from a closed vocabulary, an ordered list of the Level 2 observations that
justify it (P9), the parameter set, and an evidence status read from the measured register.
Flow attribution ("buyers", "selling pressure") is not permitted on an index (card 12).
"""
from .vocabulary import Label, FOLK_TERMS, BehaviourParams, DEFAULT_BEHAVIOUR_PARAMS, FORBIDDEN_LEVEL3
from .interpret import Interpretation, interpret, explain
from .study import (
    OUTCOMES, Outcome, binomial_z, two_sample_z, conditional_study, status_of, STATUS_RULE, pair_bars, random_walk_items,
    ExtensionTable, EmpiricalExtensionTable, extension_table, extension_null_p,
)

__all__ = [
    "Label", "FOLK_TERMS", "BehaviourParams", "DEFAULT_BEHAVIOUR_PARAMS", "FORBIDDEN_LEVEL3",
    "Interpretation", "interpret", "explain",
    "OUTCOMES", "Outcome", "binomial_z", "two_sample_z", "conditional_study", "status_of", "STATUS_RULE", "pair_bars", "random_walk_items", "ExtensionTable", "EmpiricalExtensionTable", "extension_table", "extension_null_p",
]
