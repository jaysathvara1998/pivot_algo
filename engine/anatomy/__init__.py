"""Level 2 — Candle anatomy (Skill 02).

Adds to Level 1 geometry exactly two things:
  1. shape classes with explicit, logged parameters (body class, close zone, wick profile,
     doji / marubozu flags);
  2. relative size: a candle's range (and body) compared with a reference built only from
     already-completed candles of the same clock minute in the previous N sessions, so the
     comparison is deseasonalised, volatility-scaled and free of look-ahead.

Nothing here attaches meaning (pressure, rejection, acceptance): that is Level 3.
"""
from .params import AnatomyParams, DEFAULT_PARAMS
from .shape import (
    BodyClass, CloseZone, WickProfile, body_class, close_zone, wick_profile,
    is_doji, is_marubozu, upper_wick_to_range, lower_wick_to_range, wick_asymmetry, shape,
)
from .reference import ClockReference, SizeClass, size_class
from .engine import AnatomyEngine, AnatomyRecord, replay_anatomy, FORBIDDEN_WORDS

__all__ = [
    "AnatomyParams", "DEFAULT_PARAMS",
    "BodyClass", "CloseZone", "WickProfile", "body_class", "close_zone", "wick_profile",
    "is_doji", "is_marubozu", "upper_wick_to_range", "lower_wick_to_range", "wick_asymmetry", "shape",
    "ClockReference", "SizeClass", "size_class",
    "AnatomyEngine", "AnatomyRecord", "replay_anatomy", "FORBIDDEN_WORDS",
]
