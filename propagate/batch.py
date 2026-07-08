"""Vectorized catalog-scale SGP4 propagation.

Uses sgp4.api.SatrecArray, which vectorizes across satellites AND times in
one call (not a per-object Python loop) -- this is the primitive the whole
screening pipeline's performance budget depends on.
"""
from __future__ import annotations

import numpy as np
from sgp4.api import Satrec, SatrecArray

from ingest.models import TrackedObject


class PropagatedCatalog:
    """Result of propagating a catalog across a time grid.

    r, v: shape (n_objects, n_times, 3), km and km/s in TEME frame.
    err: shape (n_objects, n_times), nonzero = SGP4 error code at that sample
    (see sgp4 docs for codes, e.g. decayed orbit); those samples are not
    physically meaningful and should be excluded from screening.
    """

    def __init__(self, catalog: list[TrackedObject], jd: np.ndarray, fr: np.ndarray,
                 err: np.ndarray, r: np.ndarray, v: np.ndarray) -> None:
        self.catalog = catalog
        self.jd = jd
        self.fr = fr
        self.err = err
        self.r = r
        self.v = v


def propagate_catalog(catalog: list[TrackedObject], jd: np.ndarray, fr: np.ndarray) -> PropagatedCatalog:
    satrecs = [Satrec.twoline2rv(o.line1, o.line2) for o in catalog]
    array = SatrecArray(satrecs)
    err, r, v = array.sgp4(jd, fr)
    return PropagatedCatalog(catalog, jd, fr, err, r, v)
