"""Simulated broker with injectable failures (card 30: rejections, outages, duplicates, position drift). PROJECT DEFINITION."""
from __future__ import annotations

from dataclasses import dataclass, field


class BrokerOutage(Exception):
    pass


@dataclass
class SimBroker:
    reject_next: list = field(default_factory=list)      # reasons to reject the next placements (popped)
    outage_next: int = 0                                 # number of next calls that raise BrokerOutage (after possibly accepting the order!)
    accept_during_outage: bool = True                    # the real failure mode: the broker accepts, the API call times out
    positions: dict = field(default_factory=dict)        # instrument -> net units
    orders: dict = field(default_factory=dict)           # broker_id -> client_id
    seen_client_ids: set = field(default_factory=set)
    _n: int = 0

    def place(self, client_id: str, instrument: str, side: str, qty: int) -> str:
        if client_id in self.seen_client_ids:
            raise ValueError(f"duplicate client_id {client_id}")        # idempotency: the system must never resend the same id
        if self.reject_next:
            raise PermissionError(self.reject_next.pop(0))
        self._n += 1; bid = f"B{self._n:06d}"
        if self.outage_next > 0:
            self.outage_next -= 1
            if self.accept_during_outage:
                self.seen_client_ids.add(client_id); self.orders[bid] = client_id
            raise BrokerOutage("timeout")
        self.seen_client_ids.add(client_id); self.orders[bid] = client_id
        return bid

    def lookup(self, client_id: str):
        for bid, cid in self.orders.items():
            if cid == client_id:
                return bid
        return None

    def apply_fill(self, instrument: str, side: str, qty: int) -> None:
        self.positions[instrument] = self.positions.get(instrument, 0) + (qty if side == "BUY" else -qty)

    def drift(self, instrument: str, units: int) -> None:
        """Inject a position the system does not know about (manual trade, missed fill)."""
        self.positions[instrument] = self.positions.get(instrument, 0) + units
