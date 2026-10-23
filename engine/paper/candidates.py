"""Pluggable candidate rules for the paper pipeline. A candidate sees only closed bars and returns order intents; it never touches fills.
The only candidate implemented is the Level 20 gap-fade cell UP | BAR_5 | REF_Q90 | SESSION, run as MONITORING (no viable strategy exists)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from engine.strategy.rules import STOP_REF_Q90, STOP_FLOOR_REF


@dataclass
class Intent:
    side: str                 # BUY | SELL
    order_type: str           # MARKET | LIMIT | SL-M
    role: str                 # ENTRY | STOP | TARGET | EXIT
    price: Optional[float] = None
    trigger: Optional[float] = None
    note: str = ""


class GapFadeCandidate:
    name = "GAP_FADE|UP|BAR_5|REF_Q90|SESSION"

    def __init__(self):
        self._armed_day = None; self._done_day = None

    def on_bar(self, st: dict) -> list:
        """st: bar, rec, ctx, position (units, signed), session_index, is_second_last_bar."""
        ctx = st["ctx"]; rec = st["rec"]; b = st["bar"]
        d = b.timestamp.date()
        if st["position"] == 0 and st["session_index"] == 4 and self._done_day != d:
            # the sixth bar (index 5) opens next; decide now on the five closed bars
            if ctx.gap_class == "SMALL" and ctx.gap_sign == "UP" and ctx.prev_close is not None and rec.reference_range:
                touched = st["session_touched_prev_close"]
                if not touched and b.close > ctx.prev_close:
                    self._done_day = d
                    return [Intent("SELL", "MARKET", "ENTRY", note=self.name)]
        return []

    def brackets(self, st: dict, entry_px: float) -> list:
        ctx = st["ctx"]; rec = st["rec"]
        dist = max(STOP_REF_Q90 * rec.reference_range, STOP_FLOOR_REF * rec.reference_range)
        return [Intent("BUY", "SL-M", "STOP", trigger=round(entry_px + dist, 2)), Intent("BUY", "LIMIT", "TARGET", price=round(ctx.prev_close, 2))]
