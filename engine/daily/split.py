"""Fixed split of the DAILY series (PROJECT DEFINITION, registered 2026-10-05 in skills/27_daily_behaviour/HYPOTHESES.md)."""
from datetime import date

DAILY_SPLIT = {"DEVELOPMENT": (date(2006, 1, 2), date(2015, 12, 31)), "VALIDATION": (date(2016, 1, 1), date(2019, 12, 31)),
               "HOLDOUT": (date(2020, 1, 1), date(2023, 8, 31)), "SEEN": (date(2023, 9, 1), date(2030, 12, 31)), "registered_on": "2026-10-05"}
HALVES = {"H1": (date(2006, 1, 2), date(2010, 12, 31)), "H2": (date(2011, 1, 1), date(2015, 12, 31))}


def daily_window_of(d: date) -> str:
    for k in ("DEVELOPMENT", "VALIDATION", "HOLDOUT", "SEEN"):
        a, b = DAILY_SPLIT[k]
        if a <= d <= b:
            return k
    return "OUTSIDE"
