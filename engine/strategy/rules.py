"""Pre-registered GAP_FADE family (PROJECT DEFINITION, 2026-10-04; see skills/20_strategy_formation/STRATEGY_REGISTRY.md)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product

STOP_REF_Q90 = 3.5
STOP_FLOOR_REF = 0.5
MIN_60_BAR = 59
COST_REF_1M = {"development_era": 1.0, "stressed": 2.0}      # reference ranges per round trip (card 26: ≈ 7 points before 2026-04, ≈ 15 after)


@dataclass(frozen=True)
class GapFadeRule:
    sign: str        # UP | DOWN (gap sign; the trade fades it)
    entry: str       # BAR_1 | BAR_5
    stop: str        # GAP_1 | REF_Q90
    exit: str        # SESSION | MIN_60

    @property
    def entry_bar(self) -> int:
        return 1 if self.entry == "BAR_1" else 5

    @property
    def direction(self) -> str:
        return "DOWN" if self.sign == "UP" else "UP"

    @property
    def name(self) -> str:
        return f"GAP_FADE|{self.sign}|{self.entry}|{self.stop}|{self.exit}"

    def as_dict(self) -> dict:
        d = asdict(self); d["name"] = self.name; return d


GRID: tuple = tuple(GapFadeRule(s, e, st, x) for s, e, st, x in product(("UP", "DOWN"), ("BAR_1", "BAR_5"), ("GAP_1", "REF_Q90"), ("SESSION", "MIN_60")))


def grid_as_dict() -> list:
    return [r.as_dict() for r in GRID]
