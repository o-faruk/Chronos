"""Cheap per-object orbital parameters for the API's catalog_snapshot view.

These come straight from the TLE mean motion, not from propagated state --
they're a single representative altitude/period for a scatter plot, not a
time-varying quantity.
"""
from __future__ import annotations

import math

from ingest.models import TrackedObject

MU_EARTH_KM3_S2 = 398600.4418
R_EARTH_KM = 6378.137


def mean_motion_rev_per_day(obj: TrackedObject) -> float:
    # TLE line 2, columns 53-63 (1-indexed): mean motion in revs/day.
    return float(obj.line2[52:63])


def eccentricity(obj: TrackedObject) -> float:
    # TLE line 2, columns 27-33 (1-indexed): decimal point is implied.
    return float("0." + obj.line2[26:33])


def period_min(obj: TrackedObject) -> float:
    return 1440.0 / mean_motion_rev_per_day(obj)


def semi_major_axis_km(obj: TrackedObject) -> float:
    n_rad_s = mean_motion_rev_per_day(obj) * 2 * math.pi / 86400.0
    return (MU_EARTH_KM3_S2 / n_rad_s ** 2) ** (1.0 / 3.0)


def altitude_km(obj: TrackedObject) -> float:
    """Circular-equivalent altitude (semi-major axis based), a single
    representative value for the scatter-plot view -- not perigee/apogee."""
    return semi_major_axis_km(obj) - R_EARTH_KM
