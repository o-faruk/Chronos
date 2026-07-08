"""Orbital regime bucketing -- matches the frontend's LEO/MEO/GEO/HEO rows
and is the coarse pre-filter's grouping key (screen/coarse.py).

Thresholds are the conventional ones (period-based for LEO/GEO/MEO,
eccentricity-based for HEO overriding altitude) -- not a precise physical
boundary, just enough to bucket the catalog the way the mockup UI already
displays it.
"""
from __future__ import annotations

from ingest.models import TrackedObject
from propagate.orbit_params import eccentricity, period_min

REGIMES = ("LEO", "MEO", "GEO", "HEO")

_HEO_ECCENTRICITY_THRESHOLD = 0.25
_LEO_PERIOD_MAX_MIN = 128.0
_GEO_PERIOD_MIN_MIN = 1430.0
_GEO_PERIOD_MAX_MIN = 1450.0


def classify_regime(obj: TrackedObject) -> str:
    if eccentricity(obj) > _HEO_ECCENTRICITY_THRESHOLD:
        return "HEO"
    p = period_min(obj)
    if p < _LEO_PERIOD_MAX_MIN:
        return "LEO"
    if _GEO_PERIOD_MIN_MIN <= p <= _GEO_PERIOD_MAX_MIN:
        return "GEO"
    return "MEO"
