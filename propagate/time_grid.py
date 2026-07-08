"""Build the (jd, fr) time arrays SatrecArray.sgp4() expects."""
from __future__ import annotations

from datetime import datetime, timezone

import numpy as np
from sgp4.api import jday


def time_grid(start: datetime, window_hours: float, step_seconds: float) -> tuple[np.ndarray, np.ndarray]:
    """Return (jd, fr) arrays spanning `window_hours` from `start` at `step_seconds` cadence.

    jd/fr split (whole Julian day, fractional day) matches what sgp4 expects
    for numerical precision -- see the sgp4 package docs on why a single
    float Julian date loses precision at sub-second scales.
    """
    if start.tzinfo is None:
        raise ValueError("start must be timezone-aware (UTC)")
    start_utc = start.astimezone(timezone.utc)

    n_steps = int(window_hours * 3600.0 / step_seconds) + 1
    offsets_days = np.arange(n_steps) * step_seconds / 86400.0

    jd0, fr0 = jday(
        start_utc.year, start_utc.month, start_utc.day,
        start_utc.hour, start_utc.minute,
        start_utc.second + start_utc.microsecond / 1e6,
    )
    fr = fr0 + offsets_days
    jd = np.full(n_steps, jd0)
    return jd, fr
