import boto3
import pytest
from moto import mock_aws

from ingest.models import TrackedObject

BUCKET = "test-chronos-catalog-cache"

ISS = TrackedObject(
    norad_id=25544, name="ISS (ZARYA)", object_type="active", source="celestrak",
    line1="1 25544U 98067A   26188.50835634  .00005806  00000+0  11369-3 0  9990",
    line2="2 25544  51.6304 199.5144 0006687 267.6545  92.3678 15.48933372574901",
)


@pytest.fixture
def aws(monkeypatch):
    monkeypatch.setenv("CHRONOS_CATALOG_BUCKET", BUCKET)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    with mock_aws():
        boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=BUCKET)
        yield


def test_save_and_load_round_trips(aws):
    from api.catalog_cache import load_catalog, save_catalog

    save_catalog([ISS])
    loaded = load_catalog()

    assert loaded == [ISS]
