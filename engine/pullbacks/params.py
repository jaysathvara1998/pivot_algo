from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.market_state import MarketStateParams

DEPTH_CLASSES = ("SHALLOW", "NORMAL", "DEEP", "BROKEN")


@dataclass(frozen=True)
class PullbackParams:
    start_min_ref: float = 1.0        # close at least this many reference ranges off the impulse extreme -> pullback starts
    shallow_max: float = 0.382        # depth classes on the share of the impulse leg retraced
    normal_max: float = 0.618
    deep_max: float = 1.0             # beyond 1.0 the last confirmed swing is violated -> FAILED_CONTINUATION
    expire_bars: int = 20             # bars from the pullback start without resolution -> EXPIRED
    market_state: MarketStateParams = MarketStateParams()

    def as_dict(self) -> dict:
        d = asdict(self); d["market_state"] = self.market_state.as_dict(); return d

    def __post_init__(self):
        if not (0 < self.shallow_max < self.normal_max < self.deep_max) or self.start_min_ref <= 0 or self.expire_bars < 1:
            raise ValueError("invalid PullbackParams")


DEFAULT_PB_PARAMS = PullbackParams()
