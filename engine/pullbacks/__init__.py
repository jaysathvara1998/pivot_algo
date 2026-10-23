"""Level 11 — Pullbacks and continuation (Skill 11).

Within a Level 10 TRENDING state: the impulse leg runs from the last confirmed swing (HL in an up trend) to the running
extreme; a pullback starts when the close retreats at least d reference ranges from that extreme; its depth is the share
of the impulse retraced (SHALLOW < 0.382, NORMAL < 0.618, DEEP <= 1.0, BROKEN > 1.0 = structure violated); it resolves as
CONTINUATION (close beyond the impulse extreme), FAILED_CONTINUATION (close beyond the last confirmed swing, i.e. structure
broken) or EXPIRED (h bars). Confirmation stamps throughout. No signals.
"""
from .params import PullbackParams, DEFAULT_PB_PARAMS, DEPTH_CLASSES
from .machine import PullbackEngine, PullbackRecord, PullbackEvent, replay_pullbacks, depth_class
from .study import pb_labels_by_key, pb_resolution, pb_study

__all__ = ["PullbackParams", "DEFAULT_PB_PARAMS", "DEPTH_CLASSES", "PullbackEngine", "PullbackRecord", "PullbackEvent",
           "replay_pullbacks", "depth_class", "pb_labels_by_key", "pb_resolution", "pb_study"]
