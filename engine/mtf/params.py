from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.anatomy import AnatomyParams
from engine.market_state import MarketStateParams
from engine.price_action import PriceActionParams
from engine.structure import StructureParams

ALIGNMENTS = ("ALIGNED_UP", "ALIGNED_DOWN", "CONFLICT_LTF_UP", "CONFLICT_LTF_DOWN", "HTF_UP_LTF_OTHER", "HTF_DOWN_LTF_OTHER",
              "HTF_RANGING", "HTF_TRANSITIONING", "HTF_UNCLEAR")


@dataclass(frozen=True)
class MTFParams:
    higher_minutes: tuple = (5, 15)                 # higher timeframes built from the 1-minute store
    htf_cross_session: bool = True                  # HTF structure continues across the overnight gap (within-session HTF states are mostly UNDEFINED)
    horizon_bars: int = 15                          # 1-minute horizon for the study
    anatomy: AnatomyParams = AnatomyParams()
    ltf_state: MarketStateParams = MarketStateParams()

    def htf_state_params(self) -> MarketStateParams:
        return MarketStateParams(price_action=PriceActionParams(structure=StructureParams(reset_each_session=not self.htf_cross_session)))

    def as_dict(self) -> dict:
        d = asdict(self); d["anatomy"] = self.anatomy.as_dict(); d["ltf_state"] = self.ltf_state.as_dict(); return d


DEFAULT_MTF_PARAMS = MTFParams()
