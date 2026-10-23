"""Level 23 — execution layer: regime-dated config and costs, order-state machine, prechecks, conservative paper fills, simulated broker,
reconciliation with halt, slippage log. Infrastructure only: no strategy is deployed."""
from .config import ExecConfig, exec_config, round_trip_cost_inr, stt_sell_rate, REGIME_DATES
from .orders import Order, IllegalTransition, STATES, TRANSITIONS
from .precheck import precheck
from .fills import fill_on_bar
from .broker_sim import SimBroker, BrokerOutage
from .reconcile import reconcile, SlippageLog
from .engine import ExecutionEngine

__all__ = ["ExecConfig", "exec_config", "round_trip_cost_inr", "stt_sell_rate", "REGIME_DATES", "Order", "IllegalTransition", "STATES", "TRANSITIONS",
           "precheck", "fill_on_bar", "SimBroker", "BrokerOutage", "reconcile", "SlippageLog", "ExecutionEngine"]
