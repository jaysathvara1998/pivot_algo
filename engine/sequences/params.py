from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class SequenceParams:
    min_run: int = 3                    # consecutive same-direction closes to call a DIRECTIONAL_RUN (FLAT breaks a run)
    max_run_label: int = 5              # run lengths >= this share one label ("5+")
    impulse_min_strength: float = 0.75  # |close_n - open_1| / sum(reference ranges of the run) at or above this -> IMPULSE
    pullback_max_depth: float = 0.75    # retracement of the impulse's net move beyond this -> FAILED_MOVEMENT
    pullback_max_bars: int = 5          # pullback bars without resolution -> EXPIRED
    k_range: int = 3                    # strictly rising (falling) relative ranges over k bars -> EXPANSION (COMPRESSION) sequence
    k_body: int = 3                     # strictly rising (falling) relative bodies in a same-direction run -> MOMENTUM_BUILDING (FADING)
    k_excursion: int = 2                # consecutive Level 3 excursion-returned labels on the same side -> REPEATED_*_EXCURSION

    def as_dict(self) -> dict:
        return asdict(self)

    def __post_init__(self):
        if self.min_run < 2 or self.k_range < 2 or self.k_body < 2 or self.k_excursion < 2:
            raise ValueError("sequence lengths must be >= 2")
        if not (0 < self.pullback_max_depth <= 1) or self.impulse_min_strength <= 0:
            raise ValueError("depth in (0,1], strength > 0")


DEFAULT_SEQUENCE_PARAMS = SequenceParams()
