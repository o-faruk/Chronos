"""Live sanity check: fetch the current ISS TLE and confirm propagated state
is physically plausible for a known LEO object (altitude/speed magnitude).
Requires network; skips rather than fails if CelesTrak is unreachable so CI
isn't hostage to an external service.
"""
from datetime import datetime, timezone

import numpy as np
import pytest

from propagate.batch import propagate_catalog
from propagate.time_grid import time_grid

ISS_NORAD_ID = 25544


def test_iss_live_propagation_is_physically_plausible():
    try:
        from ingest import celestrak
        iss = celestrak.fetch_object(ISS_NORAD_ID, object_type="active")
    except Exception as exc:
        pytest.skip(f"CelesTrak unreachable: {exc}")

    jd, fr = time_grid(datetime.now(timezone.utc), window_hours=0, step_seconds=1)
    result = propagate_catalog([iss], jd, fr)

    assert result.err[0, 0] == 0
    r = result.r[0, 0]
    v = result.v[0, 0]

    earth_radius_km = 6378.137
    altitude_km = float(np.linalg.norm(r)) - earth_radius_km
    speed_km_s = float(np.linalg.norm(v))

    # ISS orbits at ~400-420 km, ~7.66 km/s -- published, well-known values.
    assert 380 < altitude_km < 450, f"ISS altitude {altitude_km:.1f} km outside expected LEO range"
    assert 7.4 < speed_km_s < 7.9, f"ISS speed {speed_km_s:.3f} km/s outside expected LEO range"
