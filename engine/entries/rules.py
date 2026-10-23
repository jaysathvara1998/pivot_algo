"""Pre-registered entry-rule grid (PROJECT DEFINITION, 2026-10-04)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from itertools import product


@dataclass(frozen=True)
class EntryRule:
    trigger: str        # NEXT_OPEN | CONFIRM_CLOSE | BREAK_EXTREME
    stop: str           # REF_1 | REF_2 | SIGNAL_EXTREME
    exit: str           # TIME_1 | TIME_3 | TARGET_1R | TARGET_2R
    break_within: int = 2        # BREAK_EXTREME: bars allowed for the break after the signal bar
    confirm_within: int = 1      # CONFIRM_CLOSE: the next bar must close in the setup direction
    max_hold: int = 20           # bars after entry before a forced time exit (R-target rules)

    @property
    def name(self) -> str:
        return f"{self.trigger}|{self.stop}|{self.exit}"

    def as_dict(self) -> dict:
        d = asdict(self); d["name"] = self.name; return d


GRID: tuple = tuple(EntryRule(t, s, e) for t, s, e in product(("NEXT_OPEN", "CONFIRM_CLOSE", "BREAK_EXTREME"),
                                                             ("REF_1", "REF_2", "SIGNAL_EXTREME"),
                                                             ("TIME_1", "TIME_3", "TARGET_1R", "TARGET_2R")))


def grid_as_dict() -> list:
    return [r.as_dict() for r in GRID]
