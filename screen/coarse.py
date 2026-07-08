"""Coarse spatial pass: regime-bucket the catalog, then use cKDTree.query_pairs
per coarse time sample to find candidate pairs -- never enumerate all N^2 pairs.

See docs/decisions.md for why this shape (regime bucket + per-timestep KD-tree,
not a pure orbital-shell sweep) was chosen, and for the honest bound on what
snapshot-based coarse screening can miss.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np
from scipy.spatial import cKDTree

from ingest.models import TrackedObject
from propagate.batch import propagate_catalog
from propagate.time_grid import time_grid
from screen.regime import classify_regime

DEFAULT_COARSE_STEP_SECONDS = 60.0
# 6x the 5km "low" severity cutoff. Measured against the real ~32k-object
# catalog (see docs/decisions.md): 75km produced 1.6M candidates and an
# ~12 min fine-screening stage for a 24h window alone (would blow Lambda's
# 15 min limit at 72h); 30km cuts that to ~190k candidates / ~1.4 min while
# keeping a comfortable buffer over the reporting threshold.
DEFAULT_COARSE_RADIUS_KM = 30.0


@dataclass(frozen=True)
class CandidatePair:
    i: int  # index into the full catalog passed to coarse_candidate_pairs
    j: int
    first_flagged_time: datetime
    last_flagged_time: datetime


def _regime_groups(catalog: list[TrackedObject]) -> dict[str, list[int]]:
    regimes = [classify_regime(o) for o in catalog]
    heo_idx = [i for i, r in enumerate(regimes) if r == "HEO"]
    groups: dict[str, list[int]] = {}
    for regime in ("LEO", "MEO", "GEO"):
        own = [i for i, r in enumerate(regimes) if r == regime]
        # HEO objects can dip through any altitude band near perigee, so they
        # ride along in every group rather than only being checked HEO-vs-HEO.
        groups[regime] = sorted(set(own) | set(heo_idx))
    return groups


def coarse_candidate_pairs(
    catalog: list[TrackedObject],
    window_start: datetime,
    window_hours: float,
    coarse_step_seconds: float = DEFAULT_COARSE_STEP_SECONDS,
    radius_km: float = DEFAULT_COARSE_RADIUS_KM,
) -> list[CandidatePair]:
    jd, fr = time_grid(window_start, window_hours, coarse_step_seconds)
    times = [datetime.fromtimestamp((jd[t] - 2440587.5 + fr[t]) * 86400.0, tz=window_start.tzinfo)
             for t in range(len(jd))]

    flagged: dict[tuple[int, int], list[int]] = {}

    for group_indices in _regime_groups(catalog).values():
        if len(group_indices) < 2:
            continue
        group_catalog = [catalog[i] for i in group_indices]
        result = propagate_catalog(group_catalog, jd, fr)

        for t in range(len(jd)):
            valid = result.err[:, t] == 0
            positions = result.r[:, t, :]
            valid_local_idx = np.nonzero(valid)[0]
            if len(valid_local_idx) < 2:
                continue
            tree = cKDTree(positions[valid_local_idx])
            for a, b in tree.query_pairs(r=radius_km):
                gi = group_indices[valid_local_idx[a]]
                gj = group_indices[valid_local_idx[b]]
                key = (gi, gj) if gi < gj else (gj, gi)
                flagged.setdefault(key, []).append(t)

    candidates = []
    for (i, j), time_indices in flagged.items():
        candidates.append(CandidatePair(
            i=i, j=j,
            first_flagged_time=times[min(time_indices)],
            last_flagged_time=times[max(time_indices)],
        ))
    return candidates
