"""Trading session definition (Level 1 scope: time vocabulary only).

PROJECT DEFINITION: the regular equity/index session on NSE and BSE runs 09:15–15:30 IST.
SOURCE FACT: NSE and BSE publish 09:15 open / 15:30 close for the normal market segment
(pre-open 09:00–09:08/09:15 and post-close sessions exist and are excluded here).

Nothing about *behaviour* within the session is defined here; that is Level 14.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")


@dataclass(frozen=True)
class SessionSpec:
    """A regular session: candles whose [start, end) lies within [open, close) belong to it."""

    open: time
    close: time
    tz: ZoneInfo = IST
    name: str = "REGULAR"

    def bounds(self, d: date) -> tuple[datetime, datetime]:
        return (
            datetime.combine(d, self.open, tzinfo=self.tz),
            datetime.combine(d, self.close, tzinfo=self.tz),
        )


NSE_BSE_REGULAR = SessionSpec(open=time(9, 15), close=time(15, 30), tz=IST, name="NSE_BSE_REGULAR")


def session_date(ts: datetime, spec: SessionSpec = NSE_BSE_REGULAR) -> date:
    """Calendar date of the session a timestamp belongs to, in the session timezone."""
    return ts.astimezone(spec.tz).date()


def candle_in_session(start: datetime, interval: timedelta, spec: SessionSpec = NSE_BSE_REGULAR) -> bool:
    """True iff the whole candle interval [start, start+interval) lies inside the session.

    Example (1m): 09:15 → True, 09:14 → False, 15:29 → True, 15:30 → False.
    Example (15m): 15:15 → True (ends exactly 15:30), 15:30 → False.
    Weekends / holidays are NOT handled here: this is a time-of-day test only.
    """
    s = start.astimezone(spec.tz)
    o, c = spec.bounds(s.date())
    return o <= s and (s + interval) <= c
