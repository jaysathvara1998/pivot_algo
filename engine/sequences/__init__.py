"""Level 4 — Candle sequences (Skill 04).

Multi-bar readings built from Level 2 records and Level 3 labels, session-bounded, each event
stamped at the bar that confirms it (card 18), with explicit parameters. Studied with the
Level 3 null machinery. Nothing here is a signal.
"""
from .params import SequenceParams, DEFAULT_SEQUENCE_PARAMS
from .state import SequenceEngine, SequenceRecord, SeqEvent, replay_sequences, path_efficiency
from .study import streak_study, pullback_study, event_shares, labels_by_key

__all__ = ["SequenceParams", "DEFAULT_SEQUENCE_PARAMS", "SequenceEngine", "SequenceRecord", "SeqEvent",
           "replay_sequences", "path_efficiency", "streak_study", "pullback_study", "event_shares", "labels_by_key"]
