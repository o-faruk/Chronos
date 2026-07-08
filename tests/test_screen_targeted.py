"""One-object-vs-catalog screening (the custom-TLE-injection differentiator's
actual execution path) must find the same known conjunction the full
pipeline finds, treating one fixture object as the "submitted" object and
the other as the sole catalog entry.
"""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingest.models import parse_3le_text
from screen.targeted import run_targeted_screening

FIXTURE = Path(__file__).resolve().parent.parent / "testdata" / "known_conjunction.txt"
EPOCH = datetime(2026, 7, 7, 12, 0, 0, tzinfo=timezone.utc)


def test_targeted_screening_catches_known_conjunction():
    target, other = parse_3le_text(FIXTURE.read_text(), object_type="debris", source="celestrak")

    run = run_targeted_screening(target, [other], window_start=EPOCH - timedelta(hours=1), window_hours=2)

    assert len(run.conjunctions) == 1
    conj = run.conjunctions[0]
    assert conj.severity == "critical"
    assert abs(conj.miss_distance_m - 30.0) < 1.0
    assert abs((conj.tca - EPOCH).total_seconds()) < 5.0
    assert run.pairs_screened == 1


def test_targeted_screening_skips_identical_elements_pair():
    [target] = parse_3le_text(FIXTURE.read_text(), object_type="debris", source="celestrak")[:1]
    docked_companion = type(target)(
        norad_id=99999, name="DOCKED COMPANION", line1=target.line1, line2=target.line2,
        object_type="active", source="celestrak",
    )

    run = run_targeted_screening(target, [docked_companion], window_start=EPOCH, window_hours=2)

    assert run.conjunctions == []
