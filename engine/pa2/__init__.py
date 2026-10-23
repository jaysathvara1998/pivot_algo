"""Skill 33 — the practitioner's price-action reading as computable detectors, with a study harness."""
from .detect import Bars, Signal, detect_all, SETUPS, LOOK
from .study import outcomes, summarise, merge_outcomes, block_shuffle_bars, HORIZONS_INDEX, HORIZONS_STOCK, COST_REF_5M

__all__ = ["Bars", "Signal", "detect_all", "SETUPS", "LOOK", "outcomes", "summarise", "merge_outcomes", "block_shuffle_bars", "HORIZONS_INDEX", "HORIZONS_STOCK", "COST_REF_5M"]
