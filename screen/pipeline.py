"""Orchestrates the full screening run: coarse candidate pairs -> fine TCA
refinement -> severity classification -> ranked conjunction list.

Output shape matches the locked API schema (see docs/validation.md /
Chronos Orbital Conjunction System/Chronos Console.dc.html for the frontend
contract this was built against).
"""
from __future__ import annotations

import time
from dataclasses import dataclass
from datetime import datetime, timedelta

from ingest.models import TrackedObject
from screen.coarse import DEFAULT_COARSE_RADIUS_KM, DEFAULT_COARSE_STEP_SECONDS, coarse_candidate_pairs
from screen.regime import classify_regime
from screen.severity import classify_severity
from screen.tca import refine_tca

# Docked spacecraft (e.g. ISS + everything berthed to it) are published by
# CelesTrak/Space-Track under separate NORAD IDs that share the *exact same*
# orbital element set, since the docked vehicle isn't independently tracked --
# found by hand-checking a real sample run against CelesTrak's `stations`
# group, where several docked pairs came back at exactly 0.000 m / 0.000 km/s.
# That's a shared-tracking artifact, not a conjunction, so pairs with
# identical elements are skipped before fine screening even runs. This is
# deliberately narrower than a relative-velocity threshold: a genuinely
# close, independently-orbiting pair can have a very small but nonzero
# relative velocity (see testdata/known_conjunction.md) and that case must
# still be reported.


@dataclass(frozen=True)
class Conjunction:
    id: str
    object_a: TrackedObject
    object_b: TrackedObject
    tca: datetime
    miss_distance_m: float
    relative_velocity_kms: float
    severity: str
    approach_angle_deg: float
    altitude_km: float
    regime: str


@dataclass(frozen=True)
class ScreeningRun:
    started_at: datetime
    catalog_size: int
    pairs_screened: int
    screening_window_hours: float
    duration_ms: float
    conjunctions: list[Conjunction]
    candidates_screened: int = 0


def _orbital_elements(obj: TrackedObject) -> str:
    """Inclination through mean motion (line2 cols 9-63), excluding the NORAD
    ID and the revolution-number/checksum trailer -- those two objects can
    differ on while still sharing the same underlying element set (see the
    MENGTIAN/TIANZHOU-10 example in docs/decisions.md)."""
    return obj.line2[8:63]


def run_screening(
    catalog: list[TrackedObject],
    window_start: datetime,
    window_hours: float = 72.0,
    coarse_step_seconds: float = DEFAULT_COARSE_STEP_SECONDS,
    coarse_radius_km: float = DEFAULT_COARSE_RADIUS_KM,
) -> ScreeningRun:
    t0 = time.perf_counter()

    candidates = coarse_candidate_pairs(
        catalog, window_start, window_hours, coarse_step_seconds, coarse_radius_km,
    )

    conjunctions = []
    for seq, cand in enumerate(candidates):
        obj_a, obj_b = catalog[cand.i], catalog[cand.j]
        if obj_a.object_type != "active" and obj_b.object_type != "active":
            continue  # active-vs-catalog only, matching SOCRATES' scope -- see docs/validation.md
        if _orbital_elements(obj_a) == _orbital_elements(obj_b):
            continue  # shared element set (docked/berthed), not an independent conjunction

        pad = coarse_step_seconds
        search_start = cand.first_flagged_time - timedelta(seconds=pad)
        search_end = cand.last_flagged_time
        search_end += timedelta(seconds=pad)

        result = refine_tca(obj_a, obj_b, search_start, search_end)
        miss_m = result.miss_distance_km * 1000.0
        severity = classify_severity(miss_m)
        if severity is None:
            continue  # coarse radius is generous; fine TCA can land beyond the 5km cutoff

        conjunctions.append(Conjunction(
            id=f"CHRONOS-{window_start.year}-{seq + 1:06d}",
            object_a=obj_a,
            object_b=obj_b,
            tca=result.tca,
            miss_distance_m=miss_m,
            relative_velocity_kms=result.relative_velocity_km_s,
            severity=severity,
            approach_angle_deg=result.approach_angle_deg,
            altitude_km=result.altitude_km,
            regime=classify_regime(obj_a),
        ))

    conjunctions.sort(key=lambda c: c.miss_distance_m)
    duration_ms = (time.perf_counter() - t0) * 1000.0
    n = len(catalog)

    return ScreeningRun(
        started_at=window_start,
        catalog_size=n,
        pairs_screened=n * (n - 1) // 2,
        screening_window_hours=window_hours,
        duration_ms=duration_ms,
        conjunctions=conjunctions,
        candidates_screened=len(candidates),
    )
