"""Stop width from the conditional distribution of forward range (PROJECT DEFINITION, Skill 18)."""
from __future__ import annotations

from collections import defaultdict


def context_key(vol: str, phase: str, expiry: bool, post_expansion: bool, post_violation: bool) -> tuple:
    return (vol, phase, "EXPIRY" if expiry else "NON_EXPIRY", "POST_EXPANSION" if post_expansion else "-", "POST_VIOLATION" if post_violation else "-")


class StopWidthTable:
    """For each context key: quantiles of the forward h-bar adverse-or-favourable range (max high − min low over the next h bars) in
    reference ranges of the signal bar. A stop placed at the q-quantile is touched by noise alone in at most (1 − q) of cases."""

    def __init__(self, horizon_bars: int = 3, quantiles=(0.5, 0.75, 0.9, 0.95)):
        self.h = horizon_bars; self.qs = quantiles
        self._rows = defaultdict(list)
        self.table = {}

    def add_stream(self, stream, session_recs, post_flags):
        """stream: Level 16 rows (candle, rec, labels, state, vol, session_index); session_recs: Level 14 SessionRecords aligned;
        post_flags(i) -> (post_expansion, post_violation) from the bar's labels."""
        n = len(stream)
        for i in range(n - self.h):
            b = stream[i]
            if any(stream[i + k]["session_index"] != b["session_index"] + k for k in range(1, self.h + 1)):
                continue
            ref = b["rec"].reference_range
            if not ref:
                continue
            hi = max(stream[i + k]["candle"].high for k in range(1, self.h + 1)); lo = min(stream[i + k]["candle"].low for k in range(1, self.h + 1))
            pe, pv = post_flags(b)
            s = session_recs[i]
            key = context_key(b["vol"], s.phase, bool(s.flags.get("nifty_weekly_expiry") or s.flags.get("sensex_weekly_expiry")), pe, pv)
            self._rows[key].append((hi - lo) / ref)
            self._rows[("ALL",)].append((hi - lo) / ref)
        return self

    def finalize(self, min_n: int = 100):
        self.table = {}
        for key, xs in self._rows.items():
            if len(xs) < min_n:
                continue
            s = sorted(xs)
            self.table[key] = {"n": len(s), **{f"q{int(q * 100)}": round(s[min(len(s) - 1, int(q * len(s)))], 3) for q in self.qs}}
        return self

    def stop_width(self, key: tuple, q: float = 0.9):
        row = self.table.get(key) or self.table.get(("ALL",))
        return row.get(f"q{int(q * 100)}") if row else None

    def as_dict(self) -> dict:
        return {"|".join(k): v for k, v in self.table.items()}
