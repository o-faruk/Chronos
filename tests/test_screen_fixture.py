"""Regression fixture: the known close-approach pair in
testdata/known_conjunction.txt (derivation + hand-computed expected values in
testdata/known_conjunction.md) must be caught by the full screening pipeline,
not just by the low-level TCA refiner -- this is the test that fails if the
coarse pass, regime bucketing, or fine refinement breaks.
"""
import dataclasses
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingest.models import parse_3le_text
from screen.pipeline import run_screening
from screen.tca import refine_tca

FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "known_conjunction.txt"
EPOCH = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


def _load_pair():
    # One "active" so the pair survives run_screening's active-vs-catalog
    # filter (see docs/validation.md) -- matches a realistic scenario of an
    # operator's satellite vs. a nearby debris fragment.
    objects = parse_3le_text(FIXTURE.read_text(), object_type="debris", source="celestrak")
    assert len(objects) == 2
    active_a = dataclasses.replace(objects[0], object_type="active")
    return [active_a, objects[1]]


def test_tca_refiner_recovers_known_close_approach():
    obj_a, obj_b = _load_pair()
    result = refine_tca(obj_a, obj_b, EPOCH - timedelta(minutes=30), EPOCH + timedelta(minutes=30))

    assert abs((result.tca - EPOCH).total_seconds()) < 5.0
    assert abs(result.miss_distance_km * 1000 - 30.0) < 1.0
    # Co-planar, same angular position at epoch by construction -> the two
    # objects are moving in essentially the same direction, not crossing.
    assert result.approach_angle_deg < 1.0
    # FIXTURE-A is designed at 550.000 km altitude.
    assert abs(result.altitude_km - 550.0) < 1.0


def test_full_pipeline_catches_known_conjunction():
    obj_a, obj_b = _load_pair()
    catalog = [obj_a, obj_b]

    run = run_screening(catalog, window_start=EPOCH - timedelta(hours=1), window_hours=2)

    assert len(run.conjunctions) == 1
    conj = run.conjunctions[0]
    assert {conj.object_a.norad_id, conj.object_b.norad_id} == {obj_a.norad_id, obj_b.norad_id}
    assert conj.severity == "critical"
    assert abs(conj.miss_distance_m - 30.0) < 1.0
    assert abs((conj.tca - EPOCH).total_seconds()) < 5.0
