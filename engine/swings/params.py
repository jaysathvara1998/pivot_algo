from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class SwingParams:
    fractal_n: int = 3              # bar i is a fractal high if high_i > every high of the n bars on each side; confirmed at i+n
    zigzag_T: float = 3.0           # zigzag reversal when |close - candidate extreme| >= T x reference_range(current bar)
    reset_each_session: bool = True # swing state does not cross the overnight gap (card 15)
    equal_tol: float = 0.0          # |price - previous| <= tol -> EQUAL label (0 = exact)

    def as_dict(self) -> dict:
        return asdict(self)

    def __post_init__(self):
        if self.fractal_n < 1 or self.zigzag_T <= 0 or self.equal_tol < 0:
            raise ValueError("fractal_n >= 1, zigzag_T > 0, equal_tol >= 0")


DEFAULT_SWING_PARAMS = SwingParams()
