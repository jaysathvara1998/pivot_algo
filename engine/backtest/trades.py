"""Normalised trade record for the backtest engine, with adapters from the Level 17/19/20 simulators."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Optional

from engine.session.labels import phase_of, regime_of


@dataclass
class BtTrade:
    entry_ts: str
    exit_ts: str
    direction: str
    r_gross: float
    cost_r: float          # cost at the recorded (development-era) level, in R
    mae_r: float
    mfe_r: float
    bars_held: int
    exit_reason: str
    stop_ref: float
    regime: str
    phase: str
    month: str

    @property
    def r_net(self) -> float:
        return self.r_gross - self.cost_r

    def r_net_at(self, cost_multiplier: float) -> float:
        return self.r_gross - cost_multiplier * self.cost_r

    def as_dict(self) -> dict:
        d = asdict(self); d["r_net"] = round(self.r_net, 4); return d


def _mk(entry: datetime, exit_: datetime, direction, r_gross, cost_r, mae, mfe, bars, reason, stop_ref) -> BtTrade:
    d = entry.date()
    return BtTrade(entry.isoformat(), exit_.isoformat(), direction, r_gross, cost_r, mae, mfe, bars, reason, stop_ref, regime_of(d), phase_of(entry.time(), d), f"{d.year}-{d.month:02d}")


def from_bar_trades(trades, seq) -> list:
    """Level 17 `Trade` / Level 19 `ManagedTrade` objects with entry_index / exit_index into `seq` (rows with 'candle')."""
    out = []
    for t in trades:
        e = seq[t.entry_index]["candle"].timestamp; x = seq[t.exit_index]["candle"].timestamp
        out.append(_mk(e, x, t.direction, t.r_gross, t.cost_r, t.mae_r, t.mfe_r, t.exit_index - t.entry_index + 1, t.exit_reason, t.stop_ref))
    return out


def from_gap_trades(trades, sessions) -> list:
    """Level 20 `GapTrade` objects; `sessions` are the Session objects they were simulated on (matched by date)."""
    by_date = {s.date.isoformat(): s for s in sessions}
    out = []
    for t in trades:
        s = by_date[t.date]
        e = s.bars[t.entry_bar].timestamp; x = s.bars[t.exit_bar].timestamp
        out.append(_mk(e, x, t.direction, t.r_gross, t.cost_r, t.mae_r, t.mfe_r, t.exit_bar - t.entry_bar + 1, t.exit_reason, t.stop_ref))
    return out
