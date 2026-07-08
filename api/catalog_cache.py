"""S3-backed cache of the full catalog, written by the scheduled screening
run and read by the on-demand custom-TLE endpoint (screen/pipeline.py needs
TLE lines for the whole catalog, not just the last screening result, and
querying that out of DynamoDB item-by-item would be slow -- one JSON blob in
S3 written once per scheduled run is simpler and cheap).
"""
from __future__ import annotations

import json
import os

import boto3

from ingest.models import TrackedObject

BUCKET_ENV = "CHRONOS_CATALOG_BUCKET"
OBJECT_KEY = "catalog/latest.json"


def _bucket_name() -> str:
    name = os.environ.get(BUCKET_ENV)
    if not name:
        raise RuntimeError(f"{BUCKET_ENV} must be set (see infra/template.yaml)")
    return name


def save_catalog(catalog: list[TrackedObject]) -> None:
    records = [
        {
            "norad_id": o.norad_id, "name": o.name, "line1": o.line1,
            "line2": o.line2, "object_type": o.object_type, "source": o.source,
        }
        for o in catalog
    ]
    boto3.client("s3").put_object(
        Bucket=_bucket_name(), Key=OBJECT_KEY,
        Body=json.dumps(records).encode("utf-8"),
        ContentType="application/json",
    )


def load_catalog() -> list[TrackedObject]:
    resp = boto3.client("s3").get_object(Bucket=_bucket_name(), Key=OBJECT_KEY)
    records = json.loads(resp["Body"].read())
    return [TrackedObject(**r) for r in records]
