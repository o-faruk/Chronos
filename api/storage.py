"""DynamoDB persistence for screening runs, keyed for the two access
patterns the API actually needs:

1. "give me the latest run's conjunctions" -- ChronosRuns (run metadata,
   with a fixed-key "LATEST" pointer item) + ChronosConjunctions (PK=run_id,
   SK=conjunction_id), queried by run_id.
2. "did this specific pair's miss distance close or open since last time"
   (the trend differentiator) -- ChronosPairHistory (PK=pair_key), one item
   per pair holding only its most recent miss distance, overwritten each
   run. A pair-keyed table, not a scan/query over run history, because the
   only thing a new run needs is "what was this pair's value last time,"
   not the full history.

Table names come from environment variables so the same code runs against
whatever the SAM template names the deployed tables (see infra/template.yaml).
"""
from __future__ import annotations

import os
from decimal import Decimal

import boto3

from ingest.models import TrackedObject
from screen.pipeline import Conjunction, ScreeningRun

RUNS_TABLE_ENV = "CHRONOS_RUNS_TABLE"
CONJUNCTIONS_TABLE_ENV = "CHRONOS_CONJUNCTIONS_TABLE"
PAIR_HISTORY_TABLE_ENV = "CHRONOS_PAIR_HISTORY_TABLE"

LATEST_POINTER_KEY = "LATEST"
_BATCH_WRITE_LIMIT = 25  # DynamoDB BatchWriteItem hard limit


def _table_name(env_var: str) -> str:
    name = os.environ.get(env_var)
    if not name:
        raise RuntimeError(f"{env_var} must be set (see infra/template.yaml)")
    return name


def _dynamodb():
    return boto3.resource("dynamodb")


def _pair_key(obj_a: TrackedObject, obj_b: TrackedObject) -> str:
    a, b = sorted((obj_a.norad_id, obj_b.norad_id))
    return f"{a}_{b}"


def _batched(items: list, size: int):
    for i in range(0, len(items), size):
        yield items[i:i + size]


def _floats_to_decimal(value):
    """DynamoDB's boto3 layer rejects native float -- everything numeric has
    to be Decimal. Converting via str() avoids binary-float representation
    artifacts (e.g. Decimal(0.1) vs Decimal("0.1"))."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _floats_to_decimal(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_floats_to_decimal(v) for v in value]
    return value


def compute_trends(conjunctions: list[Conjunction]) -> dict[str, str | None]:
    """Reads each conjunction's pair history, returns {conjunction.id: trend}.
    Import kept local to avoid a hard dependency for callers that only need
    write_screening_run without trend computation (e.g. a dry run)."""
    from api.schema import compute_trend

    table = _dynamodb().Table(_table_name(PAIR_HISTORY_TABLE_ENV))
    trends: dict[str, str | None] = {}
    for conj in conjunctions:
        key = _pair_key(conj.object_a, conj.object_b)
        item = table.get_item(Key={"pair_key": key}).get("Item")
        previous_miss_m = float(item["miss_distance_m"]) if item else None
        trends[conj.id] = compute_trend(conj.miss_distance_m, previous_miss_m)
    return trends


def _update_pair_history(conjunctions: list[Conjunction], run_id: str) -> None:
    table = _dynamodb().Table(_table_name(PAIR_HISTORY_TABLE_ENV))
    for batch in _batched(conjunctions, _BATCH_WRITE_LIMIT):
        with table.batch_writer() as writer:
            for conj in batch:
                writer.put_item(Item=_floats_to_decimal({
                    "pair_key": _pair_key(conj.object_a, conj.object_b),
                    "miss_distance_m": conj.miss_distance_m,
                    "run_id": run_id,
                    "tca": conj.tca.isoformat(),
                }))


def write_screening_run(run: ScreeningRun, trends: dict[str, str | None]) -> str:
    """Persists a completed run: metadata, per-conjunction rows, and updates
    pair history for the *next* run's trend computation. Returns run_id."""
    from api.schema import run_id_for
    run_id = run_id_for(run)

    runs_table = _dynamodb().Table(_table_name(RUNS_TABLE_ENV))
    run_item = {
        "run_id": run_id,
        "started_at": run.started_at.isoformat(),
        "catalog_size": run.catalog_size,
        "pairs_screened": run.pairs_screened,
        "screening_window_hours": run.screening_window_hours,
        "duration_ms": run.duration_ms,
        "conjunction_count": len(run.conjunctions),
        "candidates_screened": run.candidates_screened,
    }
    runs_table.put_item(Item=_floats_to_decimal(run_item))
    runs_table.put_item(Item=_floats_to_decimal(
        {**run_item, "run_id": LATEST_POINTER_KEY, "latest_run_id": run_id}
    ))

    conj_table = _dynamodb().Table(_table_name(CONJUNCTIONS_TABLE_ENV))
    for batch in _batched(run.conjunctions, _BATCH_WRITE_LIMIT):
        with conj_table.batch_writer() as writer:
            for conj in batch:
                from api.schema import conjunction_json
                item = conjunction_json(conj, trend=trends.get(conj.id))
                writer.put_item(Item=_floats_to_decimal(
                    {"run_id": run_id, "conjunction_id": conj.id, **item}
                ))

    _update_pair_history(run.conjunctions, run_id)
    return run_id


def get_latest_run_id() -> str | None:
    runs_table = _dynamodb().Table(_table_name(RUNS_TABLE_ENV))
    item = runs_table.get_item(Key={"run_id": LATEST_POINTER_KEY}).get("Item")
    return item["latest_run_id"] if item else None


def get_run_metadata(run_id: str) -> dict | None:
    runs_table = _dynamodb().Table(_table_name(RUNS_TABLE_ENV))
    return runs_table.get_item(Key={"run_id": run_id}).get("Item")


def get_conjunctions_for_run(run_id: str) -> list[dict]:
    conj_table = _dynamodb().Table(_table_name(CONJUNCTIONS_TABLE_ENV))
    items: list[dict] = []
    kwargs = {"KeyConditionExpression": boto3.dynamodb.conditions.Key("run_id").eq(run_id)}
    while True:
        resp = conj_table.query(**kwargs)
        items.extend(resp["Items"])
        if "LastEvaluatedKey" not in resp:
            break
        kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
    return items
