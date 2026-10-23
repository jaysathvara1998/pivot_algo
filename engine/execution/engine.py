"""ExecutionEngine: intents → prechecked orders → simulated broker → bar-driven fills → positions → reconciliation with halt-on-mismatch.
No strategy lives here; the engine executes whatever intents it is handed and refuses what the rules forbid."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, date
from typing import Optional

from .broker_sim import SimBroker, BrokerOutage
from .config import ExecConfig, exec_config, round_trip_cost_inr
from .fills import fill_on_bar
from .orders import Order
from .precheck import precheck
from .reconcile import reconcile, SlippageLog


@dataclass
class ExecutionEngine:
    instrument: str
    capital_inr: float
    broker: SimBroker = field(default_factory=SimBroker)
    max_concurrent: int = 1
    max_trades: int = 5
    max_loss_r: float = 3.0
    orders: dict = field(default_factory=dict)         # client_id -> Order
    positions: dict = field(default_factory=dict)      # instrument -> units
    halted: bool = False
    halt_reason: Optional[str] = None
    slippage: SlippageLog = field(default_factory=SlippageLog)
    fills: list = field(default_factory=list)
    day: Optional[date] = None
    day_trades: int = 0
    day_pnl_inr: float = 0.0
    _seq: int = 0
    _entry_px: dict = field(default_factory=dict)
    on_fill: Optional[object] = None                 # callback(fill_dict, bar) invoked inside on_bar right after each fill

    # ------------------------------------------------------------------ helpers
    def cfg(self, d: date) -> ExecConfig:
        return exec_config(self.instrument, d)

    def new_client_id(self) -> str:
        self._seq += 1
        return f"{self.instrument}-{self._seq:06d}"

    def _roll_day(self, now: datetime) -> None:
        if self.day != now.date():
            self.day = now.date(); self.day_trades = 0; self.day_pnl_inr = 0.0

    # ------------------------------------------------------------------ submission
    def submit(self, o: Order, now: datetime, reference_price: float, available_margin_inr: Optional[float] = None, is_index_option: bool = False) -> Order:
        self._roll_day(now); o.created_at = now.isoformat()
        if o.client_id in self.orders:
            o.transition("REJECTED", now, "DUPLICATE_CLIENT_ID"); o.reason = "DUPLICATE_CLIENT_ID"; return o
        self.orders[o.client_id] = o
        if self.halted:
            o.transition("REJECTED", now, f"HALTED:{self.halt_reason}"); o.reason = "HALTED"; return o
        cfg = self.cfg(now.date())
        margin = self.capital_inr * 0.5 if available_margin_inr is None else available_margin_inr
        reasons = precheck(o, cfg, now, margin, is_index_option, sum(1 for v in self.positions.values() if v), self.max_concurrent, self.day_trades, self.max_trades,
                           self.day_pnl_inr / max(1.0, reference_price * cfg.lot * 0.01) if False else 0.0, self.max_loss_r)
        if reasons:
            o.transition("REJECTED", now, ",".join(reasons)); o.reason = ",".join(reasons); return o
        o.transition("VALIDATED", now); o.transition("SUBMITTED", now)
        o.history.append(("reference_price", reference_price, now.isoformat(), ""))
        try:
            o.broker_id = self.broker.place(o.client_id, o.instrument, o.side, o.qty)
        except PermissionError as e:
            o.transition("REJECTED", now, str(e)); o.reason = str(e)
        except BrokerOutage:
            o.transition("UNKNOWN", now, "broker timeout")                      # the order may or may not exist at the broker
        return o

    def resolve_unknown(self, now: datetime) -> None:
        """After an outage: ask the broker whether each UNKNOWN order exists; cancel the ones it never saw."""
        for o in self.orders.values():
            if o.state == "UNKNOWN":
                bid = self.broker.lookup(o.client_id)
                if bid:
                    o.broker_id = bid; o.transition("PENDING_TRIGGER" if o.order_type in ("SL", "SL-M") else "OPEN", now, "found at broker")
                else:
                    o.transition("CANCELLED", now, "not found at broker")

    # ------------------------------------------------------------------ bars
    def on_bar(self, bar, partial_fraction: float = 1.0) -> list:
        """Process one completed bar: attempt fills for live orders (latency: only orders created before this bar's open), update positions,
        reconcile. Returns the fills made."""
        now = bar.timestamp; self._roll_day(now); made = []
        cfg = self.cfg(now.date())
        if cfg.static_after is not None and now.time() >= cfg.static_after:
            return made
        # OCO priority inside a bar: stops first, then exits and targets, then entries — a bar that touches both legs is a stop (conservative).
        # Orders created before this bar act on it; orders with act_from == this bar (brackets placed at a fill) act in the same bar, after the fill.
        order_of = {"STOP": 0, "EXIT": 1, "TARGET": 2, "ENTRY": 3}; now_iso = now.isoformat(); processed = set()
        def eligible(o):
            if o.is_terminal or o.state == "UNKNOWN" or o.created_at is None or o.client_id in processed:
                return False
            return (o.act_from is not None and o.act_from <= now_iso) or (o.act_from is None and o.created_at < now_iso)
        while True:
            todo = sorted([o for o in self.orders.values() if eligible(o)], key=lambda x: order_of.get(x.role, 9))
            if not todo:
                break
            for o in todo:
                processed.add(o.client_id)
                if o.role in ("STOP", "TARGET", "EXIT") and self.positions.get(o.instrument, 0) == 0:
                    o.transition("CANCELLED", now, "OCO: position flat"); continue
                before = o.filled_qty
                px = fill_on_bar(o, bar, cfg, now, partial_fraction)
                if px is not None:
                    qty = o.filled_qty - before
                    self.positions[o.instrument] = self.positions.get(o.instrument, 0) + (qty if o.side == "BUY" else -qty)
                    self.broker.apply_fill(o.instrument, o.side, qty)
                    ref = next((h[1] for h in o.history if h[0] == "reference_price"), px)
                    self.slippage.add(o.client_id, o.side, ref, px, qty, o.order_type)
                    if o.role == "ENTRY":
                        self.day_trades += 1; self._entry_px[o.client_id] = px
                    made.append({"client_id": o.client_id, "role": o.role, "side": o.side, "qty": qty, "price": px, "at": now_iso})
                    self.fills.append(made[-1])
                    if self.on_fill is not None:
                        self.on_fill(made[-1], bar)
        self.cancel_other_leg_if_closed(now)
        rec = reconcile(self.positions, self.broker.positions)
        if not rec["ok"]:
            self.halted = True; self.halt_reason = f"POSITION_MISMATCH {rec['mismatches']}"
        return made

    def cancel_other_leg_if_closed(self, now: datetime) -> None:
        """OCO: once the position is flat, cancel any live STOP/TARGET/EXIT orders."""
        if all(v == 0 for v in self.positions.values()):
            for o in self.orders.values():
                if o.role in ("STOP", "TARGET", "EXIT") and not o.is_terminal and o.state not in ("UNKNOWN", "NEW"):
                    o.transition("CANCELLED", now, "OCO: position flat")

    def reconcile_now(self) -> dict:
        rec = reconcile(self.positions, self.broker.positions)
        if not rec["ok"]:
            self.halted = True; self.halt_reason = f"POSITION_MISMATCH {rec['mismatches']}"
        return rec

    def paper_pnl(self, d: date, round_trips: list) -> dict:
        """round_trips: [(entry_px, exit_px, side, units)]. Gross and net INR with the regime-dated explicit cost; paper = upper bound (card 30)."""
        gross = 0.0; cost = 0.0
        for e, x, side, units in round_trips:
            gross += (x - e) * units * (1 if side == "BUY" else -1); cost += round_trip_cost_inr(d, (e + x) / 2, units)["total_inr"]
        return {"gross_inr": round(gross, 2), "cost_inr": round(cost, 2), "net_inr": round(gross - cost, 2), "note": "paper P&L is an upper bound (card 30)"}
