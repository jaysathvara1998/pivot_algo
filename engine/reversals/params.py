from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.pullbacks import PullbackParams


@dataclass(frozen=True)
class ReversalParams:
    confirm_bars: int = 30          # bars after a violation within which the opposite TRENDING state must appear -> CONFIRMED
    weak_horizon: int = 15          # weakening study: violation within this many bars
    pullbacks: PullbackParams = PullbackParams()

    def as_dict(self) -> dict:
        d = asdict(self); d["pullbacks"] = self.pullbacks.as_dict(); return d

    def __post_init__(self):
        if self.confirm_bars < 1 or self.weak_horizon < 1:
            raise ValueError("invalid ReversalParams")


DEFAULT_REV_PARAMS = ReversalParams()
