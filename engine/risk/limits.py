"""System-enforced daily limits (card 25 rule 3: stopping rules are enforced by the system, not the operator)."""
from __future__ import annotations

from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class DailyLimits:
    max_loss_r: float = 3.0          # stop trading for the day after this cumulative loss in R
    max_trades: int = 5              # stop trading after this many trades in a day
    max_concurrent: int = 1          # portfolio of one

    def as_dict(self) -> dict:
        return asdict(self)


def apply_limits(trades, limits: DailyLimits, day_of):
    """trades: ordered Trade-like objects with .r_net and .entry_index / .exit_index; day_of(trade) -> hashable day key.
    Returns (kept, blocked, reasons) where blocked trades were refused by a limit at the time of their entry."""
    kept, blocked, reasons = [], [], {}
    state = {}
    for t in trades:
        d = day_of(t)
        st = state.setdefault(d, {"loss": 0.0, "count": 0, "last_exit": None})
        if st["last_exit"] is not None and getattr(t, "entry_index", 0) <= st["last_exit"]:
            blocked.append(t); reasons[id(t)] = "CONCURRENT"; continue
        if st["count"] >= limits.max_trades:
            blocked.append(t); reasons[id(t)] = "MAX_TRADES"; continue
        if -st["loss"] >= limits.max_loss_r:
            blocked.append(t); reasons[id(t)] = "MAX_LOSS"; continue
        kept.append(t); st["count"] += 1; st["loss"] += t.r_net; st["last_exit"] = getattr(t, "exit_index", None)
    return kept, blocked, reasons
