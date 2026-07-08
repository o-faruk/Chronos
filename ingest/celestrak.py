"""CelesTrak GP data fetch (no-auth public API).

CelesTrak's free GP API does not expose a full-catalog bulk download --
only curated groups (active satellites, individual constellations) plus a
handful of named debris clouds. See docs/validation.md for why the full
catalog comes from Space-Track instead (ingest/spacetrack.py); CelesTrak is
used here for the curated "active" list, which we use to classify which
Space-Track catalog entries are operational payloads vs everything else.
"""
from __future__ import annotations

import requests

from ingest.models import TrackedObject, parse_3le_text

BASE_URL = "https://celestrak.org/NORAD/elements/gp.php"
TIMEOUT_S = 30

# Per CelesTrak's published policy: check for these and stop, don't retry-loop.
_HARD_FAIL_STATUS = {301, 403, 404, 500}


def _fetch_3le(params: dict) -> str:
    resp = requests.get(BASE_URL, params={**params, "FORMAT": "3le"}, timeout=TIMEOUT_S)
    if resp.status_code in _HARD_FAIL_STATUS:
        raise RuntimeError(
            f"CelesTrak request failed with HTTP {resp.status_code} for params {params}; "
            "stopping rather than retrying (see CelesTrak usage policy)"
        )
    resp.raise_for_status()
    return resp.text


def fetch_active_satellites() -> list[TrackedObject]:
    """Curated list of active payloads. Used to classify Space-Track records."""
    text = _fetch_3le({"GROUP": "active"})
    return parse_3le_text(text, object_type="active", source="celestrak")


def fetch_group(group: str, object_type: str) -> list[TrackedObject]:
    """Fetch an arbitrary named CelesTrak group (e.g. a debris cloud)."""
    text = _fetch_3le({"GROUP": group})
    return parse_3le_text(text, object_type=object_type, source="celestrak")


def fetch_object(norad_id: int, object_type: str) -> TrackedObject:
    """Fetch a single object by NORAD catalog number."""
    text = _fetch_3le({"CATNR": norad_id})
    objects = parse_3le_text(text, object_type=object_type, source="celestrak")
    if not objects:
        raise ValueError(f"no CelesTrak record found for NORAD ID {norad_id}")
    return objects[0]
