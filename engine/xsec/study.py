"""Pre-registered cross-sectional study (Skill 31)."""
from __future__ import annotations

from datetime import date

from engine.daily.split import HALVES
from engine.setups import bonferroni_z
from .panel import calendar, load_panel
from .portfolio import cell, basket_backtest, rebalance_dates, clear_cache
from .signals import SIGNALS, HORIZON

XS_SPLIT = {"DEVELOPMENT": (date(2006, 1, 2), date(2015, 12, 31)), "VALIDATION": (date(2016, 1, 1), date(2019, 12, 31)), "HOLDOUT": (date(2020, 1, 1), date(2026, 9, 30))}


def window_indices(cal, window: str):
    a, b = XS_SPLIT[window]
    idx = [i for i, d in enumerate(cal) if a <= d <= b]
    return idx[0], idx[-1]


def xsec_study(panel, cal, window: str = "DEVELOPMENT", n_perm: int = 200, basket_sizes=(10, 5)) -> dict:
    clear_cache(); a, b = window_indices(cal, window); start = max(a, 260)
    cells = {}; fam = 16; zcrit = bonferroni_z(fam, 0.05)
    for sig in SIGNALS:
        dates = rebalance_dates(cal, start, b, HORIZON[sig])
        cells[f"{sig}|close"] = cell(panel, cal, dates, sig, "close", HALVES, n_perm)
        if HORIZON[sig] == 5:
            cells[f"{sig}|open"] = cell(panel, cal, dates, sig, "open", HALVES, n_perm)
    supported = [k for k, c in cells.items() if c["status"] == "SUPPORTED_DESCRIPTIVE"]
    baskets = {}
    for k in supported:
        sig = k.split("|")[0]
        for n in basket_sizes:
            baskets[f"{sig}|N{n}"] = basket_backtest(panel, cal, rebalance_dates(cal, start, b, HORIZON[sig]), sig, n)
    bench = basket_backtest(panel, cal, rebalance_dates(cal, start, b, 21), "MOM_12_1", 10) if False else None
    out = {"window": window, "stocks_in_panel": len(panel), "sessions": b - start + 1, "family": fam, "bonferroni_z": round(zcrit, 3), "cells": cells, "supported": supported, "baskets": baskets}
    out["count"] = {s: sum(1 for c in cells.values() if c["status"] == s) for s in {c["status"] for c in cells.values()}}
    return out
