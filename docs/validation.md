# Phase 0 — Validating the gap against SOCRATES

Date: 2026-07-07

## What SOCRATES actually does

Source: [celestrak.org/SOCRATES](https://celestrak.org/SOCRATES/), [socrates-format.php](https://celestrak.org/SOCRATES/socrates-format.php), [search.php](https://celestrak.org/SOCRATES/search.php).

- **Scope**: screens *active satellites* ("primaries") against the *full public catalog* ("secondaries") — active, debris, rocket bodies, everything. It does **not** screen debris-vs-debris or rocket-body-vs-debris pairs where neither object is an active payload. Also explicitly excludes intra-fleet conjunctions between two fully-operational satellites in the same constellation (e.g. Starlink-vs-Starlink), though it still checks a fleet's operational satellites against that same fleet's dead/partially-operational ones.
- **Cadence**: documented as three runs per day. Current live run (checked today, 2026-07-07) is stamped "data current as of 2026 Jul 07 00:12:25 UTC" and took **11h 51m** to compute a 7-day window over 15,913 primaries × 31,819 secondaries (145,083 total conjunctions found within the threshold). A run of that length three times a day would overlap itself, so in practice the effective refresh rate is slower than the documented cadence at current catalog size.
- **Window**: 7 days forward.
- **Threshold**: flags everything within **5 km** miss distance at TCA — much looser than the severity bands already baked into the mockup UI (critical <200 m / high <500 m / medium <1 km / low <5 km). SOCRATES doesn't rank by a severity enum at all; it just sorts a big table by min range, max probability, relative speed, NORAD ID, or TCA.
- **Output mechanism**: a parameterized web page (`search.php` / `table-socrates.php?...`) returning HTML or an RFC-4180 CSV dump. There is no JSON, no auth-free programmatic endpoint, no webhook/feed — you're expected to open the page or download a CSV and use a spreadsheet.
- **Data source**: Space-Track GP/TLE data, propagated with SGP4 — same propagator and same general data lineage we'd be using via CelesTrak's mirror of the public catalog.

## What it does not expose (confirmed gaps)

1. **No programmatic API.** Query-string HTML/CSV only. Nothing a frontend or another service can poll and get typed JSON back from.
2. **No custom/user-submitted objects.** You cannot inject a TLE for a satellite you personally operate or care about and screen it against the catalog. You only get what's already in the public catalog as a "primary" (i.e., already flagged active by CelesTrak).
3. **No historical trend tracking.** Each run is a fresh snapshot; there's no concept of "this pair's miss distance was 1200 m two runs ago and is now 400 m" — no run-over-run diffing or closing/opening trend signal.
4. **No debris-debris screening.** Not marketed as a gap by CelesTrak, but it's real: two dead objects on a collision course with each other are invisible to SOCRATES because neither is an active payload. This matters for Kessler-cascade-style risk (debris creating more debris) even though no single operator is directly threatened.
5. **No severity/alerting abstraction.** No closed severity enum, no push mechanism — it's pull-only, look-it-up-yourself.

## Decision: what Chronos differentiates on

Decided 2026-07-07, after review: build two of the three identified gaps.

1. **Baseline (not a differentiator, but required): real JSON API.** Needed anyway for the frontend contract already locked in (the mockup UI at `Chronos Orbital Conjunction System/` is already built against exactly this shape: severity enum with the 200 m/500 m/1 km/5 km bands, object type colors for active/debris/rocket, LEO/MEO/GEO/HEO regime rows).
2. **User-submitted custom TLE screening.** SOCRATES has zero support for this. A user can inject a TLE for a satellite they operate/track and get it screened on demand against the full catalog. Computationally cheap (one object vs ~30k, not 30k²) — a good fit as a separate on-demand Lambda endpoint rather than part of the scheduled full-catalog run.
3. **Run-over-run trend tracking.** Persist each run's conjunction list (DynamoDB) and diff against the previous run per-pair — is miss distance closing or opening. Requires the scheduled full-catalog run and storage to exist first, so this lands after the core screening pipeline (Phase 2/3), not before.
4. ~~Full debris-vs-debris screening~~ — **reversed 2026-07-07.** Initially picked, then dropped once the TLE-source gap below made clear it added catalog-acquisition cost without matching payoff. Chronos screens active-vs-full-catalog (matching SOCRATES' pair shape), not full N².

Net: SOCRATES is not a full substitute for what's being built here. Proceeding to Phase 1.

## Follow-on decision: TLE source

CelesTrak's free GP API turned out not to expose a full-catalog bulk download — only active satellites (by group) plus 3 named debris clouds (Fengyun-1C, Iridium-33, Cosmos-2251), and its rate-limit policy (one download per group per 2h cycle, 250MB/day cap, IP firewall for excess requests) rules out reconstructing the rest via per-object `CATNR` queries.

Decision: **use Space-Track as the bulk source for the full unclassified catalog** (~30k objects, matching the catalog size already baked into the mockup UI), with CelesTrak kept as a convenience/secondary source. This is also what settled the debris-vs-debris reversal above — once full debris-vs-debris was off the table, the driving reason to fight for full debris coverage went away, but a full catalog of *secondaries* is still worth having so active-satellite screening isn't limited to 3 named debris clouds.
