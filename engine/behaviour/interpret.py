"""interpret(record) -> ordered interpretations with their justifying observations (P9)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Optional

from engine.anatomy import AnatomyRecord
from .vocabulary import BehaviourParams, DEFAULT_BEHAVIOUR_PARAMS, Label


@dataclass
class Interpretation:
    label: str
    observations: list                       # ordered Level 2 facts that justify the label
    reading: str                             # one neutral sentence about what price did
    evidence_status: str = "UNTESTED"        # filled from the measured register when available
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


def _shape_obs(s: dict, p_anat: dict | None = None) -> dict:
    return {
        "direction": f"direction {s['direction']}",
        "body": f"body class {s['body_class']} (body {s['body_to_range']:.3f} of range)" if s["body_to_range"] is not None else "body undefined (zero range)",
        "zone": f"close zone {s['close_zone']} (close at {s['close_location']:.3f} of range from the low)" if s["close_location"] is not None else "close zone undefined",
        "wick": f"wick profile {s['wick_profile']} (upper {s['upper_wick_to_range']:.3f}, lower {s['lower_wick_to_range']:.3f} of range)" if s["upper_wick_to_range"] is not None else "wicks undefined",
    }


def interpret(rec: AnatomyRecord, p: BehaviourParams = DEFAULT_BEHAVIOUR_PARAMS,
              register: Optional[dict] = None) -> list[Interpretation]:
    """Return the interpretations that apply, in a fixed order. Shape and size labels are
    independent, so a candle can carry one shape label and one size label; otherwise UNCLASSIFIED."""
    s = rec.shape
    out: list[Interpretation] = []
    pd = p.as_dict()
    if s["range"] == 0 or rec.static_window:
        return [Interpretation(Label.UNCLASSIFIED.value, ["range is zero or candle is inside the static window"],
                               "No shape or size reading is defined for this candle.", "NOT_APPLICABLE", pd)]
    o = _shape_obs(s)

    # --- shape labels (mutually exclusive by construction: retained needs a LARGE/FULL body, returned needs a
    #     dominant wick which caps body at 50 %, unresolved needs a NEGLIGIBLE/SMALL body and no dominant wick)
    if s["body_class"] in p.retained_body_classes and s["direction"] == "BULLISH" and s["close_zone"] == "HIGH":
        out.append(Interpretation(Label.DIRECTIONAL_RETAINED_UP.value, [o["direction"], o["body"], o["zone"]],
                                  "Price moved up through the candle and the move was retained to the close.", params=pd))
    elif s["body_class"] in p.retained_body_classes and s["direction"] == "BEARISH" and s["close_zone"] == "LOW":
        out.append(Interpretation(Label.DIRECTIONAL_RETAINED_DOWN.value, [o["direction"], o["body"], o["zone"]],
                                  "Price moved down through the candle and the move was retained to the close.", params=pd))
    elif s["wick_profile"] == p.excursion_wick_profile_upper and s["close_zone"] in p.excursion_close_zones_upper:
        out.append(Interpretation(Label.UPPER_EXCURSION_RETURNED.value, [o["wick"], o["zone"], o["body"]],
                                  "Price travelled above the open-close pair for most of the range and returned to close in the lower two fifths.", params=pd))
    elif s["wick_profile"] == "LOWER_DOMINANT" and s["close_zone"] in ("HIGH", "UPPER"):
        out.append(Interpretation(Label.LOWER_EXCURSION_RETURNED.value, [o["wick"], o["zone"], o["body"]],
                                  "Price travelled below the open-close pair for most of the range and returned to close in the upper two fifths.", params=pd))
    elif s["body_class"] in p.unresolved_body_classes and s["wick_profile"] not in ("UPPER_DOMINANT", "LOWER_DOMINANT"):
        out.append(Interpretation(Label.TWO_SIDED_UNRESOLVED.value, [o["body"], o["wick"], o["zone"]],
                                  "Price travelled both ways and closed near where it opened; neither side of the range was retained.", params=pd))

    # --- size labels (need a relative size and a local ratio; first bar never qualifies: local is undefined)
    if rec.local_relative_range is not None and rec.size_class in p.expansion_size_classes and rec.local_relative_range >= p.expansion_local_min:
        out.append(Interpretation(Label.RANGE_EXPANSION.value,
                                  [f"size class {rec.size_class} ({rec.relative_range:.2f}x the same-minute median of {rec.reference_sessions} sessions)",
                                   f"local relative range {rec.local_relative_range:.2f}x the previous bars of this session (threshold {p.expansion_local_min})"],
                                  "The candle's range is large both for this clock minute and for this session so far.", params=pd))
    elif rec.local_relative_range is not None and rec.size_class in p.compression_size_classes and rec.local_relative_range <= p.compression_local_max:
        out.append(Interpretation(Label.RANGE_COMPRESSION.value,
                                  [f"size class {rec.size_class} ({rec.relative_range:.2f}x the same-minute median of {rec.reference_sessions} sessions)",
                                   f"local relative range {rec.local_relative_range:.2f}x the previous bars of this session (threshold {p.compression_local_max})"],
                                  "The candle's range is small both for this clock minute and for this session so far.", params=pd))

    if not out:
        out.append(Interpretation(Label.UNCLASSIFIED.value, [o["body"], o["wick"], o["zone"], f"size class {rec.size_class}"],
                                  "The candle matches none of the defined readings.", "NOT_APPLICABLE", pd))
    if register:
        for it in out:
            if it.label in register:
                it.evidence_status = register[it.label]
    return out


def explain(its: list[Interpretation]) -> list[str]:
    """P9 format: 'LABEL because: (1) ... (2) ... — reading. Evidence: STATUS'."""
    lines = []
    for it in its:
        obs = " ".join(f"({i + 1}) {o}" for i, o in enumerate(it.observations))
        lines.append(f"{it.label} because: {obs} — {it.reading} Evidence: {it.evidence_status}.")
    return lines
