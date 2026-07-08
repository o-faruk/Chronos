"""Local dev server -- runs the real Chronos backend (real Space-Track
catalog, real SGP4 propagation, real screening pipeline) without touching
AWS. Uses moto to mock DynamoDB/S3 in-process, so this exercises the actual
Lambda handler code in api/handlers.py, not a reimplemented parallel path.

First run screens the full real catalog (~3 min against ~32k objects -- see
docs/decisions.md for the measured number) and caches the result to
data/cache/local_run_cache.json so restarts are instant. Pass --refresh to
force a fresh screening run (e.g. after re-fetching the catalog).

Usage:
    source .venv/bin/activate
    python dev/local_server.py [--refresh]

Then open http://localhost:8787/ in a browser -- the server serves the
mockup dashboard itself (same origin as the API, no CORS/file:// surprises)
alongside the JSON routes.
"""
from __future__ import annotations

import json
import os
import sys
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

CACHE_DIR = REPO_ROOT / "data" / "cache"
CATALOG_FILE = CACHE_DIR / "catalog.json"
RUN_CACHE_FILE = CACHE_DIR / "local_run_cache.json"
PORT = 8787

MOCKUP_DIR = REPO_ROOT / "Chronos Orbital Conjunction System"
STATIC_FILES = {
    "/": ("Chronos Console.dc.html", "text/html; charset=utf-8"),
    "/support.js": ("support.js", "application/javascript; charset=utf-8"),
}

TABLE_ENV_DEFAULTS = {
    "CHRONOS_RUNS_TABLE": "local-chronos-runs",
    "CHRONOS_CONJUNCTIONS_TABLE": "local-chronos-conjunctions",
    "CHRONOS_PAIR_HISTORY_TABLE": "local-chronos-pair-history",
    "CHRONOS_CATALOG_BUCKET": "local-chronos-catalog-cache",
}

_response_cache: dict = {}


def _setup_env() -> None:
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    os.environ.setdefault("AWS_ACCESS_KEY_ID", "testing")
    os.environ.setdefault("AWS_SECRET_ACCESS_KEY", "testing")
    for key, value in TABLE_ENV_DEFAULTS.items():
        os.environ.setdefault(key, value)
    # Local server: never touch real Space-Track (we already have a cached
    # real catalog); this just prevents an accidental live-network surprise.
    os.environ.setdefault("SPACETRACK_USERNAME", "unused-local-dev")
    os.environ.setdefault("SPACETRACK_PASSWORD", "unused-local-dev")


def _create_tables_and_bucket() -> None:
    import boto3

    ddb = boto3.client("dynamodb")
    ddb.create_table(
        TableName=os.environ["CHRONOS_RUNS_TABLE"],
        KeySchema=[{"AttributeName": "run_id", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "run_id", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    ddb.create_table(
        TableName=os.environ["CHRONOS_CONJUNCTIONS_TABLE"],
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
        TableName=os.environ["CHRONOS_PAIR_HISTORY_TABLE"],
        KeySchema=[{"AttributeName": "pair_key", "KeyType": "HASH"}],
        AttributeDefinitions=[{"AttributeName": "pair_key", "AttributeType": "S"}],
        BillingMode="PAY_PER_REQUEST",
    )
    boto3.client("s3").create_bucket(Bucket=os.environ["CHRONOS_CATALOG_BUCKET"])


def _ensure_data(force_refresh: bool) -> dict:
    from api import catalog_cache, storage
    from api.handlers import get_catalog_snapshot_handler, get_conjunctions_handler
    from ingest.catalog import load_catalog

    if not CATALOG_FILE.exists():
        raise SystemExit(
            f"No cached catalog at {CATALOG_FILE}. Run ingestion first, e.g.:\n"
            f"  python -c \"from ingest.catalog import build_catalog, save_catalog; "
            f"save_catalog(build_catalog())\""
        )
    catalog = load_catalog(CATALOG_FILE)

    if RUN_CACHE_FILE.exists() and not force_refresh:
        print(f"[chronos-dev] using cached screening result from {RUN_CACHE_FILE}")
        catalog_cache.save_catalog(catalog)  # still seed S3 so POST /screen works
        return json.loads(RUN_CACHE_FILE.read_text())

    print(f"[chronos-dev] no cache (or --refresh) -- screening {len(catalog):,} real objects "
          f"against the real catalog. Measured ~3 min at this scale, see docs/decisions.md.")
    from screen.pipeline import run_screening

    window_start = datetime.now(timezone.utc)
    t0 = time.time()
    run = run_screening(catalog, window_start=window_start, window_hours=72)
    print(f"[chronos-dev] screening done in {time.time() - t0:.1f}s: "
          f"{len(run.conjunctions)} conjunctions found")

    trends = storage.compute_trends(run.conjunctions)
    storage.write_screening_run(run, trends)
    catalog_cache.save_catalog(catalog)

    conjunctions_response = json.loads(get_conjunctions_handler({}, None)["body"])
    catalog_snapshot_response = json.loads(get_catalog_snapshot_handler({}, None)["body"])
    cached = {"conjunctions": conjunctions_response, "catalog_snapshot": catalog_snapshot_response}

    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    RUN_CACHE_FILE.write_text(json.dumps(cached))
    print(f"[chronos-dev] cached to {RUN_CACHE_FILE} -- next restart will be instant "
          f"(pass --refresh to force a new screening run)")
    return cached


class ChronosDevHandler(BaseHTTPRequestHandler):
    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def _json(self, status: int, obj: dict) -> None:
        body = json.dumps(obj).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self) -> None:
        self.send_response(204)
        self._cors()
        self.end_headers()

    def _static(self, filename: str, content_type: str) -> None:
        path = MOCKUP_DIR / filename
        if not path.exists():
            self._json(404, {"error": f"static file not found: {path}"})
            return
        body = path.read_bytes()
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self._cors()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        if self.path == "/conjunctions":
            self._json(200, _response_cache["conjunctions"])
        elif self.path == "/catalog-snapshot":
            # Live, not cached: positions are only valid for the instant
            # they were computed. Re-propagating the real ~32k catalog to
            # "now" is well under a second -- see docs/decisions.md.
            from api.handlers import get_catalog_snapshot_handler
            resp = get_catalog_snapshot_handler({}, None)
            self._json(resp["statusCode"], json.loads(resp["body"]))
        elif self.path in STATIC_FILES:
            filename, content_type = STATIC_FILES[self.path]
            self._static(filename, content_type)
        else:
            self._json(404, {"error": f"no route for GET {self.path}"})

    def do_POST(self) -> None:
        if self.path == "/screen":
            length = int(self.headers.get("Content-Length", 0))
            raw_body = self.rfile.read(length).decode("utf-8") if length else "{}"
            from api.handlers import screen_custom_object_handler
            resp = screen_custom_object_handler({"body": raw_body}, None)
            self._json(resp["statusCode"], json.loads(resp["body"]))
        else:
            self._json(404, {"error": f"no route for POST {self.path}"})

    def log_message(self, fmt: str, *args) -> None:
        print(f"[chronos-dev] {self.address_string()} {fmt % args}")


def main() -> None:
    force_refresh = "--refresh" in sys.argv

    _setup_env()
    from moto import mock_aws
    mock = mock_aws()
    mock.start()
    try:
        _create_tables_and_bucket()
        global _response_cache
        _response_cache = _ensure_data(force_refresh)

        server = ThreadingHTTPServer(("localhost", PORT), ChronosDevHandler)
        print(f"[chronos-dev] open http://localhost:{PORT}/ in a browser to see the dashboard")
        print(f"[chronos-dev] API routes:")
        print(f"[chronos-dev]   GET  /conjunctions")
        print(f"[chronos-dev]   GET  /catalog-snapshot")
        print(f"[chronos-dev]   POST /screen   (body: {{\"name\",\"line1\",\"line2\"}})")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print("\n[chronos-dev] shutting down")
    finally:
        mock.stop()


if __name__ == "__main__":
    main()
