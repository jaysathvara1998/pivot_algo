"""Paper-trading replay driver (Skill 24): a session clock feeds closed bars through the Level 2 and Level 15 engines, a candidate, and the
ExecutionEngine in paper mode. No capital, no live connection. Kill switches: data gap, daily loss, engine halt."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import timedelta
from typing import Optional

from engine.anatomy import AnatomyEngine, DEFAULT_PARAMS
from engine.context import ContextEngine
from engine.execution import ExecutionEngine, Order, round_trip_cost_inr
from engine.setups.split import window_of
from .candidates import GapFadeCandidate

M1 = timedelta(minutes=1)


@dataclass
class PaperTrade:
    date: str
    candidate: str
    direction: str
    entry_bar: int
    entry: float
    stop: float
    target: Optional[float]
    exit_bar: int
    exit_price: float
    exit_role: str
    r_gross: float
    cost_inr: float
    gross_inr: float
    net_inr: float
    latency_bars: int

    def as_dict(self):
        from dataclasses import asdict
        return asdict(self)


@dataclass
class PaperDriver:
    instrument: str
    capital_inr: float = 2_000_000
    candidate: object = field(default_factory=GapFadeCandidate)
    trade_windows: tuple = ("DEVELOPMENT",)
    max_daily_loss_r: float = 3.0
    anatomy: AnatomyEngine = field(default_factory=lambda: AnatomyEngine(DEFAULT_PARAMS))
    context: ContextEngine = field(default_factory=ContextEngine)
    engine: ExecutionEngine = None
    trades: list = field(default_factory=list)
    events: list = field(default_factory=list)
    daily: dict = field(default_factory=dict)
    _day: object = None
    _prev_bar: object = None
    _open: dict = field(default_factory=dict)     # live round trip bookkeeping
    _touched: bool = False
    _day_r: float = 0.0
    _entries_blocked: Optional[str] = None
    _bars_in_day: int = 0

    _cur: dict = field(default_factory=dict)       # current bar's rec / ctx for the fill callback

    def __post_init__(self):
        if self.engine is None:
            self.engine = ExecutionEngine(self.instrument, self.capital_inr)
        self.engine.on_fill = self._on_fill

    # ------------------------------------------------------------------ day handling
    def _new_day(self, d):
        if self._day is not None:
            self._close_day()
        self._day = d; self._day_r = 0.0; self._entries_blocked = None; self._touched = False; self._bars_in_day = 0
        self.engine.positions = {}; self.engine.broker.positions = {}; self.engine.orders = {}; self.engine.halted = False; self.engine.halt_reason = None

    def _close_day(self):
        d = self._day.isoformat()
        self.daily[d] = {"bars": self._bars_in_day, "trades": [t.as_dict() for t in self.trades if t.date == d], "day_r": round(self._day_r, 4),
                         "entries_blocked": self._entries_blocked, "halted": self.engine.halted, "halt_reason": self.engine.halt_reason,
                         "orders_by_state": {s: sum(1 for o in self.engine.orders.values() if o.state == s) for s in {o.state for o in self.engine.orders.values()}},
                         "events": [e for e in self.events if e["date"] == d]}

    def _flatten(self, bar, why: str):
        pos = self.engine.positions.get(self.instrument, 0)
        if pos:
            self.engine.submit(Order(self.engine.new_client_id(), self.instrument, "SELL" if pos > 0 else "BUY", abs(pos), "MARKET", role="EXIT"), bar.timestamp, bar.close)
        self._entries_blocked = why
        self.events.append({"date": bar.timestamp.date().isoformat(), "at": bar.timestamp.isoformat(), "kind": why})

    # ------------------------------------------------------------------ bars
    def on_bar(self, bar) -> None:
        d = bar.timestamp.date()
        rec = self.anatomy.process(bar); ctx = self.context.process(bar, rec)
        if d != self._day:
            self._new_day(d); self._prev_bar = None
        self._bars_in_day += 1
        tradeable = window_of(d) in self.trade_windows
        # data-gap kill switch (a missing minute inside the session)
        if self._prev_bar is not None and bar.timestamp - self._prev_bar.timestamp > M1 and tradeable:
            self._flatten(bar, "DATA_GAP")
        self._prev_bar = bar
        if ctx.prev_close is not None and bar.low <= ctx.prev_close <= bar.high:
            self._touched = True
        # 1. fills for orders created before this bar (latency ≥ 1 bar); brackets placed at an entry fill act in the same bar via the callback
        self._cur = {"rec": rec, "ctx": ctx}
        fills = self.engine.on_bar(bar) if tradeable else []
        if self.engine.halted and tradeable and self._entries_blocked is None:
            self._entries_blocked = "ENGINE_HALTED"; self.events.append({"date": d.isoformat(), "at": bar.timestamp.isoformat(), "kind": "ENGINE_HALTED", "reason": self.engine.halt_reason})
        if not tradeable:
            return
        # 2. daily loss kill switch
        if self._day_r <= -abs(self.max_daily_loss_r) and self._entries_blocked is None:
            self._flatten(bar, "DAILY_LOSS_LIMIT")
        # 3. session end: flatten on the second-last bar so the EXIT fills at the last bar's open
        is_second_last = bar.timestamp.time().hour == 15 and bar.timestamp.time().minute == 28
        pos = self.engine.positions.get(self.instrument, 0)
        if is_second_last and pos:
            self._flatten(bar, "SESSION_END")
            return
        # 4. candidate intents
        if self._entries_blocked is None and pos == 0 and not is_second_last:
            st = {"bar": bar, "rec": rec, "ctx": ctx, "position": pos, "session_index": ctx.session_index, "session_touched_prev_close": self._touched}
            for it in self.candidate.on_bar(st):
                lot = self.engine.cfg(d).lot
                o = Order(self.engine.new_client_id(), self.instrument, it.side, lot, it.order_type, it.price, it.trigger, role=it.role)
                self.engine.submit(o, bar.timestamp, bar.close)
                self._open = {"intent_bar": ctx.session_index, "st": st, "candidate": getattr(self.candidate, "name", "candidate")}

    def _on_fill(self, f, bar):
        self._book(f, bar, self._cur["ctx"], self._cur["rec"])

    def _book(self, f, bar, ctx, rec):
        if f["role"] == "ENTRY":
            st = dict(self._open.get("st") or {}); st.update({"rec": rec, "ctx": ctx, "bar": bar})      # brackets use the ENTRY bar's reference range
            self._open.update({"entry": f["price"], "entry_bar": ctx.session_index, "side": f["side"], "qty": f["qty"], "fill_bar_ts": bar.timestamp})
            brackets = self.candidate.brackets(st, f["price"]) if st else []
            for it in brackets:
                o = Order(self.engine.new_client_id(), self.instrument, it.side, f["qty"], it.order_type, it.price, it.trigger, role=it.role)
                o.act_from = bar.timestamp.isoformat()                                                     # live within the entry bar, after the fill
                self.engine.submit(o, bar.timestamp, f["price"])
            self._open["stop"] = next((it.trigger for it in brackets if it.role == "STOP"), None)
            self._open["target"] = next((it.price for it in brackets if it.role == "TARGET"), None)
        else:
            o = self._open
            if "entry" not in o:
                return
            sgn = 1 if o["side"] == "BUY" else -1
            dist = abs(o["entry"] - o["stop"]) if o.get("stop") else None
            r = sgn * (f["price"] - o["entry"]) / dist if dist else 0.0
            cost = round_trip_cost_inr(bar.timestamp.date(), (o["entry"] + f["price"]) / 2, o["qty"])["total_inr"]
            gross = sgn * (f["price"] - o["entry"]) * o["qty"]
            self.trades.append(PaperTrade(bar.timestamp.date().isoformat(), o.get("candidate", ""), "UP" if sgn > 0 else "DOWN", o["entry_bar"], o["entry"], o.get("stop"), o.get("target"),
                                          ctx.session_index, f["price"], f["role"], round(r, 4), round(cost, 2), round(gross, 2), round(gross - cost, 2), o["entry_bar"] - o["intent_bar"]))
            self._day_r += r; self._open = {}

    def finish(self) -> dict:
        if self._day is not None:
            self._close_day()
        return self.report()

    # ------------------------------------------------------------------ report
    def report(self) -> dict:
        tr = self.trades; n = len(tr)
        by_role = {}
        for t in tr:
            by_role[t.exit_role] = by_role.get(t.exit_role, 0) + 1
        return {"instrument": self.instrument, "candidate": getattr(self.candidate, "name", "candidate"), "days": len(self.daily), "trades": n,
                "mean_r_gross": round(sum(t.r_gross for t in tr) / n, 4) if n else None, "gross_inr": round(sum(t.gross_inr for t in tr), 2), "cost_inr": round(sum(t.cost_inr for t in tr), 2),
                "net_inr": round(sum(t.net_inr for t in tr), 2), "exits": by_role, "mean_latency_bars": round(sum(t.latency_bars for t in tr) / n, 2) if n else None,
                "kill_switch_events": {k: sum(1 for e in self.events if e["kind"] == k) for k in {e["kind"] for e in self.events}},
                "slippage": self.engine.slippage.summary(), "note": "paper results are an upper bound (card 30); no capital; monitoring only"}
