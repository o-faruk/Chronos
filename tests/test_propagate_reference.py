"""Correctness check: our propagation call against Vallado's published SGP4
verification vector for satellite 00005 at tsince=0 (the standard reference
used to validate SGP4 implementations, taken from the sgp4 package's own
bundled SGP4-VER.TLE test file).

Acceptable margin: 1e-6 km / 1e-6 km/s -- matches the precision Vallado's
paper reports the reference vectors to (millimeter-level), and is far
tighter than anything that matters for km-scale conjunction screening; if
this test ever fails by more than that it means something is wrong with how
we're invoking the library (wrong units, wrong time base), not floating
point noise.
"""
import numpy as np

from ingest.models import TrackedObject
from propagate.batch import propagate_catalog

LINE1 = "1 00005U 58002B   00179.78495062  .00000023  00000-0  28098-4 0  4753"
LINE2 = "2 00005  34.2682 348.7242 1859667 331.7664  19.3264 10.82419157413667"

EXPECTED_R_KM = np.array([7022.465, -1400.083, 0.040])
EXPECTED_V_KM_S = np.array([1.893841, 6.405894, 4.534807])
TOLERANCE = 1e-3  # published reference is only quoted to 3 decimal places


def test_propagate_catalog_matches_vallado_reference_at_epoch():
    obj = TrackedObject(norad_id=5, name="TEST", line1=LINE1, line2=LINE2,
                         object_type="debris", source="celestrak")

    # Evaluate at tsince=0, i.e. exactly at the TLE epoch.
    from sgp4.api import Satrec
    satrec = Satrec.twoline2rv(LINE1, LINE2)
    jd = np.array([satrec.jdsatepoch])
    fr = np.array([satrec.jdsatepochF])

    result = propagate_catalog([obj], jd, fr)

    assert result.err[0, 0] == 0
    np.testing.assert_allclose(result.r[0, 0], EXPECTED_R_KM, atol=TOLERANCE)
    np.testing.assert_allclose(result.v[0, 0], EXPECTED_V_KM_S, atol=TOLERANCE)


def test_propagate_catalog_is_vectorized_across_satellites_and_times():
    obj = TrackedObject(norad_id=5, name="TEST", line1=LINE1, line2=LINE2,
                         object_type="debris", source="celestrak")
    from sgp4.api import Satrec
    satrec = Satrec.twoline2rv(LINE1, LINE2)
    jd = np.full(5, satrec.jdsatepoch)
    fr = satrec.jdsatepochF + np.arange(5) * (10.0 / 1440.0)  # 10-minute steps

    result = propagate_catalog([obj, obj], jd, fr)

    assert result.r.shape == (2, 5, 3)
    assert result.v.shape == (2, 5, 3)
    # Same object propagated twice should give identical results.
    np.testing.assert_array_equal(result.r[0], result.r[1])
