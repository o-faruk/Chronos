"""Space-Track.org GP data fetch -- bulk source for the full unclassified catalog.

Requires a free Space-Track account (manual approval). Credentials are read
from environment variables, never hardcoded or logged:
  SPACETRACK_USERNAME
  SPACETRACK_PASSWORD

See docs/validation.md for why Space-Track is the bulk source (CelesTrak's
free API only exposes curated groups, not the full ~30k-object catalog).
"""
from __future__ import annotations

import os

import requests

from ingest.models import TrackedObject, parse_3le_text

LOGIN_URL = "https://www.space-track.org/ajaxauth/login"
QUERY_URL = (
    "https://www.space-track.org/basicspacedata/query/class/gp/"
    "EPOCH/%3Enow-30/orderby/NORAD_CAT_ID%20asc/format/3le"
)
TIMEOUT_S = 120


class SpaceTrackAuthError(RuntimeError):
    pass


def _credentials_from_env() -> tuple[str, str]:
    username = os.environ.get("SPACETRACK_USERNAME")
    password = os.environ.get("SPACETRACK_PASSWORD")
    if not username or not password:
        raise SpaceTrackAuthError(
            "SPACETRACK_USERNAME and SPACETRACK_PASSWORD must be set in the environment. "
            "Register a free account at https://www.space-track.org/auth/createAccount "
            "if you don't have one yet."
        )
    return username, password


class SpaceTrackClient:
    """Thin session wrapper. Use as a context manager so login/logout are paired."""

    def __init__(self) -> None:
        self._username, self._password = _credentials_from_env()
        self._session = requests.Session()

    def __enter__(self) -> "SpaceTrackClient":
        resp = self._session.post(
            LOGIN_URL,
            data={"identity": self._username, "password": self._password},
            timeout=TIMEOUT_S,
        )
        if resp.status_code != 200 or "error" in resp.text.lower()[:200]:
            raise SpaceTrackAuthError(f"Space-Track login failed (HTTP {resp.status_code})")
        return self

    def __exit__(self, *exc_info) -> None:
        self._session.get("https://www.space-track.org/ajaxauth/logout", timeout=TIMEOUT_S)
        self._session.close()

    def fetch_full_catalog(self) -> list[TrackedObject]:
        """Latest element set per object across the full unclassified catalog.

        Object type defaults to "debris" here; catalog.py reclassifies entries
        that also appear in CelesTrak's curated active-satellite list as "active".
        """
        resp = self._session.get(QUERY_URL, timeout=TIMEOUT_S)
        resp.raise_for_status()
        objects = parse_3le_text(resp.text, object_type="debris", source="spacetrack")

        # The query can return multiple epochs per object (EPOCH>now-30); keep
        # only the most recent element set per NORAD ID.
        latest: dict[int, TrackedObject] = {}
        for obj in objects:
            existing = latest.get(obj.norad_id)
            if existing is None or obj.epoch > existing.epoch:
                latest[obj.norad_id] = obj
        return list(latest.values())
