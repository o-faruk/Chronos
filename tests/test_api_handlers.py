"""Lambda handlers exercised end-to-end against moto (mocked AWS) and a
small in-memory catalog (no real Space-Track/CelesTrak network calls)."""
import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import boto3
import pytest
from moto import mock_aws

from ingest.models import TrackedObject, parse_3le_text

RUNS_TABLE = "test-h-chronos-runs"
CONJ_TABLE = "test-h-chronos-conjunctions"
PAIR_TABLE = "test-h-chronos-pair-history"
BUCKET = "test-h-chronos-catalog-cache"

FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "known_conjunction.txt"


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("CHRONOS_RUNS_TABLE", RUNS_TABLE)
    monkeypatch.setenv("CHRONOS_CONJUNCTIONS_TABLE", CONJ_TABLE)
    monkeypatch.setenv("CHRONOS_PAIR_HISTORY_TABLE", PAIR_TABLE)
    monkeypatch.setenv("CHRONOS_CATALOG_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    # already set -> api.secrets.load_spacetrack_credentials_into_env() is a
    # no-op; these tests cover the screening/storage flow, not SSM lookup
    # (that's tests/test_api_secrets.py).
    monkeypatch.setenv("SPACETRACK_USERNAME", "test-user")
    monkeypatch.setenv("SPACETRACK_PASSWORD", "test-pass")
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
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield


def _fixture_pair():
    # One "active" so the pair survives run_screening's active-vs-catalog
    # filter (see docs/validation.md).
    objects = parse_3le_text(FIXTURE.read_text(), object_type="debris", source="celestrak")
    return [dataclasses.replace(objects[0], object_type="active"), objects[1]]


def test_scheduled_then_get_conjunctions_round_trip(aws):
    from api.handlers import get_conjunctions_handler, scheduled_screening_handler

    epoch = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    with patch("api.handlers.build_catalog", return_value=_fixture_pair()), \
         patch("api.handlers.datetime") as mock_dt:
        mock_dt.now.return_value = epoch - timedelta(minutes=30)
        resp = scheduled_screening_handler({}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["conjunction_count"] == 1

    resp2 = get_conjunctions_handler({}, None)
    assert resp2["statusCode"] == 200
    body2 = json.loads(resp2["body"])
    assert body2["screening_run"]["catalog_size"] == 2
    assert len(body2["conjunctions"]) == 1
    conj = body2["conjunctions"][0]
    assert conj["severity"] == "critical"
    assert abs(conj["miss_distance_m"] - 30.0) < 1.0
    assert conj["trend"] == "new"


def test_get_conjunctions_before_any_run_returns_404(aws):
    from api.handlers import get_conjunctions_handler

    resp = get_conjunctions_handler({}, None)
    assert resp["statusCode"] == 404


def test_get_catalog_snapshot_after_scheduled_run(aws):
    from api.handlers import get_catalog_snapshot_handler, scheduled_screening_handler

    with patch("api.handlers.build_catalog", return_value=_fixture_pair()):
        scheduled_screening_handler({}, None)

    resp = get_catalog_snapshot_handler({}, None)
    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert len(body["catalog_snapshot"]) == 2
    assert {"norad_id", "name", "type", "altitude_km", "period_min", "regime", "position_km"} == set(body["catalog_snapshot"][0].keys())
    assert "position_epoch" in body


def test_screen_custom_object_finds_conjunction_against_cached_catalog(aws):
    from api.handlers import screen_custom_object_handler
    from api import catalog_cache

    target, other = _fixture_pair()
    catalog_cache.save_catalog([other])

    epoch = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)
    event = {"body": json.dumps({"name": target.name, "line1": target.line1, "line2": target.line2})}
    with patch("api.handlers.datetime") as mock_dt:
        mock_dt.now.return_value = epoch - timedelta(minutes=30)
        resp = screen_custom_object_handler(event, None)

    assert resp["statusCode"] == 200
    body = json.loads(resp["body"])
    assert body["object"]["norad_id"] == target.norad_id
    assert len(body["conjunctions"]) == 1
    assert abs(body["conjunctions"][0]["miss_distance_m"] - 30.0) < 1.0
    assert body["screening_run"]["screening_window_hours"] == 24.0


def test_screen_custom_object_rejects_malformed_body(aws):
    from api.handlers import screen_custom_object_handler

    resp = screen_custom_object_handler({"body": json.dumps({"name": "X"})}, None)
    assert resp["statusCode"] == 400


def test_screen_custom_object_without_cached_catalog_returns_503(aws):
    from api.handlers import screen_custom_object_handler

    target, _ = _fixture_pair()
    event = {"body": json.dumps({"name": target.name, "line1": target.line1, "line2": target.line2})}
    resp = screen_custom_object_handler(event, None)
    assert resp["statusCode"] == 503
