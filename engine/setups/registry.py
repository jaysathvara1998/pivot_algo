"""Setup registry: every candidate ever written down, with its hypothesis and registration date (topic 29, card 25)."""
from __future__ import annotations

from dataclasses import dataclass, asdict, field


@dataclass(frozen=True)
class Setup:
    name: str
    interval_minutes: int
    hypothesis: str                       # written before evaluation
    requires: tuple                       # labels that must all be present at the signal bar
    excludes: tuple = ()                  # labels that must be absent
    expected: str = "UP"                  # UP | DOWN | LARGE (size setup) | FILL (context setup)
    horizon_bars: int = 1
    source_levels: tuple = ()
    registered_on: str = "2026-10-04"
    notes: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# Candidates are restricted to facts that survived the Level 14 regime audit (streak reversal; clustering; first bar/U-shape;
# gap-fill base rates). Everything here was registered on 2026-10-04 before the development window was evaluated.
REGISTRY: tuple = (
    Setup("streak_reversal_down_5m", 5, "After 5+ consecutive 5m down closes the next 5m bar closes up more often than noise (Level 4, STABLE at Level 14).",
          ("RUN_DOWN_5+",), (), "UP", 1, (4, 14)),
    Setup("streak_reversal_up_5m", 5, "After 5+ consecutive 5m up closes the next 5m bar closes down more often than noise (Level 4, STABLE at Level 14).",
          ("RUN_UP_5+",), (), "DOWN", 1, (4, 14)),
    Setup("streak_reversal_down_5m_h3", 5, "The down-streak reversal persists over 3 bars (close 3 bars later above the signal close).",
          ("RUN_DOWN_5+",), (), "UP", 3, (4, 14), notes="horizon variant of candidate 1"),
    Setup("streak_reversal_down_5m_normal_vol", 5, "The down-streak reversal is at least as strong when the volatility regime is NORMAL (no spike).",
          ("RUN_DOWN_5+", "VOL_NORMAL"), (), "UP", 1, (4, 10, 14), notes="conjunction with Level 10 volatility regime"),
    Setup("streak_reversal_down_5m_not_trending_down", 5, "The down-streak reversal is stronger outside a TRENDING_DOWN state (ranging markets snap back).",
          ("RUN_DOWN_5+",), ("STATE_TRENDING_DOWN",), "UP", 1, (4, 10, 14), notes="conjunction with Level 10 state"),
    Setup("expansion_then_large_1m", 1, "After a RANGE_EXPANSION bar the next 1m bar is LARGE far more often than the base rate (Levels 3, 14).",
          ("RANGE_EXPANSION",), (), "LARGE", 1, (3, 14)),
    Setup("violation_then_large_1m", 1, "After a structural violation of an UP trend the next 1m bar is LARGE more often than the base rate (Level 12).",
          ("VIOLATION|UP",), (), "LARGE", 1, (12,)),
    Setup("gap_small_up_fills", 1, "A SMALL UP gap is revisited during the session more often than the gap-aware walk predicts (Level 15).",
          ("GAP_SMALL_UP", "FIRST_BAR"), (), "FILL", 375, (15,), notes="evaluated per session at the first bar"),
)


def registry_as_dict() -> list:
    return [s.as_dict() for s in REGISTRY]
