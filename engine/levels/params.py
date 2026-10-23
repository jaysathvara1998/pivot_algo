from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, time

from engine.swings import SwingParams


@dataclass(frozen=True)
class LevelParams:
    opening_range_minutes: int = 15        # ORH/ORL from the first k minutes; known at the close of minute k
    at_level_ref: float = 0.5              # |close - level| <= this many reference ranges -> AT_<family>
    round_grid: float = 100.0              # round-number grid in index points (NIFTY 100; SENSEX studies use 500)
    n_swing_levels: int = 3                # most recent confirmed swing highs/lows kept as levels
    close_definition: str = "LAST_CONTINUOUS_PRINT"   # PDC provenance; CAS value (from 2026-08-03) is absent from the feed
    static_window_from: date = date(2026, 8, 3)
    static_window_start: time = time(15, 15)
    swing: SwingParams = SwingParams()

    def as_dict(self) -> dict:
        d = asdict(self); d["static_window_from"] = self.static_window_from.isoformat(); d["static_window_start"] = self.static_window_start.isoformat(); return d

    def __post_init__(self):
        if self.opening_range_minutes < 1 or self.at_level_ref <= 0 or self.round_grid <= 0 or self.n_swing_levels < 1:
            raise ValueError("invalid LevelParams")


DEFAULT_LEVEL_PARAMS = LevelParams()
