"""Session-bounded sequence state with confirmation-stamped events (PROJECT DEFINITION, Skill 04)."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Iterable, Iterator, Optional

from engine.ohlc import Candle, session_date
from engine.anatomy import AnatomyRecord
from engine.behaviour import interpret
from .params import SequenceParams, DEFAULT_SEQUENCE_PARAMS


@dataclass
class SeqEvent:
    label: str
    confirmed_at: str                     # ISO timestamp of the confirming bar
    observations: list
    params: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return asdict(self)


@dataclass
class SequenceRecord:
    timestamp: str
    session_index: int
    run_direction: Optional[str]          # BULLISH | BEARISH | None (FLAT or first bar of a run resets)
    run_length: int
    run_efficiency: Optional[float]       # |close_n - open_1| / sum(range_i) over the run, None if sum 0
    run_strength: Optional[float]         # |close_n - open_1| / sum(reference_range_i), None if any reference missing
    rel_range_trend: Optional[int]        # +1 rising k bars, -1 falling k bars, 0 neither, None insufficient
    impulse_state: str                    # NONE | IMPULSE_ACTIVE | PULLBACK
    impulse_direction: Optional[str]
    pullback_depth: Optional[float]
    pullback_bars: int
    events: list                          # SeqEvent confirmed at this bar

    def as_dict(self) -> dict:
        d = asdict(self)
        return d


def path_efficiency(bars: list[Candle]) -> Optional[float]:
    tot = sum(c.high - c.low for c in bars)
    return abs(bars[-1].close - bars[0].open) / tot if tot > 0 else None


class SequenceEngine:
    def __init__(self, p: SequenceParams = DEFAULT_SEQUENCE_PARAMS):
        self.p = p
        self._reset_session(None)

    def _reset_session(self, d) -> None:
        self._session = d
        self._idx = -1
        self._run: list[tuple[Candle, AnatomyRecord]] = []
        self._run_dir: Optional[str] = None
        self._rel_ranges: list[float] = []            # relative ranges of this session's bars with a reference
        self._labels3: list[list[str]] = []           # Level 3 labels per bar this session
        self._imp_state = "NONE"
        self._imp_dir: Optional[str] = None
        self._imp_origin: Optional[float] = None
        self._imp_extreme: Optional[float] = None
        self._pb_bars = 0
        self._pb_depth: Optional[float] = None

    # ---------------------------------------------------------------------------------------
    def process(self, c: Candle, rec: AnatomyRecord) -> SequenceRecord:
        d = session_date(c.timestamp)
        if d != self._session:
            self._reset_session(d)
        self._idx += 1
        p = self.p
        ts = c.timestamp.isoformat()
        events: list[SeqEvent] = []
        direction = rec.shape["direction"]
        pd = p.as_dict()

        # --- directional run ---------------------------------------------------------------
        if direction == "FLAT" or direction != self._run_dir:
            self._run = []
            self._run_dir = direction if direction != "FLAT" else None
        if direction != "FLAT":
            self._run.append((c, rec))
        n = len(self._run)
        eff = path_efficiency([b for b, _ in self._run]) if n else None
        refs = [r.reference_range for _, r in self._run]
        strength = None
        if n and all(x is not None and x > 0 for x in refs):
            strength = abs(self._run[-1][0].close - self._run[0][0].open) / sum(refs)
        if n >= p.min_run:
            k = min(n, p.max_run_label)
            tag = f"{k}+" if n >= p.max_run_label else str(k)
            events.append(SeqEvent(f"RUN_{'UP' if direction == 'BULLISH' else 'DOWN'}_{tag}", ts,
                                   [f"{n} consecutive {direction} closes", f"path efficiency {eff:.3f}" if eff is not None else "path efficiency undefined",
                                    f"strength {strength:.2f} normal ranges" if strength is not None else "strength undefined (reference missing)"], pd))
            # body momentum inside the run
            if n >= p.k_body:
                bodies = [r.relative_body for _, r in self._run[-p.k_body:]]
                if all(b is not None for b in bodies):
                    if all(bodies[i] < bodies[i + 1] for i in range(len(bodies) - 1)):
                        events.append(SeqEvent(f"MOMENTUM_BUILDING_{'UP' if direction == 'BULLISH' else 'DOWN'}", ts,
                                               [f"relative bodies rising over {p.k_body} bars: " + ", ".join(f"{b:.2f}" for b in bodies)], pd))
                    elif all(bodies[i] > bodies[i + 1] for i in range(len(bodies) - 1)):
                        events.append(SeqEvent(f"MOMENTUM_FADING_{'UP' if direction == 'BULLISH' else 'DOWN'}", ts,
                                               [f"relative bodies falling over {p.k_body} bars: " + ", ".join(f"{b:.2f}" for b in bodies)], pd))

        # --- range trend (deseasonalised) ------------------------------------------------------
        trend = None
        if rec.relative_range is not None and not rec.first_bar and not rec.static_window:
            self._rel_ranges.append(rec.relative_range)
        else:
            self._rel_ranges.append(float("nan"))
        if len(self._rel_ranges) >= p.k_range:
            w = self._rel_ranges[-p.k_range:]
            if all(x == x for x in w):              # no NaN
                if all(w[i] < w[i + 1] for i in range(len(w) - 1)):
                    trend = 1
                    events.append(SeqEvent("EXPANSION_SEQUENCE", ts, [f"relative ranges rising over {p.k_range} bars: " + ", ".join(f"{x:.2f}" for x in w)], pd))
                elif all(w[i] > w[i + 1] for i in range(len(w) - 1)):
                    trend = -1
                    events.append(SeqEvent("COMPRESSION_SEQUENCE", ts, [f"relative ranges falling over {p.k_range} bars: " + ", ".join(f"{x:.2f}" for x in w)], pd))
                else:
                    trend = 0

        # --- repeated excursions (Level 3 labels) ---------------------------------------------
        labs = [i.label for i in interpret(rec)]
        self._labels3.append(labs)
        if len(self._labels3) >= p.k_excursion:
            w = self._labels3[-p.k_excursion:]
            for side, lab in (("UPPER", "UPPER_EXCURSION_RETURNED"), ("LOWER", "LOWER_EXCURSION_RETURNED")):
                if all(lab in x for x in w):
                    events.append(SeqEvent(f"REPEATED_{side}_EXCURSION", ts, [f"{p.k_excursion} consecutive bars labelled {lab}"], pd))

        # --- impulse / pullback machine --------------------------------------------------------
        up = self._imp_dir == "UP"
        if self._imp_state == "NONE":
            if n >= p.min_run and strength is not None and strength >= p.impulse_min_strength:
                self._imp_state = "IMPULSE_ACTIVE"
                self._imp_dir = "UP" if direction == "BULLISH" else "DOWN"
                self._imp_origin = self._run[0][0].open
                self._imp_extreme = max(b.high for b, _ in self._run) if self._imp_dir == "UP" else min(b.low for b, _ in self._run)
                events.append(SeqEvent(f"IMPULSE_{self._imp_dir}", ts, [f"{n}-bar run", f"strength {strength:.2f} >= {p.impulse_min_strength}",
                                                                          f"origin {self._imp_origin}, extreme {self._imp_extreme}"], pd))
        elif self._imp_state == "IMPULSE_ACTIVE":
            same = (direction == "BULLISH") if up else (direction == "BEARISH")
            if same:
                self._imp_extreme = max(self._imp_extreme, c.high) if up else min(self._imp_extreme, c.low)
            elif direction != "FLAT":
                self._imp_state = "PULLBACK"; self._pb_bars = 1
                self._pb_depth = self._depth(c.close)
                events.append(SeqEvent(f"PULLBACK_START_{self._imp_dir}", ts, [f"first close against the impulse", f"depth {self._pb_depth:.2f} of the impulse move"], pd))
                self._resolve(c, ts, events)
        elif self._imp_state == "PULLBACK":
            self._pb_bars += 1
            self._pb_depth = self._depth(c.close)
            self._resolve(c, ts, events)

        return SequenceRecord(ts, self._idx, self._run_dir if n else None, n, eff, strength, trend, self._imp_state, self._imp_dir,
                              self._pb_depth if self._imp_state == "PULLBACK" else None, self._pb_bars if self._imp_state == "PULLBACK" else 0, events)

    def _depth(self, close: float) -> float:
        move = self._imp_extreme - self._imp_origin
        if move == 0:
            return 1.0
        return (self._imp_extreme - close) / move          # same sign convention for UP and DOWN

    def _resolve(self, c: Candle, ts: str, events: list) -> None:
        up = self._imp_dir == "UP"
        beyond = c.close > self._imp_extreme if up else c.close < self._imp_extreme
        pd = self.p.as_dict()
        if beyond:
            events.append(SeqEvent(f"CONTINUATION_{self._imp_dir}", ts, [f"close beyond the impulse extreme {self._imp_extreme} after {self._pb_bars} pullback bars"], pd))
            self._clear_impulse()
        elif self._pb_depth >= self.p.pullback_max_depth:
            events.append(SeqEvent(f"FAILED_MOVEMENT_{self._imp_dir}", ts, [f"retracement {self._pb_depth:.2f} >= {self.p.pullback_max_depth} of the impulse move after {self._pb_bars} bars"], pd))
            self._clear_impulse()
        elif self._pb_bars >= self.p.pullback_max_bars:
            events.append(SeqEvent(f"PULLBACK_EXPIRED_{self._imp_dir}", ts, [f"{self._pb_bars} pullback bars without resolution; depth {self._pb_depth:.2f}"], pd))
            self._clear_impulse()

    def _clear_impulse(self) -> None:
        self._imp_state = "NONE"; self._imp_dir = None; self._imp_origin = None; self._imp_extreme = None
        self._pb_bars = 0; self._pb_depth = None


def replay_sequences(items: Iterable[tuple[Candle, AnatomyRecord]], p: SequenceParams = DEFAULT_SEQUENCE_PARAMS) -> Iterator[tuple[int, Candle, SequenceRecord]]:
    eng = SequenceEngine(p)
    for i, (c, r) in enumerate(items):
        yield i, c, eng.process(c, r)
