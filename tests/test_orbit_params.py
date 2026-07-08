from ingest.models import TrackedObject
from propagate.orbit_params import altitude_km, period_min

# ISS: ~400-420 km altitude, ~92-93 min period -- well-known published values.
ISS = TrackedObject(
    norad_id=25544, name="ISS (ZARYA)", object_type="active", source="celestrak",
    line1="1 25544U 98067A   26188.50835634  .00005806  00000+0  11369-3 0  9990",
    line2="2 25544  51.6304 199.5144 0006687 267.6545  92.3678 15.48933372574901",
)


def test_iss_period_is_plausible():
    p = period_min(ISS)
    assert 90 < p < 95


def test_iss_altitude_is_plausible():
    alt = altitude_km(ISS)
    assert 380 < alt < 430
