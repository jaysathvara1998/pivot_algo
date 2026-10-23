"""Scheduled events with provenance — ported from Foundations topic 19 (events.py). RBI MPC dates before 2026 are MODEL MEMORY,
UNVERIFIED, and are flagged as such; never treat them as facts without checking rbi.org.in."""
from datetime import date

EVENTS = [
    (date(2024, 2, 1), "BUDGET", "IN_SESSION", "SOURCE FACT (interim Budget 2024)"),
    (date(2024, 7, 23), "BUDGET", "IN_SESSION", "SOURCE FACT (full Budget 2024-25 after the election)"),
    (date(2025, 2, 1), "BUDGET", "IN_SESSION", "SOURCE FACT (Saturday special session, NSE/CMTR/65729)"),
    (date(2026, 2, 1), "BUDGET", "IN_SESSION", "SOURCE FACT (Sunday special session)"),
    (date(2024, 6, 4), "ELECTION_RESULT", "IN_SESSION", "SOURCE FACT (Lok Sabha counting day)"),
    (date(2024, 6, 3), "EXIT_POLL_GAP", "OVERNIGHT", "SOURCE FACT (exit polls 2024-06-01)"),
    (date(2026, 4, 8), "RBI_MPC", "IN_SESSION", "SOURCE FACT (News On AIR)"),
    (date(2026, 2, 27), "MSCI_EFFECTIVE", "AT_CLOSE", "SOURCE FACT"),
    (date(2026, 5, 29), "MSCI_EFFECTIVE", "AT_CLOSE", "SOURCE FACT"),
    (date(2026, 8, 31), "MSCI_EFFECTIVE", "AT_CLOSE", "SOURCE FACT"),
] + [(d, "RBI_MPC", "IN_SESSION", "MODEL MEMORY — UNVERIFIED") for d in (
    date(2023, 10, 6), date(2023, 12, 8), date(2024, 2, 8), date(2024, 4, 5), date(2024, 6, 7), date(2024, 8, 8), date(2024, 10, 9),
    date(2024, 12, 6), date(2025, 2, 7), date(2025, 4, 9), date(2025, 6, 6), date(2025, 8, 6), date(2025, 10, 1), date(2025, 12, 5))]


def events_on(d: date) -> list:
    return [{"kind": k, "when": w, "provenance": p} for (dd, k, w, p) in EVENTS if dd == d]
