"""Fine screening: precise time-of-closest-approach + minimum miss distance
for a single candidate pair, refined by continuous-time optimization rather
than further grid sampling (avoids quantizing the miss distance to whatever
the coarse step happened to land on).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import numpy as np
from scipy.optimize import minimize_scalar
from sgp4.api import Satrec

from ingest.models import TrackedObject


R_EARTH_KM = 6378.137


@dataclass(frozen=True)
class TcaResult:
    tca: datetime
    miss_distance_km: float
    relative_velocity_km_s: float
    approach_angle_deg: float  # angle between the two velocity vectors at TCA
    altitude_km: float  # object_a's altitude at TCA (not a catalog average)


def _jd_fr(dt: datetime) -> tuple[float, float]:
    dt_utc = dt.astimezone(timezone.utc)
    jd = 2440587.5 + dt_utc.timestamp() / 86400.0
    return float(int(jd)), jd - int(jd)


def refine_tca(
    obj_a: TrackedObject,
    obj_b: TrackedObject,
    search_start: datetime,
    search_end: datetime,
) -> TcaResult:
    """Search for the minimum-distance time within [search_start, search_end]
    (typically the coarse-flagged window padded by one coarse step either side)."""
    sat_a = Satrec.twoline2rv(obj_a.line1, obj_a.line2)
    sat_b = Satrec.twoline2rv(obj_b.line1, obj_b.line2)

    epoch_jd, epoch_fr = _jd_fr(search_start)
    lo_min = 0.0
    hi_min = (search_end - search_start).total_seconds() / 60.0

    def distance_km(t_min: float) -> float:
        fr = epoch_fr + t_min / 1440.0
        _, r_a, _ = sat_a.sgp4(epoch_jd, fr)
        _, r_b, _ = sat_b.sgp4(epoch_jd, fr)
        return float(np.linalg.norm(np.array(r_a) - np.array(r_b)))

    # Dense-ish grid first (guards against minimize_scalar landing in a local
    # minimum when the pair has multiple close passes inside the window),
    # then refine the best grid point with bounded Brent minimization.
    grid = np.linspace(lo_min, hi_min, max(int(hi_min) + 1, 20))
    grid_distances = [distance_km(t) for t in grid]
    best_idx = int(np.argmin(grid_distances))
    pad = grid[1] - grid[0] if len(grid) > 1 else 1.0
    lo = max(lo_min, grid[best_idx] - pad)
    hi = min(hi_min, grid[best_idx] + pad)

    res = minimize_scalar(distance_km, bounds=(lo, hi), method="bounded",
                           options={"xatol": 1e-4})
    t_star_min = res.x
    miss_km = res.fun

    fr_star = epoch_fr + t_star_min / 1440.0
    _, r_a, v_a = sat_a.sgp4(epoch_jd, fr_star)
    _, r_b, v_b = sat_b.sgp4(epoch_jd, fr_star)
    v_a, v_b = np.array(v_a), np.array(v_b)
    rel_v = float(np.linalg.norm(v_a - v_b))

    # Crossing angle between velocity vectors -- standard conjunction-assessment
    # metric (near 180deg = head-on, near 0deg = co-moving/overtaking).
    cos_angle = np.dot(v_a, v_b) / (np.linalg.norm(v_a) * np.linalg.norm(v_b))
    approach_angle_deg = float(np.degrees(np.arccos(np.clip(cos_angle, -1.0, 1.0))))

    altitude_km = float(np.linalg.norm(np.array(r_a)) - R_EARTH_KM)

    tca = search_start + timedelta(minutes=t_star_min)
    return TcaResult(
        tca=tca, miss_distance_km=miss_km, relative_velocity_km_s=rel_v,
        approach_angle_deg=approach_angle_deg, altitude_km=altitude_km,
    )
