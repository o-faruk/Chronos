"""Objects sharing (near-)identical elements -- e.g. ISS + a docked cargo
vehicle tracked under the station's own TLE -- must not show up as a
conjunction. Found by hand-checking a real screening run against CelesTrak's
`stations` group: several docked spacecraft reported 0.000 m miss distance
and 0.000 km/s relative velocity, which is correct physically but not a
collision risk worth reporting.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingest.models import parse_3le_text
from screen.pipeline import run_screening

FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "sample_3le.txt"
EPOCH = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_identical_elements_pair_is_not_reported_as_conjunction():
    [iss] = parse_3le_text(FIXTURE.read_text(), object_type="active", source="celestrak")[:1]
    docked_companion = type(iss)(
        norad_id=99999, name="DOCKED COMPANION", line1=iss.line1, line2=iss.line2,
        object_type="active", source="celestrak",
    )

    run = run_screening([iss, docked_companion], window_start=EPOCH, window_hours=2)

    assert run.conjunctions == []
