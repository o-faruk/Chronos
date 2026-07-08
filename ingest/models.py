"""Shared record types for tracked objects and 3LE parsing."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone


# Closed enum matching the frontend's type badges (active/debris/rocket colors
# are already hardcoded in the mockup UI's typeColor()).
OBJECT_TYPES = ("active", "debris", "rocket")


@dataclass(frozen=True)
class TrackedObject:
    norad_id: int
    name: str
    line1: str
    line2: str
    object_type: str  # one of OBJECT_TYPES
    source: str  # "celestrak" or "spacetrack"

    def __post_init__(self) -> None:
        if self.object_type not in OBJECT_TYPES:
            raise ValueError(f"unknown object_type {self.object_type!r} for {self.norad_id}")

    @property
    def epoch(self) -> datetime:
        return parse_tle_epoch(self.line1)


# Alpha-5: as of July 2026 catalog numbers above 99999 encode the
# ten-thousands digit as a letter (I and O skipped to avoid confusion with
# 1 and 0) in the still-5-character NORAD ID field, e.g. "T0000" -> 270000.
# See space-track.org's Alpha-5 announcement (live now, not a future concern
# -- hit this parsing real Space-Track data on 2026-07-07).
_ALPHA5_LETTERS = "ABCDEFGHJKLMNPQRSTUVWXYZ"
_ALPHA5_VALUES = {letter: 10 + i for i, letter in enumerate(_ALPHA5_LETTERS)}


def parse_norad_id(field: str) -> int:
    """Decode a 5-character TLE catalog-number field, Alpha-5 aware."""
    leading = field[0]
    if leading.isalpha():
        return _ALPHA5_VALUES[leading.upper()] * 10000 + int(field[1:])
    return int(field)


def parse_tle_epoch(line1: str) -> datetime:
    """Decode the TLE epoch (columns 19-32: YYDDD.DDDDDDDD) to a UTC datetime.

    TLE epoch years are two digits; per the standard convention, 57-99 -> 1957-1999,
    00-56 -> 2000-2056 (the format predates Y2K and this is the accepted rollover).
    """
    yy = int(line1[18:20])
    day_of_year_frac = float(line1[20:32])
    year = 1900 + yy if yy >= 57 else 2000 + yy
    return datetime(year, 1, 1, tzinfo=timezone.utc) + timedelta(days=day_of_year_frac - 1)


def parse_3le_text(text: str, object_type: str, source: str) -> list[TrackedObject]:
    """Parse a CelesTrak/Space-Track FORMAT=3LE response into TrackedObjects.

    3LE = name line, then the standard two TLE lines, repeated per object.
    """
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if len(lines) % 3 != 0:
        raise ValueError(f"expected 3LE text in groups of 3 lines, got {len(lines)} non-blank lines")

    objects = []
    for i in range(0, len(lines), 3):
        # Space-Track's 3LE name line carries the standard "line 0" leading
        # sequence digit (e.g. "0 STARLINK-1007"); CelesTrak's 3LE omits it.
        # Strip it either way so display names are consistent across sources.
        name = re.sub(r"^\d+\s+", "", lines[i].strip())
        line1 = lines[i + 1].strip()
        line2 = lines[i + 2].strip()
        if not line1.startswith("1 ") or not line2.startswith("2 "):
            raise ValueError(f"malformed 3LE record at line {i}: {name!r}")
        norad_id = parse_norad_id(line1[2:7])
        objects.append(TrackedObject(
            norad_id=norad_id,
            name=name,
            line1=line1,
            line2=line2,
            object_type=object_type,
            source=source,
        ))
    return objects
