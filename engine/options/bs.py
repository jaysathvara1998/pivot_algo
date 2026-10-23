"""Black–Scholes marks and expiry timing for the option study (PROJECT DEFINITION, Skill 26)."""
from __future__ import annotations

import math
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from engine.session.labels import previous_trading_day, nifty_weekly_weekday, sensex_weekly_weekday

IST = ZoneInfo("Asia/Kolkata")
RISK_FREE = 0.065
TRADING_MINUTES_PER_YEAR = 252 * 375
RANGE_CONST = 2 * math.sqrt(2 / math.pi)          # E[range] of a Brownian path over T = 1.596 σ√T


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def bs_price(S: float, K: float, T: float, sigma: float, kind: str, r: float = RISK_FREE) -> float:
    """European option value; T in years, sigma as a fraction. At T ≤ 0 returns intrinsic value."""
    if T <= 0 or sigma <= 0:
        return max(0.0, S - K) if kind == "C" else max(0.0, K - S)
    d1 = (math.log(S / K) + (r + 0.5 * sigma * sigma) * T) / (sigma * math.sqrt(T)); d2 = d1 - sigma * math.sqrt(T)
    if kind == "C":
        return S * _ncdf(d1) - K * math.exp(-r * T) * _ncdf(d2)
    return K * math.exp(-r * T) * _ncdf(-d2) - S * _ncdf(-d1)


def straddle_value(S: float, K: float, T: float, sigma: float) -> float:
    return bs_price(S, K, T, sigma, "C") + bs_price(S, K, T, sigma, "P")


def next_weekly_expiry(d: date, index: str) -> date:
    """Nearest weekly expiry date on or after d for 'nifty' / 'sensex' (holiday-shifted, Level 14 rules)."""
    wd = nifty_weekly_weekday(d) if index == "nifty" else sensex_weekly_weekday(d)
    week_start = d - timedelta(days=d.weekday())
    e = previous_trading_day(week_start + timedelta(days=wd))
    if e < d:
        e = previous_trading_day(week_start + timedelta(days=7 + wd))
    return e


def years_to_expiry(ts: datetime, expiry: date) -> float:
    end = datetime.combine(expiry, time(15, 30), tzinfo=IST)
    return max(0.0, (end - ts).total_seconds() / (365 * 86400))


def implied_range(spot: float, iv_pct: float, h_minutes: int) -> float:
    """Brownian expected high−low range over h trading minutes at annualised IV (per cent)."""
    return RANGE_CONST * spot * (iv_pct / 100.0) * math.sqrt(h_minutes / TRADING_MINUTES_PER_YEAR)
