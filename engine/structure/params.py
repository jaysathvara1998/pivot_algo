from __future__ import annotations

from dataclasses import dataclass, asdict

from engine.swings import SwingParams


@dataclass(frozen=True)
class StructureParams:
    detector: str = "ZIGZAG"            # which Level 5 detector drives structure: ZIGZAG | FRACTAL
    use_close: bool = True              # BOS/CHOCH on close beyond the swing (False: on high/low beyond)
    reset_each_session: bool = True     # structure does not cross the overnight gap by default
    swing: SwingParams = SwingParams()

    def as_dict(self) -> dict:
        d = asdict(self); return d

    def __post_init__(self):
        if self.detector not in ("ZIGZAG", "FRACTAL"):
            raise ValueError("detector must be ZIGZAG or FRACTAL")


DEFAULT_STRUCTURE_PARAMS = StructureParams()
