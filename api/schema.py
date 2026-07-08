"""Serializes internal dataclasses (screen/pipeline.py, ingest/models.py) into
the locked API response contract.

Contract source of truth: the mockup UI at
`Chronos Orbital Conjunction System/Chronos Console.dc.html` and the schema
in the original build brief. Per that brief: "if you need to add fields, add
them; don't rename existing ones without flagging it." Additions made while
wiring the mockup up to real data (see docs/decisions.md, "Phase 4" section):

    {
      "screening_run": {started_at, catalog_size, pairs_screened,
                         screening_window_hours, duration_ms,
                         run_id, candidates_screened},        <- +2 fields
      "conjunctions": [{id, object_a, object_b, tca, miss_distance_m,
                         relative_velocity_kms, severity, trend,
                         approach_angle_deg, altitude_km, regime}],  <- +4 fields
      "catalog_snapshot": [{norad_id, name, type, altitude_km, period_min,
                             regime}]                          <- +1 field
    }

Every addition maps to a real, computed value (not a placeholder) -- see
screen/tca.py for approach_angle_deg/altitude_km, screen/regime.py for
regime -- driven by fields the mockup UI already had slots for
(APPROACH ∠, ALTITUDE, CATALOG BY REGIME) that the original brief's schema
didn't cover.
"""
from __future__ import annotations

from datetime import datetime

from ingest.models import TrackedObject
from propagate.orbit_params import altitude_km, period_min
from screen.pipeline import Conjunction, ScreeningRun
from screen.regime import classify_regime

# trend is None when there's no prior run to compare against (first-ever run
# for this pair) -- distinct from "stable", which means we *do* have a prior
# value and it barely moved. Valid values: new, closing, opening, stable.
_STABLE_BAND_M = 50.0  # miss distance change smaller than this counts as "stable"


def compute_trend(current_miss_distance_m: float, previous_miss_distance_m: float | None) -> str:
    if previous_miss_distance_m is None:
        return "new"
    delta = current_miss_distance_m - previous_miss_distance_m
    if abs(delta) < _STABLE_BAND_M:
        return "stable"
    return "closing" if delta < 0 else "opening"


def run_id_for(run: ScreeningRun) -> str:
    return run.started_at.strftime("%Y%m%dT%H%M%SZ")


def _object_json(obj: TrackedObject) -> dict:
    return {"norad_id": obj.norad_id, "name": obj.name, "type": obj.object_type}


def conjunction_json(conj: Conjunction, trend: str | None) -> dict:
    return {
        "id": conj.id,
        "object_a": _object_json(conj.object_a),
        "object_b": _object_json(conj.object_b),
        "tca": conj.tca.isoformat().replace("+00:00", "Z"),
        "miss_distance_m": round(conj.miss_distance_m, 1),
        "relative_velocity_kms": round(conj.relative_velocity_kms, 4),
        "severity": conj.severity,
        "trend": trend,
        "approach_angle_deg": round(conj.approach_angle_deg, 1),
        "altitude_km": round(conj.altitude_km, 1),
        "regime": conj.regime,
    }


def catalog_snapshot_json(catalog: list[TrackedObject], epoch: datetime | None = None) -> list[dict]:
    """epoch: if given, propagates the whole catalog to this single instant
    and includes each object's real position_km ([x,y,z], TEME frame -- SGP4's
    native output frame, close enough to J2000 for a visualization, not
    precise enough to call it that outright) -- see docs/decisions.md,
    "Real orbit renderer" section, for why this is computed fresh per
    request rather than cached (positions go stale in seconds; the ~32k
    catalog propagates at one instant in well under a second, so live is
    cheap). Omitted when epoch is None so existing callers that only need
    the scalar fields aren't forced to pay the propagation cost."""
    positions = errs = None
    if epoch is not None:
        from propagate.batch import propagate_catalog
        from propagate.time_grid import time_grid
        jd, fr = time_grid(epoch, window_hours=0, step_seconds=60)
        result = propagate_catalog(catalog, jd, fr)
        positions, errs = result.r[:, 0, :], result.err[:, 0]

    snapshot = []
    for i, obj in enumerate(catalog):
        try:
            entry = {
                "norad_id": obj.norad_id,
                "name": obj.name,
                "type": obj.object_type,
                "altitude_km": round(altitude_km(obj), 1),
                "period_min": round(period_min(obj), 2),
                "regime": classify_regime(obj),
            }
            if positions is not None and errs[i] == 0:
                entry["position_km"] = [round(float(v), 1) for v in positions[i]]
            snapshot.append(entry)
        except (ValueError, ZeroDivisionError):
            continue  # malformed mean-motion field on a handful of catalog entries; skip rather than fail the whole snapshot
    return snapshot


def screening_run_json(run: ScreeningRun) -> dict:
    return {
        "run_id": run_id_for(run),
        "started_at": run.started_at.isoformat().replace("+00:00", "Z"),
        "catalog_size": run.catalog_size,
        "pairs_screened": run.pairs_screened,
        "screening_window_hours": run.screening_window_hours,
        "duration_ms": round(run.duration_ms, 1),
        "candidates_screened": run.candidates_screened,
    }


def full_response_json(
    run: ScreeningRun,
    catalog: list[TrackedObject],
    trends: dict[str, str | None],
) -> dict:
    """trends: conjunction.id -> trend label, precomputed by the caller
    (api/storage.py) since that's where the previous run's data lives."""
    return {
        "screening_run": screening_run_json(run),
        "conjunctions": [conjunction_json(c, trends.get(c.id)) for c in run.conjunctions],
        "catalog_snapshot": catalog_snapshot_json(catalog),
    }
