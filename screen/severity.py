"""Severity enum + thresholds. Sourced from the mockup UI's already-shipped
severity bars (Chronos Console.dc.html: sev()/sevColor()), not invented here --
treat this as the locked contract, not a tunable.
"""
from __future__ import annotations

# Miss distance in meters, upper bound exclusive per band, ordered tightest-first.
THRESHOLDS_M = (
    ("critical", 200.0),
    ("high", 500.0),
    ("medium", 1000.0),
    ("low", 5000.0),
)


def classify_severity(miss_distance_m: float) -> str | None:
    """Returns None if the miss distance is beyond the "low" band (5 km) --
    i.e. not worth reporting as a conjunction at all, matching SOCRATES' own
    5 km screening cutoff (see docs/validation.md)."""
    for label, upper_bound in THRESHOLDS_M:
        if miss_distance_m < upper_bound:
            return label
    return None
