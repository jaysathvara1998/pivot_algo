"""Order-state machine (card 30 rule 1). PROJECT DEFINITION."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

ORDER_TYPES = ("MARKET", "LIMIT", "SL", "SL-M")
VALIDITY = ("DAY", "IOC")
STATES = ("NEW", "VALIDATED", "SUBMITTED", "OPEN", "PENDING_TRIGGER", "TRIGGERED", "TRIGGERED_UNFILLED", "PARTIALLY_FILLED", "FILLED", "CANCELLED", "REJECTED", "UNKNOWN")
TRANSITIONS = {
    "NEW": {"VALIDATED", "REJECTED"},
    "VALIDATED": {"SUBMITTED", "REJECTED"},
    "SUBMITTED": {"OPEN", "PENDING_TRIGGER", "REJECTED", "UNKNOWN", "FILLED", "PARTIALLY_FILLED", "CANCELLED"},
    "OPEN": {"PARTIALLY_FILLED", "FILLED", "CANCELLED", "UNKNOWN"},
    "PENDING_TRIGGER": {"TRIGGERED", "CANCELLED", "UNKNOWN"},
    "TRIGGERED": {"FILLED", "PARTIALLY_FILLED", "TRIGGERED_UNFILLED"},
    "TRIGGERED_UNFILLED": {"FILLED", "PARTIALLY_FILLED", "CANCELLED"},
    "PARTIALLY_FILLED": {"FILLED", "CANCELLED", "PARTIALLY_FILLED"},
    "UNKNOWN": {"OPEN", "PENDING_TRIGGER", "FILLED", "PARTIALLY_FILLED", "CANCELLED", "REJECTED"},
    "FILLED": set(), "CANCELLED": set(), "REJECTED": set(),
}
TERMINAL = {"FILLED", "CANCELLED", "REJECTED"}


class IllegalTransition(Exception):
    pass


@dataclass
class Order:
    client_id: str                    # idempotency key chosen by the system
    instrument: str
    side: str                         # BUY | SELL
    qty: int                          # units
    order_type: str                   # MARKET | LIMIT | SL | SL-M
    price: Optional[float] = None     # limit price (LIMIT, SL)
    trigger: Optional[float] = None   # trigger price (SL, SL-M)
    validity: str = "DAY"
    role: str = "ENTRY"               # ENTRY | STOP | TARGET | EXIT
    state: str = "NEW"
    filled_qty: int = 0
    avg_fill: Optional[float] = None
    broker_id: Optional[str] = None
    reason: Optional[str] = None
    created_at: Optional[str] = None
    act_from: Optional[str] = None      # when set, the order may act on the bar whose timestamp equals it (brackets placed at a fill)
    history: list = field(default_factory=list)

    def transition(self, new_state: str, at: Optional[datetime] = None, note: str = "") -> None:
        if new_state not in STATES:
            raise IllegalTransition(f"unknown state {new_state}")
        if new_state not in TRANSITIONS[self.state]:
            raise IllegalTransition(f"{self.client_id}: {self.state} → {new_state} not allowed")
        self.history.append((self.state, new_state, at.isoformat() if at else None, note)); self.state = new_state

    def record_fill(self, qty: int, price: float, at: Optional[datetime] = None) -> None:
        if qty <= 0 or self.filled_qty + qty > self.qty:
            raise ValueError("bad fill quantity")
        tot = (self.avg_fill or 0.0) * self.filled_qty + price * qty
        self.filled_qty += qty; self.avg_fill = tot / self.filled_qty
        self.transition("FILLED" if self.filled_qty == self.qty else "PARTIALLY_FILLED", at, f"fill {qty}@{price}")

    @property
    def remaining(self) -> int:
        return self.qty - self.filled_qty

    @property
    def is_terminal(self) -> bool:
        return self.state in TERMINAL
