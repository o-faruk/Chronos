# Hand-constructed known-conjunction fixture

`known_conjunction.txt` (3LE, 2 objects) is synthetic, built to have a
precomputed, independently-verifiable close approach -- not derived from the
screening pipeline itself, so it's a real regression check rather than a
tautology.

## Construction

Both objects: circular-ish orbit (e = 0.0001), inclination 51.6 deg, RAAN 0,
argument of perigee 0, mean anomaly 0, same epoch (2026-07-07T12:00:00Z).
Only the altitude differs:

- FIXTURE-A: 550.000 km altitude
- FIXTURE-B: 550.030 km altitude (30 m higher)

Same inclination/RAAN/argp/mean-anomaly-at-epoch means both objects are at
the same angular position in the same orbital plane at every instant (to the
two-body approximation TLEs encode) -- the *only* separation is radial,
equal to the altitude difference. So at epoch (t=0):

```
expected miss distance = 30 m  (critical band, <200 m)
```

Because the two altitudes give slightly different mean motions (Kepler's
third law), the along-track phase drifts apart afterward at a computable
rate of ~2.95 m/min (`(mean_motion_A - mean_motion_B) * semi_major_axis`,
converted to linear rate). Verified numerically:

| t (min from epoch) | miss distance |
|---|---|
| 0 | 30.0 m |
| 10 | 42.1 m |
| 30 | 93.7 m |
| 60 | 179.8 m |
| 95.65 (~1 period) | 284.5 m |

So epoch is the true minimum within any window containing it, and the
regression test (`tests/test_screen_fixture.py`) asserts the pipeline
recovers TCA ~= epoch and miss distance ~= 30 m (critical severity).
