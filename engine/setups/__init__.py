"""Level 16 — Setup recognition (Skill 16).

A SETUP is a pre-registered conjunction of Level 2–15 facts observed at a bar, with a hypothesis, a horizon and an expected
outcome. The registry logs every candidate (trial count). Evaluation is restricted to the DEVELOPMENT window of the fixed
chronological split; the validation and hold-out windows are not read here. Results are deflated for the number of
candidates and gated against transaction cost in reference ranges. A passing setup is a candidate for Level 20, not a signal.
"""
from .split import SPLIT, window_of, assert_window
from .registry import Setup, REGISTRY, registry_as_dict
from .evaluate import evaluate_setups, bonferroni_z, COST_REF
from .stream import build_stream

__all__ = ["SPLIT", "window_of", "assert_window", "Setup", "REGISTRY", "registry_as_dict", "evaluate_setups", "bonferroni_z", "COST_REF", "build_stream"]
