from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.price_action import PriceActionParams

STATES = ("TRENDING_UP", "TRENDING_DOWN", "RANGING", "TRANSITIONING", "UNCLEAR")


@dataclass(frozen=True)
class MarketStateParams:
    min_state_bars: int = 3            # a Level 6 state must be at least this old before it is read as TRENDING / RANGING
    transition_bars: int = 5           # bars after a CHOCH or trend-state change during which the state is TRANSITIONING
    vol_window: int = 12               # bars of relative range for the volatility regime
    vol_low: float = 0.6               # median relative range below this -> LOW
    vol_high: float = 1.6              # above this -> HIGH
    price_action: PriceActionParams = PriceActionParams()

    def as_dict(self) -> dict:
        d = asdict(self); d["price_action"] = self.price_action.as_dict(); return d

    def __post_init__(self):
        if self.min_state_bars < 1 or self.transition_bars < 1 or self.vol_window < 2 or not (0 < self.vol_low < 1 < self.vol_high):
            raise ValueError("invalid MarketStateParams")


DEFAULT_MS_PARAMS = MarketStateParams()
