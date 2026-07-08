# Decision log

One entry per non-obvious call. See `docs/validation.md` for the Phase 0 SOCRATES
gap analysis and differentiation decisions (JSON API, custom TLE injection,
run-over-run trend tracking; debris-vs-debris was considered and dropped).

## TLE sources: Space-Track (bulk) + CelesTrak (active list only)

CelesTrak's free GP API doesn't expose a full-catalog download -- only curated
groups (active satellites, named constellations) and 3 named debris clouds.
Space-Track has the full unclassified catalog via one bulk GP query but
requires a manually-approved account. Decision (with user, 2026-07-07):
Space-Track is the bulk source for the full ~30k-object catalog;
CelesTrak's curated "active" group is kept and used only to reclassify which
Space-Track entries are operational payloads (Space-Track's GP/3LE data has
no OBJECT_TYPE field of its own). `ingest/catalog.py` falls back to a
CelesTrak-only partial catalog (active + 3 debris clouds) if Space-Track
credentials aren't set, so development isn't blocked on account approval --
this fallback logs loudly rather than silently changing catalog composition.

**Status (updated 2026-07-07):** Space-Track account approved and live.
`fetch_full_catalog()` returns 31,934 objects in ~2.4s; `build_catalog()`
(Space-Track bulk + CelesTrak active-list reclassification) completes in
~11.4s end to end: 15,932 active / 13,842 debris / 2,160 rocket. See the
"Real catalog integration" section below for what broke on first contact
with live data and how it was fixed.

## Object type classification (active / debris / rocket)

Neither CelesTrak's nor Space-Track's GP/3LE data carries an explicit
"is this active" flag or rocket-body/debris split by itself. Classification
heuristic used in `ingest/catalog.py._reclassify`:
1. NORAD ID appears in CelesTrak's curated "active" group -> `active`
2. else object name contains "R/B" (the standard NORAD catalog naming
   convention for rocket bodies, visible in real data e.g. "SL-16 R/B") -> `rocket`
3. else -> `debris`

This is a naming-convention heuristic, not a ground-truth field, so it will
misclassify the rare object that doesn't follow the convention. Acceptable
given the frontend only needs 3 badge colors, not certainty.

## CelesTrak rate limiting is real and was hit during development

Fetching `GROUP=active` twice in short succession (once via a manual `curl`
check, once via `build_catalog()`) produced an HTTP 403 -- CelesTrak enforces
"one download per group per 2-hour update cycle" aggressively, not as a soft
guideline. Implication for the scheduled EventBridge run (Phase 3): the
active-list refresh must be cached and reused across a run, not re-fetched
per invocation, and definitely not re-fetched if a Lambda retries.

## Propagation primitive: `sgp4.api.SatrecArray`

The base `Satrec.sgp4_array(jd, fr)` vectorizes one satellite across many
times; it does not vectorize across satellites. `SatrecArray(satrecs).sgp4(jd, fr)`
vectorizes across **both** satellites and times in a single call, returning
`(err, r, v)` shaped `(n_objects, n_times)` / `(n_objects, n_times, 3)`. This
is the primitive `propagate/batch.py` builds on -- catalog-scale propagation
is one call, not a per-object Python loop.

## Correctness check

Two layers, per the Phase 1 definition of done:
1. **Deterministic regression** (`tests/test_propagate_reference.py`): our
   propagation call reproduces Vallado's published SGP4 verification vector
   for satellite 00005 at tsince=0 (`r=[7022.465, -1400.083, 0.040] km`,
   `v=[1.893841, 6.405894, 4.534807] km/s`) to 1e-3 km/km-s. This is the
   standard reference used to validate SGP4 ports generally, bundled with
   the `sgp4` package itself as `SGP4-VER.TLE`. Confirms we're invoking the
   library correctly (units, time base), not re-deriving SGP4 math ourselves.
2. **Live ISS sanity check** (`tests/test_propagate_iss_live.py`): fetches
   the current ISS TLE from CelesTrak and confirms propagated altitude
   (380-450 km) and speed (7.4-7.9 km/s) fall in the well-known published
   range for ISS. Skips (doesn't fail) if CelesTrak is unreachable, so CI
   isn't hostage to an external service.

**Acceptable margin:** 1e-3 km against the Vallado reference (matches the
precision the published vector itself is quoted to) -- far tighter than
matters for km-scale conjunction screening. For the live ISS check, a wide
physical-plausibility band rather than a tight numeric match, since there's
no fixed "true" position to diff against without also fetching a second
independent ephemeris source.

## Full-catalog propagation timing (Phase 1 definition of done)

Measured 2026-07-07: **26.3 seconds** wall-clock to propagate a 30,142-object
catalog across a 72-hour window at 60-second resolution (4,321 time samples,
~130.2M object-samples total), zero SGP4 error codes.

Caveat: Space-Track access isn't available yet, so this run used a synthetic
catalog -- 20 real TLEs (fetched individually via CelesTrak `CATNR`, spanning
LEO/MEO/GEO) replicated to 30,142 objects with jittered RAAN/mean-anomaly so
`SatrecArray` does distinct work per object rather than repeating identical
math. Propagation cost is per-object-per-time-sample and doesn't depend on
which specific elements are loaded, so this number should hold once the real
Space-Track catalog is wired in; it should be re-measured once that happens
to confirm rather than assumed identical.

26.3s comfortably fits Lambda's 15-minute hard limit with a lot of headroom
left for the screening pass itself (Phase 2) -- no evidence yet that Lambda
is the wrong runtime call from the original prompt. Will revisit after
Phase 2's coarse+fine screening timing is measured, since that (not raw
propagation) is more likely to be what actually pressures the time budget.

---

# Phase 2 — conjunction screening

## Coarse pass architecture: regime bucketing + per-timestep cKDTree, not a shell sweep

Considered a pure orbital-shell (perigee/apogee interval overlap) sweep as
the sole coarse filter. Rejected as the *only* filter because, at real
catalog scale, most objects are LEO and their altitude shells overlap each
other broadly (400-2000 km is a big shared band) -- a shell-only filter
wouldn't shrink the ~21k-object LEO bucket much below O(n^2) pairs.

Landed on: (1) bucket by regime (`screen/regime.py` -- LEO/MEO/GEO by period,
HEO by eccentricity overriding the other two, since it's the field the
frontend already needs for `regimeRows`), with HEO objects riding along in
every other bucket (they can dip through any altitude near perigee); (2)
propagate each bucket at coarse resolution and, per time sample, build a
`scipy.spatial.cKDTree` and call `.query_pairs(r)` directly -- this is the
part that actually avoids ever enumerating all N^2 pairs, since query_pairs
is a spatial query, not a scan. Candidate pairs are the union across all
time samples, keyed by (first, last) flagged time to bound the fine-search
window.

**Coarse defaults:** 60 s step, 75 km radius (15x the 5 km "low" severity
cutoff). **Known, bounded limitation:** this is snapshot sampling, not
continuous coverage -- a pair closing at max relative velocity (~15 km/s,
opposite-direction LEO crossing) can move ~900 km between two 60 s samples,
so in principle a very brief, radially-aligned close pass that never gets
within 75 km of either sampled endpoint could be missed. This is the same
category of risk any snapshot-based screening system accepts (SOCRATES
itself screens on a similarly discrete cadence); the generous radius-to-step
ratio is the mitigation, not a guarantee. Flagging honestly rather than
overclaiming exhaustiveness.

## Fine screening: dense grid + bounded Brent refinement, not further sampling

`screen/tca.py` refines each candidate pair's TCA by evaluating a coarse
grid across the flagged window (guards against `minimize_scalar` locking
onto the wrong local minimum when a pair has multiple close passes in the
window), then runs `scipy.optimize.minimize_scalar` (bounded/Brent) around
the best grid point. This gives a continuous-time miss distance rather than
one quantized to whatever the coarse step happened to sample.

## Real finding: docked/berthed spacecraft produce false-positive "conjunctions"

Ran the pipeline against a real CelesTrak sample (`stations` + `gps-ops` +
`iridium-NEXT` + `fengyun-1c-debris`, 2,054 objects) as the Phase 2
plausibility check. Top hits were things like CSS (MENGTIAN) vs TIANZHOU-10,
ISS (NAUKA) vs CYGNUS NG-24, at exactly 0.000 m / 0.000 km/s. Root cause:
CelesTrak publishes docked/berthed spacecraft under their own NORAD ID but
copies the station's own orbital elements (inclination/RAAN/eccentricity/
argp/mean-anomaly/mean-motion identical; only the revolution-number and
checksum trailer differ) since the docked vehicle isn't independently
tracked. That's a shared-tracking artifact, not a conjunction.

Considered filtering on relative velocity instead (docked objects have ~0
relative velocity) but rejected it: the hand-constructed known-conjunction
fixture (`testdata/known_conjunction.md`) is a *genuine* close, independently
-orbiting pair with a relative velocity of only ~1.6 cm/s at TCA (nearly
identical orbital planes, tiny altitude difference) -- a relative-velocity
threshold would have silently dropped that real case too, which is actually
the more operationally dangerous scenario (small relative velocity means a
long dwell time at close range, not just an instantaneous crossing).
Landed on comparing the parsed orbital elements themselves
(`screen/pipeline.py:_orbital_elements`, line2 cols 9-63, i.e. everything
except the NORAD ID and the revolution-number/checksum trailer): if two
objects' elements match, skip the pair before fine screening even runs.
Regression test: `tests/test_screen_docked_filter.py`.

After the fix, the same real sample dropped from 479 to 439 conjunctions
(the 39-40 pair difference all being docked-complex artifacts) and the
remaining list is dominated by Fengyun-1C debris fragments passing each
other at 1-15 km/s relative velocity, with a plausible Iridium-vs-Fengyun
-debris hit in the mix. This matches well-documented reality: the 2007
Fengyun-1C ASAT test is one of the most prolific conjunction-generating
debris clouds in LEO, so a real catalog's top hits skewing toward
Fengyun-1C-vs-Fengyun-1C is exactly what's expected, not a red flag.

## Known-conjunction regression fixture

`testdata/known_conjunction.txt` + `.md`: two synthetic circular LEO orbits,
identical inclination/RAAN/argp/mean-anomaly-at-epoch, differing only by 30 m
of altitude -- so the only separation at epoch is radial (30 m exactly, by
construction, independently verifiable by hand), growing afterward as the
tiny mean-motion difference drifts the along-track phase apart (~2.95 m/min,
also hand-computable from Kepler's third law). `tests/test_screen_fixture.py`
asserts the full pipeline recovers TCA within 5 s of epoch and miss distance
within 1 m of the predicted 30 m, at `critical` severity. This is the
fixture that would fail if coarse bucketing, the KD-tree pass, or TCA
refinement broke.

## Full-catalog screening timing: real sample is trustworthy, synthetic 30k stress test is not

The real 2,054-object sample (see above) ran the full pipeline (coarse +
fine) in **13.6 s** for a 72-hour window, producing 439 conjunctions. Treating
this as the trustworthy Phase 2 timing/plausibility result.

Also tried extending Phase 1's synthetic-30,142-object catalog (20 real TLEs
replicated with jittered RAAN/mean-anomaly) through the coarse pass alone:
**116 s** and **1.85 million** candidate pairs for just the LEO bucket
(21,100 objects) over a 24-hour window -- and that candidate count is not
credible as a full-catalog estimate. Root cause: the replication scheme only
varies orbital *phase* (RAAN/mean anomaly) while holding each clone's
altitude and inclination fixed to one of 20 discrete values, so ~1,500
clones per base orbit are confined to the same thin spherical shell and
cross each other far more often than a real catalog would, where thousands
of distinct satellites spread continuously across the full LEO altitude and
inclination range. This artifact is specific to *coarse-pass candidate
density* -- Phase 1's propagation-only timing benchmark isn't affected by it
(propagation cost is per-object-per-sample and doesn't care about spatial
clustering), but screening cost does depend on real spatial distribution.

**Decision:** don't publish the 116 s/1.85M-candidate number as a full-catalog
performance estimate -- it's known to be pessimistically biased by the
synthetic clustering artifact above. Full-catalog Phase 2 timing needs to be
re-measured once Space-Track access lands and the real ~30k-object catalog
is available; the 13.6 s/2,054-object real-data run is the trustworthy
number until then.

---

# Real catalog integration (2026-07-07) — what broke on first contact

Space-Track access landed. Three real issues surfaced immediately on live
data that no synthetic/partial-catalog testing had caught:

## Alpha-5 catalog numbers (hit live, not hypothetical)

Flagged as a heads-up earlier from a banner on space-track.org; it stopped
being a "heads-up" and became a crash within the hour. `int(line1[2:7])`
choked on `T0000` -- as of July 2026 catalog numbers above 99999 encode the
ten-thousands digit as a letter (A-Z, skipping I/O to avoid confusion with
1/0) in the still-5-character NORAD ID field. Fixed with
`ingest/models.py:parse_norad_id`, which decodes the letter when present
(`T0000` -> 270000) and falls back to plain `int()` otherwise. Regression
tests in `tests/test_ingest_parse.py`.

## Space-Track 3LE names carry a stray line-0 sequence digit

Space-Track's 3LE name line is `"0 STARLINK-1007"` (the literal TLE spec's
line-0 sequence digit); CelesTrak's 3LE output omits it (`"STARLINK-1007"`).
Not caught by earlier CelesTrak-only testing since the discrepancy only
exists between sources. Fixed by stripping a leading `\d+\s+` in
`parse_3le_text` -- safe for both sources since CelesTrak names never
started with a bare digit+space to begin with.

## Coarse radius default was tuned against the wrong catalog shape

The 75 km default (set during Phase 2, justified only against the
synthetic/partial-catalog tests available then) produced 1.6M candidate
pairs and an estimated ~12 minute fine-screening stage for just a 24-hour
window on the real ~32k catalog -- would blow past Lambda's 15-minute limit
at the target 72-hour window. Measured the actual sensitivity on live data
before picking a fix:

| coarse radius | candidates (24h) | coarse pass | est. fine stage |
|---|---|---|---|
| 75 km (old default) | 1,618,004 | ~28s | ~11.8 min |
| 50 km | 631,561 | ~29s | ~4.6 min |
| 30 km | 189,732 | ~28s | ~1.4 min |
| 20 km | 64,599 | ~28s | ~0.5 min |
| 10 km | 8,837 | ~28s | ~0.1 min |

(Fine-screening measured directly at 0.44 ms/candidate over a 500-candidate
sample, not estimated.) Also checked whether Starlink's intra-fleet
proximity (10,719 of 15,932 active objects are Starlink) was the driver, on
the theory it might need a SOCRATES-style intra-fleet exclusion --
it wasn't: only 3.3% of the 75km candidates were same-constellation pairs.
The volume is just organic catalog density at LEO altitudes, not a single
identifiable cluster to filter out.

**New default: 30 km** (`screen/coarse.py:DEFAULT_COARSE_RADIUS_KM`) --
still 6x the 5 km "low" severity reporting cutoff, comfortable margin, and
keeps a full 72-hour run in the low single-digit minutes rather than risking
Lambda's timeout. Coarse-pass cost itself (~28s/24h window) is essentially
flat across radius choices, since it's dominated by propagation + per-step
tree construction, not query result size -- only the fine stage benefits
from tightening the radius.

Full-catalog (31,934 real objects) 72-hour run at the new 30km coarse
radius: **3.2 minutes**, 21,561 conjunctions -- comfortably inside Lambda's
15-minute limit, and this is real data, not a synthetic proxy. (Re-ran after
the name-prefix fix below; count moved by 28 between runs purely because
`window_start` is `datetime.now()` each time -- expected run-to-run
variance, not instability in the pipeline.)

## Known limitation: independently-fit docked/formation pairs aren't reliably filterable

Spot-checking the real run's top (smallest-miss-distance) results surfaced
CSS (TIANHE-1) vs TIANZHOU 10, CSS modules vs each other, and TerraSAR-X vs
TanDEM-X at ~40 m and near-zero relative velocity -- all physically
docked/rigidly-formation-flying pairs, same category as the `stations`-group
finding earlier, but the existing exact-orbital-elements filter didn't catch
them. Investigated why:

Unlike CelesTrak (which literally copies the station's element set onto
berthed vehicles), Space-Track fits each object's TLE independently, even
when it's physically attached to something else. CSS (TIANHE-1)'s and
TIANZHOU 10's elements differ by up to ~1.5 deg in RAAN/argp/mean-anomaly --
nowhere near an exact-match filter, or even a loose numeric-tolerance one.

Tried a physics-based fix: check whether relative velocity stays small away
from TCA too (a rigidly-docked pair should stay near-zero indefinitely; a
merely-slow-drifting-but-genuinely-separate pair, like the known-conjunction
fixture, should show relative velocity growing measurably over a period of
hours). Measured both:

| pair | rel. v @ TCA | rel. v @ TCA+6h |
|---|---|---|
| known-conjunction fixture (genuine, 30m alt. diff.) | 0.017 m/s | 1.16 m/s |
| CSS (TIANHE-1) vs TIANZHOU 10 (docked, independently fit) | 0.15 m/s | 0.40 m/s |

The fixture's relative velocity grows about 3x faster than the docked pair's
over the same window, which *is* a real physical difference -- but the two
ranges overlap enough (both sub-1.2 m/s at +6h) that no single threshold
reliably separates them without risk of also suppressing a genuine,
currently-slow-but-real conjunction (which is arguably the more dangerous
case to miss, per the earlier docked-filter design note). Concluded this
isn't a heuristic worth adding: the underlying signal is too weak, and a
mis-tuned threshold trades a cosmetic false positive for a silent false
negative on a real hazard, which is the wrong trade.

**Decision: don't try to auto-filter this case.** It's left in the output,
same as SOCRATES would need external constellation/formation metadata (which
neither of us has) to exclude know formations properly. `relative_velocity_kms`
is already a first-class field in the API response specifically so a
consumer (the frontend, or a future exclusion list keyed on NORAD ID pairs)
can de-emphasize or filter near-zero-relative-velocity entries themselves,
rather than the backend silently guessing and risking a real miss. Flagging
this as a real, disclosed limitation rather than pretending it's solved.

---

# Phase 3 — API + deployment

## Infra choice: AWS SAM

Went with SAM (`infra/template.yaml`) over CDK or raw Terraform, per the
build brief's "your call, but document why": this is a small, self-contained
serverless stack (3 Lambdas, 3 DynamoDB tables, 1 S3 bucket, 1 HTTP API,
1 EventBridge schedule) with no cross-stack or multi-cloud needs -- SAM's
policy-template shorthand (`DynamoDBCrudPolicy`, `S3ReadPolicy`, etc.) keeps
IAM least-privilege without hand-writing every `Action`/`Resource` pair, and
`sam build --use-container` + `sam deploy --guided` is the lowest-friction
path to a working deploy for a project this size. CDK's extra abstraction
layer isn't earning its keep at this scope; Terraform's state-file
management is overhead this project doesn't need. Matches the brief's
instruction to lean on the same serverless pattern as the Recall project.

## DynamoDB table design: three tables, one per access pattern

Not one general-purpose table. Each table exists because of a specific read
pattern, not because "more tables = more normalized":

- `ChronosRunsTable` (PK `run_id`, plus a fixed `"LATEST"` pointer item
  overwritten each run) -- "what's the current run" is a single `get_item`,
  not a query/scan-and-sort-by-timestamp.
- `ChronosConjunctionsTable` (PK `run_id`, SK `conjunction_id`) -- "give me
  all conjunctions for this run" is a single `query`, not filtered from a
  larger table.
- `ChronosPairHistoryTable` (PK `pair_key`, one item per pair, overwritten
  each run) -- the trend differentiator only ever needs "what was this
  pair's value *last* run," never a full history, so this deliberately
  doesn't keep history; it's a rolling last-value cache, which keeps trend
  computation O(1) per pair instead of a range query over run history.

Considered a single table with composite keys (the usual DynamoDB
single-table-design advice) and rejected it here: the three access patterns
don't share a partition/sort key shape cleanly (run-scoped vs pair-scoped),
and at Chronos's actual scale (tens of thousands of items, not the
millions-of-items case single-table design is optimizing for) three small
tables are easier to reason about than one table with three different key
overloadings.

Conjunctions are written via `batch_writer()` in batches of 25 (DynamoDB's
`BatchWriteItem` hard limit) -- with ~21.5k real conjunctions per run (see
Phase 2), that's ~860 batched calls per scheduled run, not 21.5k individual
`put_item` round-trips.

## Custom-TLE endpoint: dedicated targeted-screening path, not a shortcut through the full pipeline

First cut of `screen_custom_object_handler` just appended the submitted
object to the catalog and called the existing `run_screening()`. Caught
before shipping it: that still pays the full regime-bucketed coarse pass
over the *entire* catalog (the ~3.2 min full-run cost from Phase 2), because
`run_screening`'s coarse pass treats every object as a potential primary --
adding one more object to a 32k-object bucket doesn't make that bucket's
`cKDTree.query_pairs()` any cheaper. That defeats the entire point of an
on-demand endpoint, and more concretely, doesn't fit in API Gateway's
Lambda-proxy integration timeout, which is a hard 29 seconds and not
configurable via any setting.

Built `screen/targeted.py` instead: propagate the whole catalog once
(unavoidable), then check the *one* submitted object's distance to every
other object per time sample with a single vectorized subtraction
(`catalog_positions - target_position`, O(catalog_size) per sample), instead
of an all-pairs spatial index query. Measured against the real ~32k-object
catalog:

| window | wall-clock |
|---|---|
| 72h (matches the scheduled run's window) | 32.1s -- over the 29s API Gateway limit |
| 24h | 10.1s -- comfortable margin |

**Decision: the on-demand endpoint defaults to a 24-hour window**, shorter
than the scheduled run's 72h, specifically because of the API Gateway
constraint (`CUSTOM_SCREEN_WINDOW_HOURS` in `api/handlers.py`). Verified
correctness two ways: the known-conjunction fixture recovers the same 30m/
critical result through the targeted path as through the full pipeline
(`tests/test_screen_targeted.py`), and a real object already known to have a
conjunction from the Phase 2 full run (STARLINK-34087) is recovered
identically (18.6m) through the targeted path at 72h -- confirming the
targeted math matches the full pipeline's, not just that it's faster.

## Secrets: SSM Parameter Store, not plaintext Lambda environment variables

Lambda environment variables are visible in plaintext to anyone with
`lambda:GetFunctionConfiguration` on the function (console or API) --
adequate for non-secret config (table names, bucket names) but not
credentials. `api/secrets.py` fetches Space-Track's username/password from
SSM Parameter Store (username as `String`, password as `SecureString`) at
cold start and populates them into the process environment once per
container lifetime, so `ingest/spacetrack.py` (already env-var-based and
already tested -- see Phase 1) needs no AWS-specific changes. The actual
parameter values are never in the template or the repo; `infra/README.md`
has the `aws ssm put-parameter` commands to run once, out-of-band.

## EventBridge schedule: every 2 hours

Matches CelesTrak/Space-Track's own GP-data refresh cadence (~2h, per
CelesTrak's documented policy -- see Phase 1) -- screening more often than
the underlying elements change doesn't add information, only cost. Also a
real, measured improvement over SOCRATES' effective cadence: their
documented "3x/day" doesn't hold at current catalog scale (a live check in
Phase 0 showed an 11h51m runtime for one run), while Chronos's full run
measures at 3.2 minutes end-to-end against the real catalog (Phase 2) --
2-hourly is comfortably achievable where SOCRATES' own real-world cadence
isn't matching its stated one.

## Not done: actual deployment

Everything above is infra-as-code and unit-tested (DynamoDB/S3/SSM via
`moto`, no real AWS account touched) but **not deployed**. Deploying creates
real, billed AWS resources under the user's account -- that's the user's
call to make and the user's credentials to run it with, not something to do
unilaterally. `infra/README.md` has the exact steps.

---

# Wiring the mockup UI to real data

The mockup at `Chronos Orbital Conjunction System/Chronos Console.dc.html`
was the user's own design prototype, explicitly out of scope per the
original brief ("I'm designing and building that myself"). Wiring it up was
a later, explicit request, so this is additive, not a scope violation of the
earlier boundary.

## No-AWS local dev server, not a shortcut backend

`dev/local_server.py` runs the *real* `api/handlers.py` Lambda handler
functions locally, with `moto` mocking DynamoDB/S3 in-process -- it's the
actual production code path under test, not a simplified parallel
implementation. First run screens the real ~32k-object catalog (~3 min,
same number as Phase 2/3) and caches the JSON response to
`data/cache/local_run_cache.json` so subsequent restarts are instant;
`--refresh` forces a new run. CORS is wide open (`Access-Control-Allow-Origin: *`)
since this only ever binds to localhost.

## New fields added to wire real UI slots that had no backend source yet

The mockup had UI slots the original locked schema didn't cover: approach
angle, per-conjunction altitude, orbital regime per conjunction, a
candidates-screened count, and a run id. Rather than fake these client-side,
computed them for real and added them to the schema (flagging per the
"add fields, don't rename" rule):

- `approach_angle_deg` -- angle between the two objects' velocity vectors at
  TCA (arccos of the normalized dot product), computed in `screen/tca.py`
  alongside the existing miss-distance/relative-velocity calculation since
  it needs the same velocity vectors already in hand. Standard conjunction-
  assessment metric (0deg = co-moving, 180deg = head-on).
- `altitude_km` (per conjunction) -- object_a's actual altitude *at TCA*
  (`|r| - R_earth`), not the catalog's static circular-equivalent altitude --
  more accurate for a specific event.
- `regime` (per conjunction and per catalog_snapshot entry) -- reuses
  `screen/regime.classify_regime`, already built for the coarse pass.
- `candidates_screened`, `run_id` on `screening_run` -- both were already
  computed/derivable internally (coarse candidate count, a timestamp-derived
  id) but not previously exposed.

## Regression coverage for the new physical fields

Verified `approach_angle_deg` and `altitude_km` against the known-conjunction
fixture, not just eyeballed: the fixture's two objects are co-planar with
matching mean anomaly at epoch (see testdata/known_conjunction.md), so at
TCA they're moving in essentially the same direction -- expect
`approach_angle_deg` ≈ 0, and `altitude_km` ≈ 550.0 (FIXTURE-A's designed
altitude). Both hold exactly (`tests/test_screen_fixture.py`).

## Frontend changes kept to data-layer only

Rewrote the mockup's `this.RAW` (hardcoded array) and `this.dots` (procedural
fake scatter generator) to fetch from the local server and derive everything
from real response data -- but left the visual design, layout, and styling
completely untouched. Only exception: three places were hardcoded HTML
(object-class legend counts, "CANDIDATES" stat, "SCREENING RUN" id) rather
than template-bound at all, meaning they'd never have reflected real data
regardless of source -- converted those three to bindings so they update
with the real feed like everything else. Also corrected two factually
stale labels now that the backend is real: "SOURCE: CELESTRAK GP" ->
"SPACE-TRACK GP" (Phase 1 decision), "CADENCE: 12H · 02:00/14:00Z" -> "2H ·
CONTINUOUS" (matches `infra/template.yaml`'s actual EventBridge schedule).

## Real orbit renderer (replacing the "your renderer mounts here" placeholder)

The mockup's center panel was explicitly a placeholder ("WEBGL ORBIT CANVAS
· your renderer mounts here · reads catalog_snapshot + conjunctions") --
built the real thing rather than leaving it, per explicit request.

**catalog_snapshot didn't have real positions.** It only had scalar
altitude/period, not x/y/z -- nothing to plot in 3D. Added `position_km`
(TEME frame, SGP4's native output -- close to but not rigorously J2000,
labeled accordingly in the UI) to each `catalog_snapshot` entry, computed by
propagating the whole catalog to one instant. `catalog_snapshot_json()` now
takes an optional `epoch`; omitted entirely when not given, so callers that
only need the scalar fields don't pay for propagation they don't need.

**Positions are computed live per request, not cached.** A snapshot goes
stale in seconds; propagating the real ~32k catalog to a single instant
measured at **0.13s**, so there's no reason to serve anything but the
current position. `dev/local_server.py`'s `/catalog-snapshot` route calls
the handler fresh on every request instead of serving the frozen disk
cache used for `/conjunctions` (screening results are correctly a discrete
per-run artifact; positions are not). The frontend re-fetches positions
every 5s to animate real orbital motion, independent of the conjunction
feed (which only needs to load once per screening run).

**Renderer: Three.js via dynamic `import()` from a CDN, no build step.**
The component script runs inside a `new Function(...)`-constructed function
(the dc-runtime's execution model -- see `support.js`), which can't use a
static `import` declaration, but a dynamic `import('https://...')`
expression works anywhere a normal expression does, so this needed no
bundler or import-map changes. Mounted imperatively into a plain
`<div id="chronos-globe-mount">` rather than through the template's `{{ }}`
bindings -- WebGL needs a stable canvas across re-renders, and React
(underneath the dc-runtime) leaves a childless, binding-free DOM subtree
alone across renders, so this is safe without fighting the framework.

Scene: real Earth-radius-scaled sphere (SGP4/TEME km / 500 -> scene units),
a `THREE.Points` cloud of all ~32k objects colored by type (matching the
existing active/debris/rocket palette), and line + marker highlights for
the top 80 conjunctions by miss distance, colored by severity. Auto-rotates
slowly, drag-to-rotate and scroll-to-zoom via raw pointer events (no
OrbitControls addon, to avoid a second CDN module dependency). Conjunction
highlight endpoints are looked up by NORAD ID against the same position
data already fetched for the point cloud, not a second fetch.

**Known limitation, disclosed rather than hidden:** conjunction highlights
show both objects' *current* positions, not their positions at TCA (which
may be hours or days in the future). Chosen deliberately -- a "current sky"
view is a coherent, honest picture; plotting two objects at their future
TCA position while every other point on screen is at "now" would be
inconsistent and more confusing than useful. If TCA-relative playback is
wanted later, `conjunctions[].tca` is already in the response.
