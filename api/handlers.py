"""Lambda entry points. Three handlers, matching infra/template.yaml:

- scheduled_screening_handler: EventBridge-triggered. Ingests the full
  catalog, runs the full screening pipeline, writes results + trend history,
  caches the catalog to S3 for the on-demand endpoint below.
- get_conjunctions_handler: API Gateway GET. Serves the latest run in the
  locked response schema.
- screen_custom_object_handler: API Gateway POST. The custom-TLE-injection
  differentiator (see docs/validation.md Phase 0) -- screens a user-submitted
  object against the cached catalog on demand, without a full catalog-vs-
  catalog run.

Kept thin on purpose: business logic lives in ingest/propagate/screen/api.schema,
these functions only translate between the Lambda event/response shape and
that logic, so the logic itself stays testable without any AWS event
plumbing (see tests/test_api_handlers.py).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from decimal import Decimal

from api import catalog_cache, storage
from ingest.catalog import build_catalog
from ingest.models import parse_3le_text
from screen.pipeline import run_screening
from screen.targeted import run_targeted_screening

logger = logging.getLogger()
logger.setLevel(logging.INFO)

DEFAULT_WINDOW_HOURS = 72.0
# API Gateway's Lambda proxy integration has a hard, non-configurable 29s
# timeout. A full 72h targeted screen measures at roughly window_hours
# proportional to the ~27s/72h full-catalog propagation cost (see
# docs/decisions.md) -- 72h would run right up against that limit with no
# margin. 24h keeps it comfortably under with room for cold-start/network
# overhead. The scheduled full run (72h, 30k x 30k) doesn't have this
# constraint since it's invoked by EventBridge, not a synchronous API call.
CUSTOM_SCREEN_WINDOW_HOURS = 24.0


def _json_default(value):
    # DynamoDB items round-trip numbers as Decimal (see api/storage.py);
    # json.dumps doesn't know how to serialize those natively.
    if isinstance(value, Decimal):
        return int(value) if value % 1 == 0 else float(value)
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


def _json_response(status: int, body: dict) -> dict:
    return {
        "statusCode": status,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body, default=_json_default),
    }


def scheduled_screening_handler(event, context) -> dict:
    from api.secrets import load_spacetrack_credentials_into_env
    load_spacetrack_credentials_into_env()

    catalog = build_catalog()
    window_start = datetime.now(timezone.utc)

    run = run_screening(catalog, window_start=window_start, window_hours=DEFAULT_WINDOW_HOURS)
    trends = storage.compute_trends(run.conjunctions)
    run_id = storage.write_screening_run(run, trends)
    catalog_cache.save_catalog(catalog)

    logger.info(
        "screening run %s: catalog=%d conjunctions=%d duration_ms=%.0f",
        run_id, run.catalog_size, len(run.conjunctions), run.duration_ms,
    )
    return _json_response(200, {"run_id": run_id, "conjunction_count": len(run.conjunctions)})


def get_conjunctions_handler(event, context) -> dict:
    run_id = storage.get_latest_run_id()
    if run_id is None:
        return _json_response(404, {"error": "no screening run has completed yet"})

    metadata = storage.get_run_metadata(run_id)
    conjunction_items = storage.get_conjunctions_for_run(run_id)

    response = {
        "screening_run": {
            "run_id": run_id,
            "started_at": metadata["started_at"],
            "catalog_size": int(metadata["catalog_size"]),
            "pairs_screened": int(metadata["pairs_screened"]),
            "screening_window_hours": float(metadata["screening_window_hours"]),
            "duration_ms": float(metadata["duration_ms"]),
            "candidates_screened": int(metadata.get("candidates_screened", 0)),
        },
        "conjunctions": [
            {
                "id": item["conjunction_id"],
                "object_a": item["object_a"],
                "object_b": item["object_b"],
                "tca": item["tca"],
                "miss_distance_m": float(item["miss_distance_m"]),
                "relative_velocity_kms": float(item["relative_velocity_kms"]),
                "severity": item["severity"],
                "trend": item.get("trend"),
                "approach_angle_deg": float(item.get("approach_angle_deg", 0)),
                "altitude_km": float(item.get("altitude_km", 0)),
                "regime": item.get("regime", "LEO"),
            }
            for item in conjunction_items
        ],
        # catalog_snapshot is intentionally omitted from this endpoint --
        # it's derived from the cached catalog (api.catalog_cache), not
        # stored per-run, so it's served from GET /catalog-snapshot instead
        # of duplicating ~32k objects into every conjunctions response.
    }
    return _json_response(200, response)


def get_catalog_snapshot_handler(event, context) -> dict:
    from api.schema import catalog_snapshot_json

    catalog = catalog_cache.load_catalog()
    epoch = datetime.now(timezone.utc)
    return _json_response(200, {
        "position_epoch": epoch.isoformat().replace("+00:00", "Z"),
        "catalog_snapshot": catalog_snapshot_json(catalog, epoch=epoch),
    })


def screen_custom_object_handler(event, context) -> dict:
    """POST body: {"name": str, "line1": str, "line2": str}. Screens the
    submitted object against the cached full catalog (from the last
    scheduled run) and returns conjunctions in the same shape as the main
    endpoint, minus catalog_snapshot and run-over-run trend (a one-off
    custom object has no history to trend against)."""
    try:
        body = json.loads(event.get("body") or "{}")
        name = body["name"]
        line1 = body["line1"]
        line2 = body["line2"]
    except (KeyError, json.JSONDecodeError) as exc:
        return _json_response(400, {"error": f"expected JSON body with name/line1/line2: {exc}"})

    try:
        [custom_obj] = parse_3le_text(f"{name}\n{line1}\n{line2}", object_type="active", source="custom")
    except ValueError as exc:
        return _json_response(400, {"error": f"invalid TLE: {exc}"})

    try:
        catalog = catalog_cache.load_catalog()
    except Exception as exc:
        logger.exception("failed to load cached catalog")
        return _json_response(503, {"error": f"catalog not available yet: {exc}"})

    # Exclude any catalog entry sharing the submitted object's NORAD ID --
    # otherwise a real, already-cataloged object submitted verbatim would
    # screen against itself as a trivial (and misleading) zero-distance pair.
    catalog = [o for o in catalog if o.norad_id != custom_obj.norad_id]

    window_start = datetime.now(timezone.utc)
    run = run_targeted_screening(
        custom_obj, catalog, window_start=window_start, window_hours=CUSTOM_SCREEN_WINDOW_HOURS,
    )

    from api.schema import conjunction_json, screening_run_json

    response = {
        "screening_run": screening_run_json(run),
        "object": {"norad_id": custom_obj.norad_id, "name": custom_obj.name, "type": custom_obj.object_type},
        "conjunctions": [conjunction_json(c, trend=None) for c in run.conjunctions],
    }
    return _json_response(200, response)
