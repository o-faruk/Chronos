from ingest.models import TrackedObject
from screen.regime import classify_regime
from screen.severity import classify_severity

ISS = TrackedObject(
    norad_id=25544, name="ISS (ZARYA)", object_type="active", source="celestrak",
    line1="1 25544U 98067A   26188.50835634  .00005806  00000+0  11369-3 0  9990",
    line2="2 25544  51.6304 199.5144 0006687 267.6545  92.3678 15.48933372574901",
)

# GOES-16 (real GEO weather satellite), period ~1436 min.
GEO_SAT = TrackedObject(
    norad_id=41866, name="GOES-16", object_type="active", source="celestrak",
    line1="1 41866U 16071A   26188.50000000  .00000090  00000-0  00000-0 0  9991",
    line2="2 41866   0.0400  90.1000 0000900 100.0000 260.0000  1.00271000 10012",
)

# Molniya-type highly-eccentric orbit (e ~ 0.72).
HEO_SAT = TrackedObject(
    norad_id=8195, name="MOLNIYA 2-14", object_type="debris", source="celestrak",
    line1="1 08195U 75081A   06176.33215444  .00000099  00000-0  11873-3 0   813",
    line2="2 08195  64.1586 279.0717 7118436 264.7651  20.2257  2.00491383225656",
)


def test_classify_regime_leo():
    assert classify_regime(ISS) == "LEO"


def test_classify_regime_geo():
    assert classify_regime(GEO_SAT) == "GEO"


def test_classify_regime_heo_overrides_altitude():
    assert classify_regime(HEO_SAT) == "HEO"


def test_severity_bands_match_locked_thresholds():
    assert classify_severity(199.0) == "critical"
    assert classify_severity(200.0) == "high"
    assert classify_severity(499.0) == "high"
    assert classify_severity(999.0) == "medium"
    assert classify_severity(4999.0) == "low"
    assert classify_severity(5000.0) is None
    assert classify_severity(10000.0) is None
