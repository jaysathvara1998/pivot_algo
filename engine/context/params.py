from __future__ import annotations

from dataclasses import dataclass, asdict

GAP_CLASSES = ("FLAT", "SMALL", "MEDIUM", "LARGE")


@dataclass(frozen=True)
class ContextParams:
    gap_flat_pct: float = 0.10        # |gap| < 0.10 % of the previous close -> FLAT
    gap_small_pct: float = 0.30       # < 0.30 % -> SMALL
    gap_medium_pct: float = 0.75      # < 0.75 % -> MEDIUM; else LARGE
    first_hour_minutes: int = 60
    null_paths_per_session: int = 5   # volatility-matched random-walk paths per real session in the study

    def as_dict(self) -> dict:
        return asdict(self)

    def __post_init__(self):
        if not (0 < self.gap_flat_pct < self.gap_small_pct < self.gap_medium_pct) or self.first_hour_minutes < 1:
            raise ValueError("invalid ContextParams")


DEFAULT_CTX_PARAMS = ContextParams()
