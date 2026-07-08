"""Build a unified tracked-object catalog and cache it locally.

Bulk elements come from Space-Track (full catalog); CelesTrak's curated
"active" group is used only to reclassify which of those objects are
operational payloads. Object type beyond that (rocket body vs debris) is
inferred from the object name's "R/B" convention, since Space-Track's GP/3LE
data doesn't carry an explicit OBJECT_TYPE field (that lives in SATCAT,
which we don't otherwise need).

If Space-Track credentials aren't available yet, falls back to a
CelesTrak-only partial catalog (active satellites + named debris clouds) so
Phase 1 development/tests aren't blocked on account approval. This fallback
is deliberately loud (logged), not silent, since it changes catalog size and
composition materially -- see docs/validation.md.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from ingest import celestrak
from ingest.models import TrackedObject
from ingest.spacetrack import SpaceTrackAuthError, SpaceTrackClient

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parent.parent / "data" / "cache"
CACHE_FILE = CACHE_DIR / "catalog.json"

# Named debris clouds CelesTrak actually exposes (see docs/validation.md).
_CELESTRAK_DEBRIS_GROUPS = ["fengyun-1c-debris", "iridium-33-debris", "cosmos-2251-debris"]


def _reclassify(obj: TrackedObject, active_ids: set[int]) -> TrackedObject:
    if obj.norad_id in active_ids:
        object_type = "active"
    elif "R/B" in obj.name.upper():
        object_type = "rocket"
    else:
        object_type = "debris"
    if object_type == obj.object_type:
        return obj
    return TrackedObject(
        norad_id=obj.norad_id,
        name=obj.name,
        line1=obj.line1,
        line2=obj.line2,
        object_type=object_type,
        source=obj.source,
    )


def build_catalog() -> list[TrackedObject]:
    active = celestrak.fetch_active_satellites()
    active_ids = {o.norad_id for o in active}

    try:
        with SpaceTrackClient() as client:
            bulk = client.fetch_full_catalog()
        logger.info("catalog source: space-track full catalog (%d objects)", len(bulk))
        merged = {o.norad_id: _reclassify(o, active_ids) for o in bulk}
        # Space-Track's own copy of active payloads should already be in `bulk`;
        # anything CelesTrak flags active that Space-Track's query missed still
        # gets included so it isn't silently dropped from the active set.
        for obj in active:
            merged.setdefault(obj.norad_id, obj)
        return list(merged.values())
    except SpaceTrackAuthError as exc:
        logger.warning(
            "Space-Track unavailable (%s); falling back to CelesTrak-only partial catalog "
            "(active satellites + %d named debris clouds, NOT the full ~30k catalog)",
            exc, len(_CELESTRAK_DEBRIS_GROUPS),
        )
        merged = {o.norad_id: o for o in active}
        for group in _CELESTRAK_DEBRIS_GROUPS:
            for obj in celestrak.fetch_group(group, object_type="debris"):
                merged.setdefault(obj.norad_id, obj)
        return list(merged.values())


def save_catalog(catalog: list[TrackedObject], path: Path = CACHE_FILE) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    records = [
        {
            "norad_id": o.norad_id,
            "name": o.name,
            "line1": o.line1,
            "line2": o.line2,
            "object_type": o.object_type,
            "source": o.source,
        }
        for o in catalog
    ]
    path.write_text(json.dumps(records, indent=2))


def load_catalog(path: Path = CACHE_FILE) -> list[TrackedObject]:
    records = json.loads(path.read_text())
    return [TrackedObject(**r) for r in records]
