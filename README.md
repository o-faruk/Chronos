# Chronos

Satellite conjunction (collision-risk) screening for the real public Earth-orbit catalog — vectorized SGP4 propagation, spatial-index coarse screening, continuous-time closest-approach refinement, and a live WebGL orbital visualization, run against real Space-Track/CelesTrak data end to end.

**[→ Live demo](https://chronos-dashboard-6f3.pages.dev)** — a real screening run: 31,934 tracked objects, 17,809 conjunctions, rendered in 3D. (Static snapshot — see [Status](#status--scope).)

## What it does

- **Ingests** the full public TLE catalog (~32k objects) from Space-Track, with CelesTrak as a secondary source for curated active-satellite classification
- **Propagates** the entire catalog with vectorized SGP4 (`SatrecArray`, numpy-batched — not a per-object Python loop): 72 hours at 60s resolution for 31,934 real objects in **~27 seconds**
- **Screens** for close approaches via a two-stage pipeline: a regime-bucketed `cKDTree` coarse pass (never enumerates all N² pairs), then continuous-time TCA (time-of-closest-approach) refinement via bounded optimization, not grid-quantized sampling
- **Serves** results as a versioned JSON API (Lambda handlers + DynamoDB/S3, SAM-deployable) and renders them in a real-time WebGL globe (Three.js) showing actual current orbital positions and live-highlighted conjunctions
- Adds two things the 2004-era public standard tool doesn't have: **on-demand custom-TLE screening** (submit your own satellite, get it screened against the full catalog in ~10s) and **run-over-run trend tracking** (is a conjunction's miss distance closing or opening between runs)

## Why: the SOCRATES gap analysis

CelesTrak's SOCRATES has been the public standard for conjunction screening since 2004. Before writing any propagation code, I pulled its live methodology and output rather than assuming a gap existed. Full writeup: [`docs/validation.md`](docs/validation.md), real head-to-head: [`docs/findings-report.md`](docs/findings-report.md).

| | SOCRATES (live) | Chronos |
|---|---|---|
| Catalog scale | 31,815 objects | 31,934 objects (same lineage) |
| Full-catalog run time | **11h 29m** (7-day window) | **3.0 min** (72h window) |
| Output | Parameterized HTML/CSV, no API | JSON API |
| Custom object screening | Not supported | `POST /screen`, ~10s |
| Run-over-run trend | Not supported | Per-conjunction, aggregated in the UI |

The honest tradeoff: Chronos finds fewer conjunctions per day than SOCRATES' 20-year-mature methodology, most likely from the coarse-pass sampling approximation — disclosed and root-caused in the findings report, not hidden.

## Engineering notes worth reading

The full reasoning for every non-obvious call is in [`docs/decisions.md`](docs/decisions.md) (one entry per decision, real numbers, not hand-waving). A few that stood out:

- **Hit a live Alpha-5 NORAD ID transition bug within an hour of getting Space-Track access.** Catalog numbers above 99999 started encoding the leading digit as a letter mid-development — not a hypothetical, an in-progress migration that broke the naive `int(line1[2:7])` parse the moment real data arrived.
- **Found that docked/berthed spacecraft (ISS modules, etc.) produce false-positive "conjunctions" at 0m separation** — CelesTrak/Space-Track publish them under separate NORAD IDs sharing near-identical orbital elements. Rejected a relative-velocity filter as the fix once I confirmed it would also silently suppress a genuine, slow-closing hazard (the hand-built regression fixture has a real ~1.6cm/s closing case) — used an orbital-elements-identity check instead.
- **Measured, not guessed, the coarse-screening radius.** The initial 75km search radius produced 1.6M candidate pairs and would have blown Lambda's timeout at the real catalog's density; retuned to 30km against real timing data, cutting the fine-screening stage from ~12 minutes to ~1.4 minutes.
- **Root-caused a "single-digit FPS" WebGL complaint to a completely different part of the page.** Spent a round chasing draw calls, overdraw, and canvas resolution in the 3D scene — the actual cause was a 17,000-row conjunction list re-rendering, unvirtualized, on a 1-second timer, starving the render loop's `requestAnimationFrame` calls. The GPU was never the bottleneck; the main thread was. Fixed by capping rendered rows, not by touching WebGL again.

## Architecture

```
ingest/     TLE fetch + parse (Space-Track bulk + CelesTrak active-list classification)
propagate/  vectorized SGP4 batch propagation (SatrecArray)
screen/     coarse spatial pass (regime-bucketed cKDTree) + fine TCA refinement
api/        response schema, DynamoDB/S3 storage, Lambda handlers
infra/      AWS SAM template (Lambda + API Gateway + DynamoDB + S3 + EventBridge)
dev/        local dev server (real pipeline, AWS mocked via moto) + static-site baker
tests/      41 tests -- Vallado reference-vector validation, a hand-derived precomputed
            conjunction fixture, moto-backed AWS integration tests
docs/       decision log, SOCRATES gap analysis, real head-to-head findings
```

**Stack:** Python (`sgp4`, `numpy`, `scipy`, `boto3`), Three.js (WebGL, loaded via dynamic `import()`, no build step), AWS (Lambda/API Gateway/DynamoDB/S3/EventBridge via SAM), `moto` for AWS-mocked testing.

## Status / scope

- **Backend:** fully built and tested against real data (real Space-Track catalog, real screening runs, 41/41 tests passing).
- **Infra:** written and unit-tested (`infra/template.yaml`, SAM) but not deployed to a real AWS account — that costs money and needs credentials, so it stayed a deliberate choice rather than something to do unilaterally.
- **Frontend:** the dashboard UI (design: separate, not part of this backend build) is fully wired to real data, including a real WebGL orbital visualization built from scratch (real SGP4-propagated positions, real conjunction highlights).
- **Live demo:** a static snapshot (baked via `dev/build_static.py`) hosted free on Cloudflare Pages. Real data, but positions are frozen at bake time (no live 5s position refresh) and custom-TLE screening isn't available (needs a real backend). The fully live version runs locally via `dev/local_server.py`.

## Running it

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt
pytest tests/ -v          # 41 tests, no credentials needed
```

To run the live dashboard against real data (needs a free [Space-Track](https://www.space-track.org) account):

```bash
export SPACETRACK_USERNAME="you@example.com"
export SPACETRACK_PASSWORD="..."
python -c "from ingest.catalog import build_catalog, save_catalog; save_catalog(build_catalog())"
python dev/local_server.py
# open http://localhost:8787
```

First run screens the real ~32k-object catalog (~3 min); results are cached to disk so subsequent restarts are instant (`--refresh` to force a new run).
