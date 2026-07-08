"""DynamoDB interactions, tested against moto (no real AWS account touched
or required)."""
import os
from datetime import datetime, timedelta, timezone

import boto3
import pytest
from moto import mock_aws

from ingest.models import TrackedObject
from screen.pipeline import Conjunction, ScreeningRun

RUNS_TABLE = "test-chronos-runs"
CONJ_TABLE = "test-chronos-conjunctions"
PAIR_TABLE = "test-chronos-pair-history"

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


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("CHRONOS_RUNS_TABLE", RUNS_TABLE)
    monkeypatch.setenv("CHRONOS_CONJUNCTIONS_TABLE", CONJ_TABLE)
    monkeypatch.setenv("CHRONOS_PAIR_HISTORY_TABLE", PAIR_TABLE)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        ddb = boto3.client("dynamodb", region_name="us-east-1")
        ddb.create_table(
            TableName=RUNS_TABLE,
            KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=CONJ_TABLE,
            KeySchema=[
                {"AttributeName": "run_id", "KeyType": "HASH"},
                {"AttributeName": "conjunction_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "run_id", "AttributeType": "S"},
                {"AttributeName": "conjunction_id", "AttributeType": "S"},
            ],
            BillingMode="PAY_PER_REQUEST",
        )
        ddb.create_table(
            TableName=PAIR_TABLE,
            KeySchema=[{"AttributeName": "pair_key", "KeyType": "HASH"}],
            AttributeDefinitions=[{"AttributeName": "pair_key", "AttributeType": "S"}],
            BillingMode="PAY_PER_REQUEST",
        )
        yield


def _make_run(miss_distance_m: float, started_at: datetime) -> ScreeningRun:
    conj = Conjunction(
        id="CHRONOS-2026-000001", object_a=ISS, object_b=DEBRIS,
        tca=started_at + timedelta(hours=1), miss_distance_m=miss_distance_m,
        relative_velocity_kms=11.4, severity="critical",
        approach_angle_deg=98.4, altitude_km=548.2, regime="LEO",
    )
    return ScreeningRun(
        started_at=started_at, catalog_size=2, pairs_screened=1,
        screening_window_hours=72, duration_ms=100.0, conjunctions=[conj],
    )


def test_first_run_has_no_trend_and_round_trips(aws):
    from api.storage import compute_trends, get_conjunctions_for_run, get_latest_run_id, write_screening_run

    run = _make_run(500.0, datetime(2026, 7, 7, tzinfo=timezone.utc))
    trends = compute_trends(run.conjunctions)
    assert trends["CHRONOS-2026-000001"] == "new"

    run_id = write_screening_run(run, trends)
    assert get_latest_run_id() == run_id

    stored = get_conjunctions_for_run(run_id)
    assert len(stored) == 1
    assert stored[0]["conjunction_id"] == "CHRONOS-2026-000001"
    assert stored[0]["trend"] == "new"


def test_second_run_computes_closing_trend_from_first(aws):
    from api.storage import compute_trends, write_screening_run

    run1 = _make_run(900.0, datetime(2026, 7, 7, tzinfo=timezone.utc))
    write_screening_run(run1, compute_trends(run1.conjunctions))

    run2 = _make_run(400.0, datetime(2026, 7, 8, tzinfo=timezone.utc))
    trends2 = compute_trends(run2.conjunctions)
    assert trends2["CHRONOS-2026-000001"] == "closing"

    run_id2 = write_screening_run(run2, trends2)
    from api.storage import get_conjunctions_for_run
    stored = get_conjunctions_for_run(run_id2)
    assert stored[0]["trend"] == "closing"


def test_latest_pointer_updates_across_runs(aws):
    from api.storage import get_latest_run_id, write_screening_run, compute_trends

    run1 = _make_run(900.0, datetime(2026, 7, 7, tzinfo=timezone.utc))
    run_id1 = write_screening_run(run1, compute_trends(run1.conjunctions))
    assert get_latest_run_id() == run_id1

    run2 = _make_run(400.0, datetime(2026, 7, 8, tzinfo=timezone.utc))
    run_id2 = write_screening_run(run2, compute_trends(run2.conjunctions))
    assert get_latest_run_id() == run_id2
    assert run_id2 != run_id1
