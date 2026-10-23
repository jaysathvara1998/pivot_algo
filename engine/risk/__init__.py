"""Level 18 — Risk management (Skill 18).

Risk as arithmetic and measurement: stop width from the measured conditional distribution of the next bars' range (by
volatility regime, phase, expiry flag, post-event flags); fixed-fractional sizing with lot discretisation and the margin
bound (cards 27–28); drawdown and ruin by bootstrap on an R distribution; daily loss and trade-count limits enforced by
the system. Nothing here selects a trade.
"""
from .stops import StopWidthTable, context_key
from .sizing import Contract, CONTRACTS, lots_for_risk, SizingResult
from .ruin import bootstrap_drawdown, growth_fraction_of_max, kelly_fraction
from .limits import DailyLimits, apply_limits

__all__ = ["StopWidthTable", "context_key", "Contract", "CONTRACTS", "lots_for_risk", "SizingResult", "bootstrap_drawdown",
           "growth_fraction_of_max", "kelly_fraction", "DailyLimits", "apply_limits"]
