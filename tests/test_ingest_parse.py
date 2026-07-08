from datetime import datetime, timezone
from pathlib import Path

from ingest.models import parse_3le_text, parse_norad_id, parse_tle_epoch
from ingest.catalog import _reclassify

FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "sample_3le.txt"


def test_parse_3le_extracts_norad_id_and_lines():
    objects = parse_3le_text(FIXTURE.read_text(), object_type="debris", source="celestrak")
    assert len(objects) == 3
    iss = objects[0]
    assert iss.norad_id == 25544
    assert iss.name == "ISS (ZARYA)"
    assert iss.line1.startswith("1 25544U")
    assert iss.line2.startswith("2 25544")


def test_parse_tle_epoch_decodes_two_digit_year():
    epoch = parse_tle_epoch("1 25544U 98067A   26188.50835634  .00005806  00000+0  11369-3 0  9990")
    assert epoch.year == 2026
    assert epoch.tzinfo is timezone.utc
    # day-of-year 188 (fractional) in 2026 -> July 7th
    assert epoch.month == 7
    assert epoch.day == 7


def test_parse_3le_strips_spacetrack_line0_sequence_digit():
    text = "0 STARLINK-1007\n1 25544U 98067A   26188.50835634  .00005806  00000+0  11369-3 0  9990\n2 25544  51.6304 199.5144 0006687 267.6545  92.3678 15.48933372574901"
    [obj] = parse_3le_text(text, object_type="active", source="spacetrack")
    assert obj.name == "STARLINK-1007"


def test_parse_norad_id_plain_numeric():
    assert parse_norad_id("25544") == 25544


def test_parse_norad_id_alpha5():
    # T -> 27 (A=10, skipping I and O): 27*10000 + 0 = 270000
    assert parse_norad_id("T0000") == 270000
    # A -> 10: 10*10000 + 1234 = 101234
    assert parse_norad_id("A1234") == 101234


def test_reclassify_uses_active_set_and_rb_naming():
    objects = parse_3le_text(FIXTURE.read_text(), object_type="debris", source="spacetrack")
    iss, vallado_sat, rocket = objects
    active_ids = {iss.norad_id}

    assert _reclassify(iss, active_ids).object_type == "active"
    assert _reclassify(vallado_sat, active_ids).object_type == "debris"
    assert _reclassify(rocket, active_ids).object_type == "rocket"
