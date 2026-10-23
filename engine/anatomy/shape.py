"""Shape classes: pure functions of one candle plus the parameter set. No neighbours, no meaning."""
from __future__ import annotations

from enum import Enum
from typing import Optional

from engine.ohlc import Candle, body, body_to_range, close_location, lower_wick, range_, upper_wick, direction
from .params import AnatomyParams, DEFAULT_PARAMS


class BodyClass(str, Enum):
    UNDEFINED = "UNDEFINED"       # zero range
    NEGLIGIBLE = "NEGLIGIBLE"     # body/range <= doji_max
    SMALL = "SMALL"
    MEDIUM = "MEDIUM"
    LARGE = "LARGE"
    FULL = "FULL"                 # body/range >= body_full_min


class CloseZone(str, Enum):
    UNDEFINED = "UNDEFINED"
    LOW = "LOW"                   # close_location in [0, e0)
    LOWER = "LOWER"               # [e0, e1)
    MIDDLE = "MIDDLE"             # [e1, e2)
    UPPER = "UPPER"               # [e2, e3)
    HIGH = "HIGH"                 # [e3, 1]


class WickProfile(str, Enum):
    UNDEFINED = "UNDEFINED"
    NONE = "NONE"                 # both wicks <= marubozu_max
    UPPER_DOMINANT = "UPPER_DOMINANT"
    LOWER_DOMINANT = "LOWER_DOMINANT"
    BALANCED = "BALANCED"         # |upper - lower| <= balanced_max_diff * range
    UPPER_LEANING = "UPPER_LEANING"
    LOWER_LEANING = "LOWER_LEANING"


def upper_wick_to_range(c: Candle) -> Optional[float]:
    r = range_(c)
    return None if r == 0 else upper_wick(c) / r


def lower_wick_to_range(c: Candle) -> Optional[float]:
    r = range_(c)
    return None if r == 0 else lower_wick(c) / r


def wick_asymmetry(c: Candle) -> Optional[float]:
    """(upper_wick - lower_wick) / range in [-1, 1]; None when range is zero."""
    r = range_(c)
    return None if r == 0 else (upper_wick(c) - lower_wick(c)) / r


def body_class(c: Candle, p: AnatomyParams = DEFAULT_PARAMS) -> BodyClass:
    btr = body_to_range(c)
    if btr is None:
        return BodyClass.UNDEFINED
    if btr <= p.doji_max_body_to_range:
        return BodyClass.NEGLIGIBLE
    if btr <= p.body_small_max:
        return BodyClass.SMALL
    if btr <= p.body_medium_max:
        return BodyClass.MEDIUM
    if btr < p.body_full_min:
        return BodyClass.LARGE
    return BodyClass.FULL


def close_zone(c: Candle, p: AnatomyParams = DEFAULT_PARAMS) -> CloseZone:
    cl = close_location(c)
    if cl is None:
        return CloseZone.UNDEFINED
    e0, e1, e2, e3 = p.close_zone_edges
    if cl < e0:
        return CloseZone.LOW
    if cl < e1:
        return CloseZone.LOWER
    if cl < e2:
        return CloseZone.MIDDLE
    if cl < e3:
        return CloseZone.UPPER
    return CloseZone.HIGH


def wick_profile(c: Candle, p: AnatomyParams = DEFAULT_PARAMS) -> WickProfile:
    r = range_(c)
    if r == 0:
        return WickProfile.UNDEFINED
    u, l = upper_wick(c) / r, lower_wick(c) / r
    if u <= p.marubozu_max_wick_to_range and l <= p.marubozu_max_wick_to_range:
        return WickProfile.NONE
    if abs(u - l) <= p.wick_balanced_max_diff:
        return WickProfile.BALANCED          # evaluated before dominance: equal half-range wicks are balanced, not dominant
    if u >= p.wick_dominant_min:
        return WickProfile.UPPER_DOMINANT
    if l >= p.wick_dominant_min:
        return WickProfile.LOWER_DOMINANT
    return WickProfile.UPPER_LEANING if u > l else WickProfile.LOWER_LEANING


def is_doji(c: Candle, p: AnatomyParams = DEFAULT_PARAMS) -> Optional[bool]:
    """True iff body/range <= doji_max_body_to_range. None when range is zero (undefined)."""
    btr = body_to_range(c)
    return None if btr is None else btr <= p.doji_max_body_to_range


def is_marubozu(c: Candle, p: AnatomyParams = DEFAULT_PARAMS) -> Optional[bool]:
    """True iff both wicks <= marubozu_max_wick_to_range * range. None when range is zero."""
    r = range_(c)
    if r == 0:
        return None
    return upper_wick(c) <= p.marubozu_max_wick_to_range * r and lower_wick(c) <= p.marubozu_max_wick_to_range * r


def shape(c: Candle, p: AnatomyParams = DEFAULT_PARAMS) -> dict:
    """All Level 2 shape facts for one candle (deterministic mapping)."""
    return {
        "direction": direction(c).value,
        "range": range_(c),
        "body": body(c),
        "body_to_range": body_to_range(c),
        "upper_wick_to_range": upper_wick_to_range(c),
        "lower_wick_to_range": lower_wick_to_range(c),
        "wick_asymmetry": wick_asymmetry(c),
        "close_location": close_location(c),
        "body_class": body_class(c, p).value,
        "close_zone": close_zone(c, p).value,
        "wick_profile": wick_profile(c, p).value,
        "doji": is_doji(c, p),
        "marubozu": is_marubozu(c, p),
    }
