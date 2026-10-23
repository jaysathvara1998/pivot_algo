"""Closed vocabulary of single-candle interpretations (PROJECT DEFINITION).

Labels describe what PRICE did inside the candle, never who did it. The folk terms that
traders use for the same shapes are recorded here for translation only; they are not emitted
by the engine because they smuggle in a cause (a "rejection" by "sellers") that OHLC on an
index cannot show (cards 12, 13).
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from enum import Enum


class Label(str, Enum):
    DIRECTIONAL_RETAINED_UP = "DIRECTIONAL_RETAINED_UP"       # large/full bullish body closing in the top fifth
    DIRECTIONAL_RETAINED_DOWN = "DIRECTIONAL_RETAINED_DOWN"
    UPPER_EXCURSION_RETURNED = "UPPER_EXCURSION_RETURNED"     # dominant upper wick, close in the bottom two fifths
    LOWER_EXCURSION_RETURNED = "LOWER_EXCURSION_RETURNED"
    TWO_SIDED_UNRESOLVED = "TWO_SIDED_UNRESOLVED"             # negligible/small body, no dominant wick
    RANGE_EXPANSION = "RANGE_EXPANSION"                       # LARGE/EXTREME vs clock AND vs today's earlier bars
    RANGE_COMPRESSION = "RANGE_COMPRESSION"                   # SMALL vs clock AND vs today's earlier bars
    UNCLASSIFIED = "UNCLASSIFIED"                             # the designed majority


# translation table: folk term -> project label (documentation and search only)
FOLK_TERMS: dict[str, str] = {
    "strong bullish candle / buying pressure": Label.DIRECTIONAL_RETAINED_UP.value,
    "strong bearish candle / selling pressure": Label.DIRECTIONAL_RETAINED_DOWN.value,
    "rejection of the high / shooting-star or inverted-hammer shape": Label.UPPER_EXCURSION_RETURNED.value,
    "rejection of the low / hammer or hanging-man shape": Label.LOWER_EXCURSION_RETURNED.value,
    "indecision / doji / spinning top": Label.TWO_SIDED_UNRESOLVED.value,
    "expansion / wide-range bar / volatility breakout": Label.RANGE_EXPANSION.value,
    "compression / narrow-range bar / NR": Label.RANGE_COMPRESSION.value,
    "acceptance (Market Profile)": "DEFERRED: time-at-level is a multi-bar concept (Level 8)",
}

# words the engine never emits at Level 3 (flow attribution, liquidity labels, decisions)
FORBIDDEN_LEVEL3 = (
    "buyers", "sellers", "buying pressure", "selling pressure", "institutional", "smart money",
    "aggressive", "absorbed", "absorption", "thin", "illiquid", "signal", " buy", " sell", "entry",
    "go long", "go short", "bullish engulfing", "rejection", "indecision",
)


@dataclass(frozen=True)
class BehaviourParams:
    # directional retention: body class in this set AND close zone at the directional extreme
    retained_body_classes: tuple = ("LARGE", "FULL")
    # excursion returned: this wick profile AND close in these zones (mirror for lower)
    excursion_wick_profile_upper: str = "UPPER_DOMINANT"
    excursion_close_zones_upper: tuple = ("LOW", "LOWER")
    # two-sided unresolved: body class in this set AND wick profile NOT dominant
    unresolved_body_classes: tuple = ("NEGLIGIBLE", "SMALL")
    # expansion / compression: Level 2 size class AND local (within-session) ratio
    expansion_size_classes: tuple = ("LARGE", "EXTREME")
    expansion_local_min: float = 2.0
    compression_size_classes: tuple = ("SMALL",)
    compression_local_max: float = 0.5

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_BEHAVIOUR_PARAMS = BehaviourParams()
