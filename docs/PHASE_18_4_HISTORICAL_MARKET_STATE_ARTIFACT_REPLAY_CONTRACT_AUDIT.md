# Phase 18.4 — Historical Market State Artifact Persistence & Replay Contract Audit

**Status: AUDIT ONLY. Zero code changes.** Every claim is backed by a
direct code read or a live query against the real VPS databases,
re-run fresh for this phase.

---

## 1. Current Capability

### Snapshot Persistence — Answer: **B, Temporary Reconstruction View, with one narrow exception**

- **DAILY resolution**: `MarketRealitySnapshotStore` (252 real,
  persisted rows, confirmed by direct count) IS a real persistent
  artifact — but only for `resolution=DAILY` snapshots, and only ones
  someone explicitly called `.write()` on.
- **FIVE_MINUTE / options-inclusive reconstructions** (Phase 18.1):
  **never persisted anywhere** — every call to
  `build_market_reality_snapshot(..., resolution=RESOLUTION_FIVE_MINUTE)`
  recomputes from `HistoricalObservationStore` fresh. Confirmed by
  direct code read of `builder.py` (no store write call exists in that
  code path) and reconfirmed by design in Phase 18.1's own report.
- **A new, load-bearing finding this phase**: the 252 persisted DAILY
  rows are **stale relative to the current model**. Directly queried
  the most recently-dated persisted row — its stored JSON keys are
  `{built_at, certification_refs, completeness, date, futures, is_final,
  schema_version, spot, vix}` — **missing `options`, `resolution`,
  `as_of` (Phase 18.1) and `reconstruction_version` entirely (Phase
  18.3)**. `MarketRealitySnapshot.from_dict()` correctly backfills
  defaults for all of these when READ (verified in Phase 18.3's own
  tests), but the persisted artifact itself has never been
  regenerated since it was originally written — **no fingerprint has
  ever actually been computed and stored for any of the 252 real rows
  that exist today.** `fingerprint()` (Phase 18.3) is a real, working
  capability that has never yet been exercised against Bujji's own
  persisted data outside of this and the prior phase's test runs.

`HistoricalObservationStore` and `RawObservationStore` themselves
remain what they have always been — immutable raw Reality, re-confirmed
unchanged this phase (no new grep needed; Phase 18.2 §9/§10's INSERT-only
finding stands, no write path was touched by 18.1–18.3).

### Research Artifact Model — Answer: **does not exist**

Direct grep for `BacktestInputArtifact`, `ResearchDatasetVersion`,
`artifact_id`, `snapshot_fingerprints` across the whole codebase:
**zero matches for the first two; the second two exist, but only in
one place, unconnected to `MarketRealitySnapshot`.**

**What was found instead**: `bujji/qualification/replay_models.py`'s
`ReplayRunResult.artifact_ids: Dict[str, Optional[str]]` — a real field
in the **older, orphaned** qualification/replay pipeline (Series
46–64, re-confirmed still orphaned this phase: zero references from
`bujji/trading_brain/`, `bujji/shadow_runtime/`, or
`bujji/production_runtime/`, same finding as Phase 18.0/18.2). This is
adjacent prior art — proof the *concept* of an artifact-id map has
existed in this codebase before — but it is tied to that pipeline's own
`ReplayScenario` schema, not `MarketRealitySnapshot`, and cannot be
reused as-is without a deliberate integration decision this phase does
not make.

**Conclusion: no `BacktestInputArtifact`/`ResearchDatasetVersion` exists
today, anywhere, for the Reality-tier architecture.** The individual
pieces this shape would need — `date_range` (trivial), `instruments`
(trivial), `resolution` (Phase 18.1 field), `snapshot_fingerprints[]`
(Phase 18.3's `fingerprint()`, callable per-snapshot), `reconstruction_version`
(Phase 18.3 field), `source_data_versions` (does not exist, see §3) —
are mostly present as *ingredients*, not assembled into any container.

## 2. Real Evidence

### Data Versioning (§3 of the phase brief)

Re-verified directly, not re-cited:

| Mechanism | Exists? | What it actually tells you |
|---|---|---|
| `ingestion_run_id` | Yes, real, per-run (`IngestionRun` model, `historical_reality/models.py`) | WHICH fetch attempt produced a row — but is a STRING label a human/script assigns (e.g. `f"OPTCHAIN-{capture_ts}"`), not a content version |
| `raw_artifact_ref` | Yes, for 6 of 7 writers (spot/futures/VIX daily+intraday); **empty string for options** (unchanged since Phase 17I.10's own disclosed gap, re-confirmed live this phase — a fresh sample options row still shows `raw_artifact_ref: ""`) | A pointer to the literal API response on disk — proves reproducibility of the TRANSFORMATION step, where present |
| `certification_status`/`certification_ref` | Yes, stamped at write time | Proves the access method was certified AT CAPTURE TIME (Phase 18.2 §10's own finding: never re-verified later) |
| `fingerprint()` | Yes (Phase 18.3), but **never yet computed against the 252 real persisted rows** (§1) | Would prove CONTENT identity between two reconstructions, once actually used |

**None of these, individually or together, currently tells Bujji
"historical data changed because X"** — there is no mechanism that
diffs today's `HistoricalObservationStore` against a prior day's and
reports "3 spot bars were corrected, 1 new option strike appeared."
`ConflictingHistoricalObservationError` (Phase 18.2 §9/§10) is the
closest real mechanism — it *would* fire if a genuinely revised value
tried to overwrite an existing natural key — but it is a *write-time
guard*, not a *versioning report*: nothing logs "a conflict was
attempted and rejected on date X" anywhere queryable after the fact
beyond the raised exception itself at the moment it happens.

### Customer Reproducibility Scenario (§4 of the phase brief)

Ran the exact worked example — NIFTY Iron Condor, 5-minute, options +
futures + VIX, entry 09:20 / exit 15:15 — against real stored data,
twice (once against the literal date given, once against a real
trading day, since the literal date turned out to be a market holiday).

**First run, `2026-08-01` (the date literally given in the brief)**:
```
ENTRY 09:20  spot=None  options=None
EXIT  15:15  spot=None  futures=None  vix=None  options=None
```
`2026-08-01` is a **Saturday** (confirmed:
`datetime.date(2026,8,1).weekday() == 5`) — markets were closed. This
is the system behaving **correctly** (no fabricated data for a day
with no market), but it means the literal example date cannot be used
to demonstrate the richer case, so a real trading day was substituted.

**Second run, `2026-08-14` (a real trading day, and the only day
options data exists at all)**:
```
ENTRY 09:20  spot=None  futures=None  vix=None  n_options=2162
EXIT  15:15  spot=None  futures=None  vix=None  n_options=2190
entry fingerprint: 159f002bd98d10aa...  reconstruction_version: 18.3.0
exit  fingerprint: 87864f120d03d396...
entry fingerprint reproducible (rebuilt twice): True
```

**This is the single most important finding of this phase.** Directly
confirmed by querying `HistoricalObservationStore.range()` for all
three of spot/futures/VIX at `FIVE_MINUTE` resolution on 2026-08-14:
**zero rows for all three, on the exact same day options has 2,162–2,190
real contracts.** (`2026-08-13`, one day earlier, has 75–77 rows for
each of the three — the 5-min backfill simply had not reached
2026-08-14 yet at the time of this audit, the same lag Phase 18.1/18.2
already found and flagged, now shown to directly collide with the one
day options exist.)

**Direct answer to the phase's own question — "can Bujji prove these
exact option chain states, futures states, and volatility states were
used?"**: Bujji can prove the **option chain state** exactly (2,162
real contracts at entry, fully fingerprinted, fully reproducible — this
part of the promise is real and demonstrated). Bujji **cannot** prove a
futures state or a volatility state for this scenario **at all**,
because none exists for this date at this resolution — not a
reproducibility failure, an honest **data absence**, but one that
means **an Iron Condor backtest exactly as described in this phase's
own example cannot be run today, for any historical date**: every date
either has options (only 2026-08-14, and only that one day, with no
spot/futures/VIX 5-min data yet) or has spot/futures/VIX 5-min data
(every trading day since 2017-07-17) but never options (before
2026-08-14). **There is currently zero overlap between "options data
exists" and "spot/futures/VIX 5-min data exists" in Bujji's real,
queried data.**

### Options-Specific Audit (§5 of the phase brief)

- **Timestamp alignment**: re-confirmed (Phase 18.3's own live test) —
  contracts within one capture cycle share a coarse, cycle-level
  timestamp (Phase 17I.10's own disclosed precision limit); different
  cycles produce genuinely different `observed_at` values per-contract,
  live-proven in Phase 18.3.
- **Missing contracts / newly listed strikes**: real, live-observed
  (Phase 18.0/18.1) — 14 new identities appeared intraday on
  2026-08-14; handled honestly (absent before first row, present after,
  never fabricated).
- **Expired contracts / expiry transitions**: **UNVERIFIED, same as
  Phase 18.2's own finding, unchanged this phase** — only one real
  calendar day of options capture exists in the entire system
  (2026-08-14); no contract in the captured set has actually expired
  yet as of this audit, so an actual expiry-transition event has never
  been observed. This remains a real, honestly-disclosed inference
  gap, not something newly resolved.
- **Symbol identity stability**: re-confirmed structurally — the
  composite identity (`underlying|expiry|strike|option_type`) is
  content-derived, not broker-symbol-derived (Phase 17I.10), so it
  cannot be destabilized by a broker-side symbol rename. This is a real
  guarantee, verified by design, though (per the point above) never
  exercised across an actual expiry rollover in practice yet.

## 3. Missing Pieces

Ordered by what actually blocks the phase's own success condition:

1. **No overlap between options and spot/futures/VIX 5-min coverage**
   (§2, the central finding) — not a code gap, a **data coverage gap**:
   the 5-min spot/futures/VIX backfill needs to catch up to (and stay
   current with) whatever date range options capture covers, or no
   multi-instrument intraday backtest is possible for ANY historical
   date today.
2. **No artifact container** (§1) — the ingredients
   (`fingerprint()`, `reconstruction_version`, `resolution`, `as_of`)
   all exist per-snapshot, but nothing assembles a `date_range`-spanning
   research artifact (`BacktestInputArtifact`/`ResearchDatasetVersion`)
   out of a sequence of them.
3. **`fingerprint()` has never been run against real persisted data
   outside of test/audit runs** (§1) — a real, if narrow, gap between
   "the mechanism works" (Phase 18.3 proved this) and "the mechanism
   has been used" (it has not, on anything that matters yet).
4. **No data-change/versioning report** (§2) — `ConflictingHistoricalObservationError`
   is a real guard but not a queryable history of what changed and why.
5. **Options `raw_artifact_ref` still empty** (§2, carried forward
   unchanged from Phase 17I.10/18.0) — the one remaining lineage gap
   for options specifically.
6. **Expiry-transition behavior remains genuinely unverified** (§2) —
   not fixable by audit; requires either more real time to pass or a
   deliberate synthetic test, neither performed this phase (out of
   scope: audit only).

## 4. Priority Classification

| Finding | Priority |
|---|---|
| Options/spot-futures-VIX coverage overlap (finding #1) | **P0 — blocks every multi-instrument intraday scenario today, including this phase's own worked example** |
| Artifact container model (finding #2) | **P1 — needed before any backtest can assert "this exact dataset version was used" across a date range, not just a single snapshot** |
| Exercise `fingerprint()` against real persisted artifacts (finding #3) | **P1 — cheap, high-value; closes the "proven in theory, unused in practice" gap** |
| Data-change/versioning report (finding #4) | **P2 — valuable for research trust, not blocking for a first backtest** |
| Options `raw_artifact_ref` (finding #5) | **P2 — carried debt, narrow blast radius (options-only, lineage-only)** |
| Expiry-transition verification (finding #6) | **P3 — cannot be forced; resolves itself as real time passes, or via a deliberate future synthetic test** |

## 5. Final Decision

**C — Major historical architecture gap**, but a narrowly-scoped and
precisely-identified one, not a broad one.

This is not a downgrade from Phase 18.2/18.3's more optimistic
per-snapshot findings — the identity/fingerprint/no-look-ahead
machinery those phases hardened is real and correctly proven. What
this phase found, that no prior phase surfaced, is a **data coverage
collision**: the one instrument (options) with the richest new
identity/provenance guarantees from Phases 17I.10–18.3 has **zero
temporal overlap** with the three instruments (spot/futures/VIX) that
have the deepest historical coverage, at the resolution a real
multi-leg strategy backtest would need. A backtesting engine built
today, however well-identified its snapshots are, has **no historical
date** on which it could actually assemble a complete Iron-Condor-shaped
input — the architecture is sound; the **data**, as it stands today,
cannot yet feed it.

**Success condition, evaluated directly**: "Before I simulate a
strategy, exactly what historical reality am I simulating against?"
— Bujji CAN now answer this precisely, per-snapshot, for whichever
individual instrument has data on a given date (proven in Phase 18.3
and re-confirmed live this phase). Bujji CANNOT yet answer it for a
realistic multi-instrument strategy scenario on **any** real historical
date, because no date exists today where the full instrument set
required actually coexists. Closing finding #1 (extending 5-min
spot/futures/VIX coverage to overlap with options' capture window, or
extending options' effective range) is the single highest-leverage next
step — everything else in §3 is real but secondary to that one data
gap.
