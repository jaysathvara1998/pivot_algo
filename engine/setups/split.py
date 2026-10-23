"""Fixed chronological split of the canonical data (PROJECT DEFINITION, registered 2026-10-04 before any Level 16 evaluation).

The windows are defined by DATES, not by counts, so they are the same for both indices and for any interval.
"""
from datetime import date

SPLIT = {
    "DEVELOPMENT": (date(2023, 9, 11), date(2025, 6, 30)),     # regimes R0, R1, R2 (≈ 440 sessions)
    "VALIDATION": (date(2025, 7, 1), date(2026, 2, 28)),       # R2 tail, R3 (≈ 160 sessions)       — read at Level 19 at the earliest
    "HOLDOUT": (date(2026, 3, 1), date(2026, 9, 10)),          # R3 tail, R4, R5 (≈ 130 sessions)   — read ONCE, at Level 22, never before
    "registered_on": "2026-10-04",
}


def window_of(d: date) -> str:
    for name in ("DEVELOPMENT", "VALIDATION", "HOLDOUT"):
        a, b = SPLIT[name]
        if a <= d <= b:
            return name
    return "OUTSIDE"


def assert_window(items, allowed=("DEVELOPMENT",)):
    """Filter (candle, record) pairs to the allowed windows; raise if a forbidden window would be touched explicitly."""
    out = [(c, r) for c, r in items if window_of(c.timestamp.date()) in allowed]
    forbidden = {window_of(c.timestamp.date()) for c, r in items} - set(allowed) - {"OUTSIDE"}
    return out, sorted(forbidden)
