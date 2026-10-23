"""All Level 2 thresholds in one logged, immutable object (card 18 rule 1: no unparameterised names)."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import date, time


@dataclass(frozen=True)
class AnatomyParams:
    # --- shape (ratios of the candle's own range) ---------------------------------------
    doji_max_body_to_range: float = 0.10          # body/range <= x  -> DOJI flag
    marubozu_max_wick_to_range: float = 0.05      # both wicks <= y*range -> MARUBOZU flag
    body_small_max: float = 0.35                  # body classes: NEGLIGIBLE <= doji_max < SMALL <= 0.35
    body_medium_max: float = 0.65                 #   < MEDIUM <= 0.65 < LARGE < full_min <= FULL
    body_full_min: float = 0.90
    wick_dominant_min: float = 0.50               # one wick >= 50 % of range -> *_DOMINANT
    wick_balanced_max_diff: float = 0.10          # |upper - lower| <= 10 % of range -> BALANCED
    close_zone_edges: tuple = (0.2, 0.4, 0.6, 0.8)  # five equal zones of close_location
    # --- relative size (reference = same clock minute, previous sessions only) ----------
    reference_sessions: int = 20                  # trailing window length, in completed sessions
    min_reference_sessions: int = 10              # below this the reference is UNAVAILABLE
    small_max_ratio: float = 0.5                  # range / reference median  < 0.5  -> SMALL
    large_min_ratio: float = 2.0                  #                           >= 2.0 -> LARGE
    extreme_min_ratio: float = 4.0                #                           >= 4.0 -> EXTREME
    local_window_bars: int = 12                   # deseasonalised range vs previous k bars of this session
    # --- session / regime --------------------------------------------------------------
    session_open: time = time(9, 15)
    static_window_from: date = date(2026, 8, 3)   # Closing Auction Session regime: index static 15:15-15:29
    static_window_start: time = time(15, 15)

    def as_dict(self) -> dict:
        d = asdict(self)
        d["session_open"] = self.session_open.isoformat()
        d["static_window_from"] = self.static_window_from.isoformat()
        d["static_window_start"] = self.static_window_start.isoformat()
        return d

    def __post_init__(self):
        if not (0 < self.doji_max_body_to_range <= self.body_small_max < self.body_medium_max < self.body_full_min <= 1):
            raise ValueError("body thresholds must be ordered 0 < doji <= small < medium < full <= 1")
        if not (0 < self.small_max_ratio < 1 < self.large_min_ratio < self.extreme_min_ratio):
            raise ValueError("size ratios must satisfy 0 < small < 1 < large < extreme")
        if self.min_reference_sessions > self.reference_sessions or self.min_reference_sessions < 1:
            raise ValueError("1 <= min_reference_sessions <= reference_sessions")
        if list(self.close_zone_edges) != sorted(self.close_zone_edges) or len(self.close_zone_edges) != 4:
            raise ValueError("close_zone_edges must be four increasing values")


DEFAULT_PARAMS = AnatomyParams()
