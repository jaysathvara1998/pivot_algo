"""Level 15 — Market context (Skill 15).

What the engine knows about the day: at the first bar's close — the overnight gap (percent, reference ranges, class and sign),
the previous session's range and close location, where the open sits against the previous range, regime and expiry flags
(Level 14), scheduled events with provenance (topic 19); during the session — whether and when the previous close was
revisited, the first-hour return, the session range so far. The study is per session against a volatility-matched random
walk that carries the real gaps. No signals.
"""
from .params import ContextParams, DEFAULT_CTX_PARAMS, GAP_CLASSES
from .events_data import EVENTS, events_on
from .engine import ContextEngine, ContextRecord, replay_context, gap_class
from .study import session_table, gap_null_table, context_study

__all__ = ["ContextParams", "DEFAULT_CTX_PARAMS", "GAP_CLASSES", "EVENTS", "events_on", "ContextEngine", "ContextRecord", "replay_context",
           "gap_class", "session_table", "gap_null_table", "context_study"]
