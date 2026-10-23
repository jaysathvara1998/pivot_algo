"""Session, regime and expiry labelling (PROJECT DEFINITION + SOURCE FACT tables, Skill 14)."""
from __future__ import annotations

import calendar as _cal
from dataclasses import dataclass, field, asdict
from datetime import date, time, timedelta
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from .calendar_data import WEEKDAY_CLOSURES, PUBLISHED_2026_REMAINDER, SPECIAL_SESSION_DATES

PHASES = ("OPEN", "MORNING", "MIDDAY", "AFTERNOON", "CLOSE", "STATIC")
# regime boundaries recorded in the Foundations track (PROJECT_STATE "Regime boundaries"); names are PROJECT DEFINITIONS
REGIMES = (
    ("R0_pre_STT", date(2000, 1, 1)),               # before 2024-10-01 (old STT / charges; several NSE weeklies)
    ("R1_STT_oct24", date(2024, 10, 1)),            # STT rise, true-to-label charges
    ("R2_one_weekly", date(2024, 11, 20)),          # one weekly expiry per exchange; NIFTY lot 75; +2 % expiry-day ELM
    ("R3_tue_thu_expiry", date(2025, 9, 1)),        # NSE Tuesday / BSE Thursday expiry
    ("R4_stt_apr26", date(2026, 4, 1)),             # STT futures 0.05 % / options 0.15 %; brokerage bracket
    ("R5_CAS", date(2026, 8, 3)),                   # Closing Auction Session: index static 15:15-15:29
)
NSE_SWITCH = date(2025, 9, 1)


@dataclass(frozen=True)
class SessionParams:
    open_end: time = time(9, 45)
    morning_end: time = time(11, 30)
    midday_end: time = time(13, 30)
    afternoon_end: time = time(15, 0)
    static_from: date = date(2026, 8, 3)
    static_start: time = time(15, 15)

    def as_dict(self) -> dict:
        return {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in asdict(self).items()}


DEFAULT_SESSION_PARAMS = SessionParams()


def holidays(year: int) -> dict:
    h = dict(WEEKDAY_CLOSURES.get(year, {}))
    if year == 2026:
        h.update(PUBLISHED_2026_REMAINDER)
    return h


def is_trading_day(d: date) -> bool:
    if d in SPECIAL_SESSION_DATES:
        return True
    return d.weekday() < 5 and d not in holidays(d.year)


def previous_trading_day(d: date) -> date:
    while not is_trading_day(d):
        d -= timedelta(days=1)
    return d


def last_weekday(year: int, month: int, weekday: int) -> date:
    last = date(year, month, _cal.monthrange(year, month)[1])
    return last - timedelta(days=(last.weekday() - weekday) % 7)


def nifty_weekly_weekday(d: date) -> int:
    return 1 if d >= NSE_SWITCH else 3


def sensex_weekly_weekday(d: date) -> int:
    return 3 if d >= NSE_SWITCH else (1 if d >= date(2025, 1, 1) else 4)   # BSE Tuesday from ~Jan 2025 is SECONDARY/UNVERIFIED (topic 19)


def expiry_flags(d: date) -> dict:
    """Weekly / monthly expiry flags for NIFTY and SENSEX on day d, holiday-shifted (topic 19 rules)."""
    week_start = d - timedelta(days=d.weekday())
    out = {}
    for name, wd in (("nifty", nifty_weekly_weekday(d)), ("sensex", sensex_weekly_weekday(d))):
        out[f"{name}_weekly_expiry"] = previous_trading_day(week_start + timedelta(days=wd)) == d
        out[f"{name}_monthly_expiry"] = previous_trading_day(last_weekday(d.year, d.month, wd)) == d
    return out


def phase_of(t: time, d: date, p: SessionParams = DEFAULT_SESSION_PARAMS) -> str:
    if d >= p.static_from and t >= p.static_start:
        return "STATIC"
    if t < p.open_end:
        return "OPEN"
    if t < p.morning_end:
        return "MORNING"
    if t < p.midday_end:
        return "MIDDAY"
    if t < p.afternoon_end:
        return "AFTERNOON"
    return "CLOSE"


def regime_of(d: date) -> str:
    name = REGIMES[0][0]
    for n, start in REGIMES:
        if d >= start:
            name = n
    return name


@dataclass
class SessionRecord:
    timestamp: str
    session_index: int
    phase: str
    first_bar: bool
    static_window: bool
    minutes_since_open: int
    weekday: int
    regime: str
    special_session: Optional[str]
    flags: dict = field(default_factory=dict)     # expiry flags
    labels: list = field(default_factory=list)

    def as_dict(self) -> dict:
        return asdict(self)


class SessionEngine:
    def __init__(self, p: SessionParams = DEFAULT_SESSION_PARAMS):
        self.p = p
        self._session = None; self._idx = -1; self._open_minute = None; self._flags = {}; self._regime = None; self._special = None

    def process(self, c: Candle, rec: AnatomyRecord) -> SessionRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            self._session = d; self._idx = -1; self._open_minute = c.timestamp.hour * 60 + c.timestamp.minute
            self._flags = expiry_flags(d); self._regime = regime_of(d); self._special = SPECIAL_SESSION_DATES.get(d)
        self._idx += 1
        t = c.timestamp.time()
        phase = phase_of(t, d, self.p)
        labels = [f"PHASE_{phase}", f"REGIME_{self._regime}"] + [f"EXPIRY_{k.upper()}" for k, v in self._flags.items() if v]
        if self._idx == 0:
            labels.append("FIRST_BAR")
        return SessionRecord(c.timestamp.isoformat(), self._idx, phase, self._idx == 0, phase == "STATIC",
                             c.timestamp.hour * 60 + c.timestamp.minute - self._open_minute, d.weekday(), self._regime, self._special, dict(self._flags), labels)


def replay_session(items: Iterable[tuple[Candle, AnatomyRecord]], p: SessionParams = DEFAULT_SESSION_PARAMS) -> Iterator[tuple[int, Candle, SessionRecord]]:
    eng = SessionEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
