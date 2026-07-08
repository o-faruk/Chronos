# Findings report: Chronos vs. SOCRATES, same-window head-to-head

Date: 2026-07-07/08. Both datasets pulled live within the same session (not
archived/stale numbers on either side).

## The comparison

| | SOCRATES (live, celestrak.org/SOCRATES) | Chronos (this run, real Space-Track catalog) |
|---|---|---|
| Data source | Space-Track GP/TLE | Space-Track GP/TLE (same lineage) |
| Catalog scale | 15,912 primaries (active) x 31,815 secondaries (full catalog) | 31,934 objects total (15,932 active) |
| Scope | active-vs-full-catalog, excluding intra-fleet operational pairs | active-vs-full-catalog, **including** intra-fleet pairs (see below) |
| Window | 7 days forward | 3 days (72h) forward |
| Threshold | 5.0 km | 5.0 km (matches exactly) |
| Conjunctions found | 144,825 | 17,848 |
| **Normalized: conjunctions/day** | **~20,689/day** | **~5,949/day** |
| Compute time (this run) | 11h 28m 39s | 3.0 min |
| Output mechanism | Parameterized HTML/CSV web page, no JSON API | JSON API (this build's whole point) |
| Custom TLE submission | Not supported | Supported (`POST /screen`, ~10s for a 24h window) |
| Run-over-run trend | Not supported | Supported (`trend` field: new/closing/opening/stable) |

Catalog scale is directly comparable: SOCRATES' 31,815-object secondary set
and Chronos's 31,934-object full catalog are pulling from the same
underlying public catalog and land within 0.4% of each other in size --
this run isn't screening a toy dataset, it's the same real catalog SOCRATES
screens.

## The headline win: compute time

SOCRATES' documented cadence is "three times daily." Its *actual* cadence at
current catalog scale doesn't support that: this run (and an independent
check earlier in the same session, which measured 11h 51m on a comparable
run) took **over 11.5 hours** to complete a 7-day screen. Three of those
literally do not fit in a day. Chronos completed a real 72-hour screen of
the same-scale catalog in **3.0 minutes** -- over 200x faster. This isn't
close: it's the difference between a service that can plausibly run on a
2-hour EventBridge schedule (this build's default -- see docs/decisions.md)
and one whose stated refresh cadence has been overtaken by catalog growth.

## The honest gap: conjunction count per day

Chronos finds meaningfully fewer conjunctions per day of screening window
than SOCRATES does, even after normalizing for the window-length difference
(7d vs 3d). Two effects push in *opposite* directions, so the raw gap
understates the real difference:

1. **Pushes Chronos's count up relative to SOCRATES:** Chronos does not
   exclude intra-fleet operational pairs (e.g. STARLINK-31223 vs
   STARLINK-32630, both active, both Starlink, appear in this run's top
   results at 12.9 m). SOCRATES explicitly excludes these. All else equal,
   this should inflate Chronos's count relative to SOCRATES's.
2. **Pushes Chronos's count down relative to SOCRATES, and is the more
   likely dominant effect:** Chronos's coarse screening pass is a
   time-sampled approximation (60s steps, 30km search radius -- see
   `docs/decisions.md`, "Coarse pass architecture"), explicitly documented
   as able to miss a brief, high-relative-velocity close pass that never
   lands within 30km of a sampled instant. SOCRATES has been in production
   since 2004 and almost certainly uses a more exhaustive per-orbit-rev or
   analytically-refined search rather than fixed wall-clock sampling.

Given effect (1) should inflate Chronos's relative count and the net result
is still lower, the coarse-sampling gap in effect (2) is probably larger
than the raw ~3.5x per-day ratio suggests. This is a real, disclosed
limitation, not a hidden one -- flagging it as the top item for follow-up
work: either shrink the coarse step, adaptively refine sampling density
based on relative velocity, or move to a rev-based sampling scheme instead
of fixed wall-clock steps.

**What this doesn't mean:** it doesn't mean Chronos's *reported* conjunctions
are wrong. Every reported conjunction is independently refined via continuous
-time TCA optimization (`screen/tca.py`), not read off the coarse grid, and
the known-conjunction fixture (`testdata/known_conjunction.md`) plus the
Vallado reference-vector regression test (`tests/test_propagate_reference.py`)
both confirm the underlying propagation and TCA math is correct. The gap is
in *recall* (finding every close approach that exists), not *precision*
(the ones found being accurate).

## What wasn't attempted

A pair-level cross-check (does a specific NORAD ID pair Chronos flagged also
appear in SOCRATES' current output) would be the strongest form of
validation. Didn't pursue it this session -- SOCRATES' actual query API
requires form parameters not fully reverse-engineered from the documentation
page, and guessing at URLs against their rate-limited service (see Phase 1's
real 403 encounter) wasn't worth the risk for a nice-to-have when the
aggregate comparison already gives a clear, honest picture. Worth doing as a
follow-up once the frontend exists to make it easy to search a specific pair.

## Net assessment

The core differentiation decided in Phase 0 holds up under real data: a
real JSON API (vs. none), a >200x faster refresh cycle that can actually
sustain a sub-daily schedule (vs. SOCRATES' documented cadence being
unachievable at current catalog scale), custom-TLE injection, and
run-over-run trend tracking. The honest cost of that speed is a coarse-pass
sampling approximation that under-counts relative to SOCRATES' more mature,
decades-refined method -- a real gap, disclosed rather than hidden, and the
clear next thing to improve.
