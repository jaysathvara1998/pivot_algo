"""Pre-registered trade-management grid (PROJECT DEFINITION, 2026-10-04, written before any development evaluation).

Entry is fixed at the development-best Level 17 trigger (NEXT_OPEN). Everything after entry is a management rule:
  stop      REF_2 (2.0 reference ranges, Level 17) | REF_Q90 (3.5 reference ranges: the Level 18 ALL 5m forward 3-bar range q90 of 3.54 rounded)
  breakeven NONE | BE_1R   (once a bar's favourable excursion reaches 1R the stop moves to the entry price, effective from the next bar)
  trail     NONE | TRAIL_2 (stop ratchets to the best favourable extreme minus 2 reference ranges) | STRUCT_3 (stop ratchets to the adverse extreme of the last 3 closed bars)
  exit      TIME_3 | TIME_10 | SESSION (hold to the session's last bar)
  partial   NONE | HALF_1R (half the position exits at +1R; the rest is managed by the other rules)
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product

STOP_REF = {"REF_2": 2.0, "REF_Q90": 3.5}
HOLD = {"TIME_3": 3, "TIME_10": 10, "SESSION": 10_000}


@dataclass(frozen=True)
class ManagementRule:
    stop: str
    breakeven: str
    trail: str
    exit: str
    partial: str
    trail_ref: float = 2.0
    struct_bars: int = 3

    @property
    def name(self) -> str:
        return f"{self.stop}|{self.breakeven}|{self.trail}|{self.exit}|{self.partial}"

    def as_dict(self) -> dict:
        d = asdict(self); d["name"] = self.name; return d


GRID: tuple = tuple(ManagementRule(s, b, t, e, p) for s, b, t, e, p in product(("REF_2", "REF_Q90"), ("NONE", "BE_1R"), ("NONE", "TRAIL_2", "STRUCT_3"),
                                                                              ("TIME_3", "TIME_10", "SESSION"), ("NONE", "HALF_1R")))
REFERENCE_RULE = ManagementRule("REF_2", "NONE", "NONE", "TIME_3", "NONE")      # the unmanaged neighbour of the Level 17 best cell


def grid_as_dict() -> list:
    return [r.as_dict() for r in GRID]
