from datetime import datetime, timezone

from api.schema import (
    catalog_snapshot_json,
    compute_trend,
    conjunction_json,
    full_response_json,
    screening_run_json,
)
from ingest.models import TrackedObject
from screen.pipeline import Conjunction, ScreeningRun

ISS = TrackedObject(
    norad_id=25544, name="ISS (ZARYA)", object_type="active", source="celestrak",
    line1="1 25544U 98067A   26188.50835634  .00005806  00000+0  11369-3 0  9990",
    line2="2 25544  51.6304 199.5144 0006687 267.6545  92.3678 15.48933372574901",
)
DEBRIS = TrackedObject(
    norad_id=39026, name="COSMOS 1408 DEB", object_type="debris", source="celestrak",
    line1="1 39026U 82092AJ  26188.50000000  .00000100  00000-0  10000-3 0  9995",
    line2="2 39026  82.5600 100.0000 0010000 200.0000 160.0000 14.50000000123456",
)

CONJ = Conjunction(
    id="CHRONOS-2026-000001", object_a=ISS, object_b=DEBRIS,
    tca=datetime(2026, 7, 7, 16, 22, 0, tzinfo=timezone.utc),
    miss_distance_m=142.3, relative_velocity_kms=11.4, severity="critical",
    approach_angle_deg=98.4, altitude_km=548.2, regime="LEO",
)


def test_conjunction_json_matches_locked_field_names():
    j = conjunction_json(CONJ, trend="closing")
    assert set(j.keys()) == {
        "id", "object_a", "object_b", "tca", "miss_distance_m",
        "relative_velocity_kms", "severity", "trend",
        "approach_angle_deg", "altitude_km", "regime",
    }
    assert j["object_a"] == {"norad_id": 25544, "name": "ISS (ZARYA)", "type": "active"}
    assert j["object_b"] == {"norad_id": 39026, "name": "COSMOS 1408 DEB", "type": "debris"}
    assert j["tca"] == "2026-07-07T16:22:00Z"
    assert j["severity"] == "critical"
    assert j["trend"] == "closing"
    assert j["regime"] == "LEO"


def test_conjunction_json_trend_none_when_no_prior_run():
    j = conjunction_json(CONJ, trend=None)
    assert j["trend"] is None


def test_compute_trend_new_when_no_previous():
    assert compute_trend(500.0, None) == "new"


def test_compute_trend_closing_and_opening():
    assert compute_trend(400.0, 900.0) == "closing"
    assert compute_trend(900.0, 400.0) == "opening"


def test_compute_trend_stable_within_band():
    assert compute_trend(500.0, 510.0) == "stable"


def test_catalog_snapshot_json_fields():
    snap = catalog_snapshot_json([ISS])
    assert snap == [{
        "norad_id": 25544, "name": "ISS (ZARYA)", "type": "active",
        "altitude_km": snap[0]["altitude_km"], "period_min": snap[0]["period_min"],
        "regime": "LEO",
    }]
    assert 380 < snap[0]["altitude_km"] < 430
    assert 90 < snap[0]["period_min"] < 95
    assert "position_km" not in snap[0]  # omitted without an epoch


def test_catalog_snapshot_json_includes_real_position_when_epoch_given():
    epoch = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    snap = catalog_snapshot_json([ISS], epoch=epoch)
    assert "position_km" in snap[0]
    x, y, z = snap[0]["position_km"]
    r = (x**2 + y**2 + z**2) ** 0.5
    # ISS orbits ~6771-6800 km from Earth's center (400-420 km altitude + 6378 km radius).
    assert 6750 < r < 6820


def test_screening_run_json_fields():
    run = ScreeningRun(
        started_at=datetime(2026, 7, 7, 2, 0, 0, tzinfo=timezone.utc),
        catalog_size=30142, pairs_screened=454_000_000,
        screening_window_hours=72, duration_ms=118000.0, conjunctions=[CONJ],
        candidates_screened=2914,
    )
    j = screening_run_json(run)
    assert j == {
        "run_id": "20260707T020000Z",
        "started_at": "2026-07-07T02:00:00Z", "catalog_size": 30142,
        "pairs_screened": 454_000_000, "screening_window_hours": 72,
        "duration_ms": 118000.0, "candidates_screened": 2914,
    }


def test_full_response_json_shape():
    run = ScreeningRun(
        started_at=datetime(2026, 7, 7, 2, 0, 0, tzinfo=timezone.utc),
        catalog_size=2, pairs_screened=1, screening_window_hours=72,
        duration_ms=100.0, conjunctions=[CONJ],
    )
    resp = full_response_json(run, [ISS, DEBRIS], trends={CONJ.id: "new"})
    assert set(resp.keys()) == {"screening_run", "conjunctions", "catalog_snapshot"}
    assert len(resp["conjunctions"]) == 1
    assert resp["conjunctions"][0]["trend"] == "new"
    assert len(resp["catalog_snapshot"]) == 2
