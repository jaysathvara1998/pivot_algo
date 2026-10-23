from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.levels import LevelParams

GROUPS = {"PDH": "PREV_DAY", "PDL": "PREV_DAY", "PDC": "PREV_DAY", "ORH": "OPENING_RANGE", "ORL": "OPENING_RANGE",
          "SWING_HIGH": "SWING", "SWING_LOW": "SWING", "PIVOT": "PIVOT"}      # OPEN and ROUND are not studied (Level 8: coordinates only)


@dataclass(frozen=True)
class BreakoutParams:
    near_ref: float = 0.5          # APPROACH / TEST_RETURNED distance in reference ranges
    accept_k: int = 3              # consecutive closes beyond the level -> ACCEPTANCE (confirmed at the k-th)
    fail_h: int = 5                # a close back across within h bars of the break (before acceptance) -> FAILED_BREAK
    horizons: tuple = (5, 15)      # bars after a BREAK at which "still beyond" is evaluated (same session)
    levels: LevelParams = LevelParams()

    def as_dict(self) -> dict:
        d = asdict(self); d["levels"] = self.levels.as_dict(); return d

    def __post_init__(self):
        if self.accept_k < 1 or self.fail_h < 1 or self.near_ref <= 0:
            raise ValueError("invalid BreakoutParams")


DEFAULT_BREAKOUT_PARAMS = BreakoutParams()
