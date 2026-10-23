from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.structure import StructureParams
from engine.sequences import SequenceParams


@dataclass(frozen=True)
class PriceActionParams:
    structure: StructureParams = StructureParams()
    sequences: SequenceParams = SequenceParams()
    struct_zone_edges: tuple = (1 / 3, 2 / 3)   # LOWER / MIDDLE / UPPER thirds of the structural range; <0 BELOW, >1 ABOVE
    session_top: float = 0.9                    # session-range location >= this -> SESSION_LOC_TOP (if not a new high)
    session_bottom: float = 0.1
    near_ref: float = 0.5                       # "near" a swing: within this many reference ranges

    def as_dict(self) -> dict:
        return asdict(self)


DEFAULT_PA_PARAMS = PriceActionParams()
