"""Single-candle representation and geometry.

Every formula here is a PROJECT DEFINITION fixed in
skills/01_ohlc_price/DEFINITIONS.md. Change the document first, then the code.

Design rules
------------
* Pure functions of one candle. No access to neighbours (no look-ahead by construction).
* Division by a zero range is never performed; the result is ``None`` ("undefined").
* No thresholds. "Near-flat", "small", "large" are Level 2 concepts; Level 1 only
  exposes the raw ratios they will need.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class Direction(str, Enum):
    BULLISH = "BULLISH"   # close > open
    BEARISH = "BEARISH"   # close < open
    FLAT = "FLAT"         # close == open (exact equality; no tolerance at Level 1)


class InvalidOHLCError(ValueError):
    """Raised when four prices cannot describe a real candle.

    ``code`` is one of:
      NON_NUMERIC, NON_FINITE, NON_POSITIVE, LOW_ABOVE_HIGH,
      OPEN_OUTSIDE_RANGE, CLOSE_OUTSIDE_RANGE
    """

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code


def validate_ohlc(open_: float, high: float, low: float, close: float, *, tol: float = 0.0) -> None:
    """Raise ``InvalidOHLCError`` unless (open, high, low, close) is a valid candle.

    Conditions (all required):
      1. each value is a real number (not bool, not None, not str)
      2. each value is finite
      3. each value is > 0 (index/futures/equity prices are strictly positive)
      4. low <= high
      5. low - tol <= open <= high + tol
      6. low - tol <= close <= high + tol

    ``tol`` defaults to 0 (strict). A small positive tolerance may be passed when a data
    vendor rounds open/close independently of high/low; the caller must document it.
    """
    for name, v in (("open", open_), ("high", high), ("low", low), ("close", close)):
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            raise InvalidOHLCError("NON_NUMERIC", f"{name}={v!r} is not a number")
        if not math.isfinite(v):
            raise InvalidOHLCError("NON_FINITE", f"{name}={v!r} is not finite")
        if v <= 0:
            raise InvalidOHLCError("NON_POSITIVE", f"{name}={v!r} must be > 0")
    if tol < 0:
        raise ValueError("tol must be >= 0")
    if low > high:
        raise InvalidOHLCError("LOW_ABOVE_HIGH", f"low={low} > high={high}")
    if not (low - tol <= open_ <= high + tol):
        raise InvalidOHLCError("OPEN_OUTSIDE_RANGE", f"open={open_} not within [{low}, {high}]")
    if not (low - tol <= close <= high + tol):
        raise InvalidOHLCError("CLOSE_OUTSIDE_RANGE", f"close={close} not within [{low}, {high}]")


@dataclass(frozen=True)
class Candle:
    """One OHLC candle.

    timestamp : start of the candle interval (left-labelled). Must be timezone-aware.
    interval  : the candle duration (e.g. timedelta(minutes=1)).
    volume    : carried for provenance only. Indices have no traded volume. NOT used.
    """

    timestamp: datetime
    open: float
    high: float
    low: float
    close: float
    interval: timedelta
    volume: Optional[float] = None

    def __post_init__(self) -> None:
        if not isinstance(self.timestamp, datetime):
            raise TypeError("timestamp must be a datetime")
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware (ambiguous otherwise)")
        if not isinstance(self.interval, timedelta) or self.interval <= timedelta(0):
            raise ValueError("interval must be a positive timedelta")
        validate_ohlc(self.open, self.high, self.low, self.close)

    @property
    def end(self) -> datetime:
        """Exclusive end of the interval: the first instant at which this candle is complete."""
        return self.timestamp + self.interval


# --------------------------------------------------------------------------- geometry

def range_(c: Candle) -> float:
    """range = high - low  (>= 0)"""
    return c.high - c.low


def body(c: Candle) -> float:
    """body = |close - open|  (>= 0)"""
    return abs(c.close - c.open)


def upper_wick(c: Candle) -> float:
    """upper_wick = high - max(open, close)  (>= 0)"""
    return c.high - max(c.open, c.close)


def lower_wick(c: Candle) -> float:
    """lower_wick = min(open, close) - low  (>= 0)"""
    return min(c.open, c.close) - c.low


def direction(c: Candle) -> Direction:
    """BULLISH if close > open, BEARISH if close < open, else FLAT."""
    if c.close > c.open:
        return Direction.BULLISH
    if c.close < c.open:
        return Direction.BEARISH
    return Direction.FLAT


def close_location(c: Candle) -> Optional[float]:
    """close_location = (close - low) / range, in [0, 1]. None when range == 0.

    1.0 → closed at the high; 0.0 → closed at the low; 0.5 → closed mid-range.
    """
    r = range_(c)
    if r == 0:
        return None
    return (c.close - c.low) / r


def body_to_range(c: Candle) -> Optional[float]:
    """body_to_range = body / range, in [0, 1]. None when range == 0."""
    r = range_(c)
    if r == 0:
        return None
    return body(c) / r


def intra_change(c: Candle) -> float:
    """Signed change within the candle: close - open."""
    return c.close - c.open


def relative_intra_change(c: Candle) -> float:
    """(close - open) / open. open > 0 is guaranteed by validation."""
    return (c.close - c.open) / c.open


def geometry(c: Candle) -> dict:
    """All Level 1 single-candle facts in one deterministic mapping."""
    return {
        "timestamp": c.timestamp.isoformat(),
        "interval_seconds": int(c.interval.total_seconds()),
        "open": c.open,
        "high": c.high,
        "low": c.low,
        "close": c.close,
        "range": range_(c),
        "body": body(c),
        "upper_wick": upper_wick(c),
        "lower_wick": lower_wick(c),
        "direction": direction(c).value,
        "close_location": close_location(c),
        "body_to_range": body_to_range(c),
        "intra_change": intra_change(c),
        "relative_intra_change": relative_intra_change(c),
    }


def observations(c: Candle) -> list[str]:
    """Plain-language OBSERVATIONS (not interpretations) about one candle.

    Each sentence is a fact derivable from the four prices alone. No adjectives such as
    'strong', 'weak', 'rejection' — those are Level 3 interpretations.
    """
    g = geometry(c)
    out = [
        f"Candle opened at {c.open}, traded between {c.low} and {c.high}, and closed at {c.close}.",
        f"Range is {g['range']}.",
    ]
    d = direction(c)
    if d is Direction.BULLISH:
        out.append(f"Close is above open by {g['body']} (bullish candle).")
    elif d is Direction.BEARISH:
        out.append(f"Close is below open by {g['body']} (bearish candle).")
    else:
        out.append("Close equals open (flat candle).")
    if g["range"] == 0:
        out.append("Range is zero: all four prices are equal; close location and body-to-range are undefined.")
    else:
        out.append(f"Close is located at {g['close_location']:.4f} of the range measured from the low.")
        out.append(f"Body occupies {g['body_to_range']:.4f} of the range.")
        out.append(f"Upper wick is {g['upper_wick']}; lower wick is {g['lower_wick']}.")
    return out
