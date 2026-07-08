"""One-object-vs-catalog screening for the custom-TLE-injection endpoint
(the headline differentiator from Phase 0 -- see docs/validation.md).

screen/coarse.py's regime-bucketed cKDTree pass is built for catalog-vs-
catalog (every object is a potential primary), which costs the same
O(bucket^2)-ish work whether the catalog has one extra object in it or not
-- appending a single object and calling run_screening() would still pay
the full ~3 minute catalog-wide coarse pass. That doesn't fit a synchronous
API Gateway request (hard 29s integration timeout, not configurable).

This module is the actually-cheap path: propagate the whole catalog once
(unavoidable -- need everyone's position to compare against), then check
one target's distance to every other object per time sample with a single
vectorized subtraction (O(catalog_size) per sample, not a spatial index
query), instead of a full pairwise pass.
"""
from __future__ import annotations

import time
from datetime import timedelta

import numpy as np

from ingest.models import TrackedObject
from propagate.batch import propagate_catalog
from propagate.time_grid import time_grid
from screen.coarse import DEFAULT_COARSE_RADIUS_KM, DEFAULT_COARSE_STEP_SECONDS
from screen.pipeline import Conjunction, ScreeningRun, _orbital_elements
from screen.regime import classify_regime
from screen.severity import classify_severity
from screen.tca import refine_tca


def run_targeted_screening(
    target: TrackedObject,
    catalog: list[TrackedObject],
    window_start,
    window_hours: float,
    coarse_step_seconds: float = DEFAULT_COARSE_STEP_SECONDS,
    coarse_radius_km: float = DEFAULT_COARSE_RADIUS_KM,
) -> ScreeningRun:
    t0 = time.perf_counter()
    jd, fr = time_grid(window_start, window_hours, coarse_step_seconds)

    target_result = propagate_catalog([target], jd, fr)
    catalog_result = propagate_catalog(catalog, jd, fr)

    target_r = target_result.r[0]  # (T, 3)
    target_err = target_result.err[0]  # (T,)

    flagged: dict[int, list[int]] = {}
    for t in range(len(jd)):
        if target_err[t] != 0:
            continue
        valid = catalog_result.err[:, t] == 0
        deltas = catalog_result.r[:, t, :] - target_r[t]
        dist = np.linalg.norm(deltas, axis=-1)
        close = np.nonzero(valid & (dist <= coarse_radius_km))[0]
        for idx in close:
            flagged.setdefault(int(idx), []).append(t)

    def jd_fr_to_dt(t_idx: int):
        return window_start + timedelta(seconds=t_idx * coarse_step_seconds)

    conjunctions = []
    for seq, (catalog_idx, time_indices) in enumerate(flagged.items()):
        other = catalog[catalog_idx]
        if _orbital_elements(target) == _orbital_elements(other):
            continue

        pad = coarse_step_seconds
        search_start = jd_fr_to_dt(min(time_indices)) - timedelta(seconds=pad)
        search_end = jd_fr_to_dt(max(time_indices)) + timedelta(seconds=pad)

        result = refine_tca(target, other, search_start, search_end)
        miss_m = result.miss_distance_km * 1000.0
        severity = classify_severity(miss_m)
        if severity is None:
            continue

        conjunctions.append(Conjunction(
            id=f"CHRONOS-CUSTOM-{window_start.year}-{seq + 1:06d}",
            object_a=target,
            object_b=other,
            tca=result.tca,
            miss_distance_m=miss_m,
            relative_velocity_kms=result.relative_velocity_km_s,
            severity=severity,
            approach_angle_deg=result.approach_angle_deg,
            altitude_km=result.altitude_km,
            regime=classify_regime(target),
        ))

    conjunctions.sort(key=lambda c: c.miss_distance_m)
    duration_ms = (time.perf_counter() - t0) * 1000.0

    return ScreeningRun(
        started_at=window_start,
        catalog_size=len(catalog) + 1,
        pairs_screened=len(catalog),
        screening_window_hours=window_hours,
        duration_ms=duration_ms,
        conjunctions=conjunctions,
        candidates_screened=len(flagged),
    )
