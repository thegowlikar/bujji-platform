# Phase 18.3 — MarketRealitySnapshot Identity Hardening

**Status: IMPLEMENTED, TESTED, LIVE-VALIDATED.** Closes Gap 1
(component timestamps) and Gap 2/3 (deterministic fingerprint +
reconstruction version) from Phase 18.2's audit. No backtest engine,
no strategy logic, no Reality-tier or storage changes — this phase
only hardens the identity of `MarketRealitySnapshot` itself.

---

## 1. Existing Architecture Audit (before writing code)

Re-inspected directly, not assumed from prior phase reports:

- **`replay_engine.fingerprint_state()`** (`bujji/replay_engine/engine.py:71`):
  `hashlib.sha256(json.dumps(_normalize(payload), sort_keys=True,
  default=str).encode()).hexdigest()` — a real, working,
  order-independent content hash, already proven live against real
  `MarketRealitySnapshot.to_dict()` output in Phase 18.2's own §9.
  `replay_engine/engine.py` has **zero project imports** (stdlib
  `hashlib`/`json` only) — confirmed by reading its import block —
  meaning importing it from `market_reality_snapshot/models.py`
  introduces no circular dependency.
- **`bujji.epistemics.identity.resolve_code_identity()`** (Phase 16D):
  a real git-commit/dirty-state resolver — considered and **not
  reused this phase**. It answers "which CODE commit built this,"
  which is a different question from "which RECONSTRUCTION LOGIC
  VERSION built this" — the phase's own Gap 3 wants a
  hand-bumped, human-legible marker for *material logic changes*
  (matching this project's own `SCHEMA_VERSION`/`MARKET_OBSERVATION_VERSION`
  pattern), not a git SHA that changes on every unrelated commit. Using
  git-dirty-state would also have made `reconstruction_version` report
  "dirty" almost permanently, per that module's own documented finding
  about this project's working tree — not useful signal for this
  purpose.
- **Existing versioning pattern**: `SCHEMA_VERSION` (models.py, bumped
  by hand at 1.0.0→1.1.0→1.2.0 across three prior phases) and
  `MARKET_OBSERVATION_VERSION` (Phase 17E) are both simple, hand-bumped
  string constants — **this exact pattern was reused verbatim** for
  `RECONSTRUCTION_VERSION`, not invented fresh.
- **`OptionContractSnapshot`/`SpotSnapshot`/`FuturesSnapshot`/`VixSnapshot`**:
  re-read field-by-field (Phase 18.2 §4's own finding, re-verified):
  none carried a `timestamp`/`observed_at` field — only
  `source_observation_ids`, requiring manual dereferencing through
  `HistoricalObservationStore` to learn a component's real moment.
- **Builder**: every one of the 7 real construction sites in
  `builder.py` (DAILY historical ×3, DAILY live-aggregated ×3,
  FIVE_MINUTE intraday ×3, options ×1) was individually re-read to find
  the exact expression that already computes the right per-component
  timestamp — in every case, the row/observation object already being
  used to build the snapshot also carries
  `.observation.identity.timestamp`, so no new query was needed
  anywhere; only new field wiring.

**Conclusion: no new hashing mechanism, no new identity utility, no new
storage was required.** Every primitive this phase needed already
existed in the codebase; the work was entirely wiring.

## 2. Design Decision

- **`observed_at`** added as a new, optional, additively-defaulted
  field on `SpotSnapshot`, `FuturesSnapshot`, `VixSnapshot`, and
  `OptionContractSnapshot`. Not "`observation_id`" and "`source_observation_reference`"
  as two further separate fields — the phase's own instructions asked
  for those, but `source_observation_ids: Tuple[str, ...]` (plural,
  existing since Phase 17H.5) already serves exactly that purpose:
  it's the real, dereferenceable pointer back to the originating
  `HistoricalObservation`(s). Adding two more fields carrying the same
  information under different names would have been the literal
  "second identity system" this phase's own reuse instruction warns
  against. This substitution is documented here explicitly rather than
  silently deviating from the literal spec.
- **`reconstruction_version`** added as a new field on
  `MarketRealitySnapshot`, defaulting to the module constant
  `RECONSTRUCTION_VERSION = "18.3.0"`, stamped by the builder on every
  construction.
- **`fingerprint()`** added as a **method** (not a stored field) on
  `MarketRealitySnapshot`. Computed fresh on every call from a private
  `_fingerprint_payload()` helper, never cached — a stored fingerprint
  field could silently go stale if a snapshot were ever hand-constructed
  (e.g. via `from_dict` on edited data) with content that no longer
  matches a previously-computed hash; a method can never disagree with
  the object's own actual state.

## 3. Implementation Details

**Files changed** (all in `bujji/market_reality_snapshot/`, plus one
new test file):

- **`models.py`**: `SCHEMA_VERSION` bumped `1.1.0` → `1.2.0`.
  `RECONSTRUCTION_VERSION = "18.3.0"` added. `observed_at: Optional[str]
  = None` added to all four component dataclasses (`to_dict`/`from_dict`
  updated). `MarketRealitySnapshot` gained
  `reconstruction_version: str = RECONSTRUCTION_VERSION`,
  `_fingerprint_payload()`, and `fingerprint()`; `to_dict`/`from_dict`
  updated with `.get(...)` defaults for full backward compatibility.
- **`builder.py`**: every one of the 7 component-construction call
  sites now passes `observed_at=row.observation.identity.timestamp`
  (single-row cases) or `observed_at=live_obs[-1].observation.identity.timestamp`
  (live-aggregated multi-tick cases — the LAST real tick folded into
  the aggregate, the moment `close` became true). The final
  `MarketRealitySnapshot(...)` construction now passes
  `reconstruction_version=RECONSTRUCTION_VERSION`.
- **`tests/test_market_reality_snapshot_identity_hardening.py`**: 17
  new tests (below).

**Zero changes** to `HistoricalObservationStore`,
`MarketRealitySnapshotStore`, any Reality-tier ingestion script, or any
raw historical data.

## 4. Fingerprint Definition

`_fingerprint_payload()`'s exact, documented contract (also inline in
the method's own docstring, per this phase's instruction to document
the decision, not just implement it):

**INCLUDED**: `date`, `resolution`, `as_of`, `completeness`, every
component's full `to_dict()` (values, `observed_at`,
`source_observation_ids`), `certification_refs` (**sorted** — their
collection order is a code-iteration artifact, never a market fact).

**EXCLUDED, each for a stated reason**:
- `built_at` — wall-clock build time; two builds of identical data a
  second apart must fingerprint identically.
- `is_final` — depends on the caller's `now` relative to `date`, not
  on market content; would otherwise make identical data fingerprint
  differently depending purely on *when* it was reconstructed.
- `schema_version` / `reconstruction_version` — version **metadata**,
  not market content. This is the load-bearing decision for Gap 3:
  if either were hashed in, a pure logic-version bump and a pure data
  change would be indistinguishable from the fingerprint alone.
  Comparing `(fingerprint, reconstruction_version)` as a **pair**
  across two builds is how the two cases are told apart — folding
  version into the hash would destroy that. **Live-proven, not just
  asserted**: a snapshot's `reconstruction_version` was swapped to
  `"99.0.0"` via `dataclasses.replace` and its `fingerprint()`
  confirmed unchanged (§6, Test 5).

`fingerprint()` calls `replay_engine.fingerprint_state()` on this
payload verbatim — no new hashing logic, no new normalization beyond
the field *selection* performed here.

## 5. Versioning Approach

`RECONSTRUCTION_VERSION` is a hand-bumped string constant, following
this project's own existing pattern (`SCHEMA_VERSION`,
`MARKET_OBSERVATION_VERSION`) rather than a computed value. It must be
bumped by a human whenever the **selection/aggregation logic** in
`builder.py` changes materially (e.g. a different tie-break rule for
`_latest_at_or_before`, a different rule for what counts as a
"contract's own latest row"). It is **not** bumped for: a new date's
data becoming available, a new instrument being wired in additively, or
any change to fields **excluded** from the fingerprint (`built_at`
logic, `is_final` logic). This bump discipline is documented in the
constant's own comment in `models.py`, matching how `SCHEMA_VERSION`'s
own bump history is documented inline at each of its three prior
bumps.

## 6. Validation Evidence

All against real, live VPS data — not simulated — reproducing the
Phase 18.2 gap examples directly.

**Test 1 — Determinism**: `build_market_reality_snapshot('2026-08-14',
..., as_of_time='2026-08-14T09:20:21.941228+05:30')` built twice;
`fingerprint()` equal both times:
`47037ee926a09195...` == `47037ee926a09195...`. **PASS.**

**Test 2 — Component provenance**, real data:
```
requested as_of (2026-08-13): 2026-08-13T10:35:00+05:30
  spot.observed_at:    2026-08-13T10:35:00+05:30
  futures.observed_at: 2026-08-13T10:35:00+05:30
  vix.observed_at:     2026-08-13T10:35:00+05:30
```
All three real components independently dereferenced their own
`observed_at` — no manual store query needed anymore, unlike Phase
18.2's own demonstration which had to reach into the store directly.
This specific real date's data happened to align all three to the
requested minute (an honest data fact, not an enforced guarantee — Phase
18.2 §4's own caveat still applies to WHETHER they align, only HOW to
observe it is now solved). **The genuinely divergent case from Gap 1's
own worked example (options at `10:34:58` while others show `10:35:00`)
is proven by construction in Test 3 below (synthetic data, since no
real captured moment happened to straddle a cycle boundary exactly
this way in the live query run)** — the mechanism itself
(`observed_at` on `OptionContractSnapshot`, populated per-contract
independently) is real and live-verified via a real options
reconstruction (`snap.options.contracts[i].observed_at` populated
correctly for 2,176 real 2026-08-14 contracts, all reporting their real
row's own timestamp). **PASS**, with the divergence case backed by a
direct, real-mechanism unit test rather than a live query that happened
to hit a straddling boundary.

**Test 3 — No-look-ahead preservation**: re-run under the hardened
model — a spot bar at `09:15` and another at `15:25` written to a real
temp store; `as_of_time=09:15` reconstruction's `spot.observed_at`
confirmed `== "2026-08-14T09:15:00+05:30"`, confirmed `!=` the later
bar's timestamp, and `close` confirmed to be the early bar's value, not
the late one's. **PASS** — the underlying no-look-ahead mechanism
(Phase 18.1's `_latest_at_or_before`, unchanged) still holds; this
phase only added visibility into which row it picked.

**Test 4 — Existing compatibility**: the full pre-existing
`test_market_reality_snapshot.py` + `test_market_reality_reconstruction.py`
+ `test_market_reality_snapshot_options_intraday.py` suites (41 tests)
re-run **unmodified** against the hardened code — all 41 still pass.
Two additional new tests explicitly construct dicts shaped exactly
like the 252 real 1.0.0-era rows and a simulated 1.1.0-era (post-18.1,
pre-18.3) row, confirming both deserialize correctly with
`reconstruction_version` defaulting to the current constant and
`observed_at` defaulting to `None` on every component. **No breaking
schema migration** — verified, not merely asserted.

**Gap 3's own worked example, directly reproduced**:
```
snap.fingerprint()                    -> 47037ee926a09195...
dataclasses.replace(snap, reconstruction_version="99.0.0").fingerprint()
                                       -> 47037ee926a09195...  (UNCHANGED)
```
Confirms: same data + different logic-version label ⇒ same fingerprint,
different `reconstruction_version` — exactly the pair-comparison
mechanism Gap 3 asked for.

## 7. Test Suite / Regression Result

17 new tests in `tests/test_market_reality_snapshot_identity_hardening.py`:
component `observed_at` correctness (4), fingerprint determinism and
exclusion rules (6), reconstruction_version stamping (1), backward
compatibility across both the 1.0.0 and 1.1.0 legacy shapes (3), a
component-level serialization round-trip (2), and a re-affirmation of
no-look-ahead under the hardened model (1).

- New test file: **17 passed**.
- Full pre-existing snapshot/reconstruction/options-intraday suite (41
  tests, spanning Phases 17H.5 through 18.1): **unmodified, all 41
  still pass**.
- **Full suite: 5,709 passed, 0 failed** (up from 5,692 — exactly the
  17 new tests, zero regressions elsewhere).

## 8. Remaining Gaps

Explicitly not addressed this phase (per its own scope restriction),
carried forward:

- **Futures synthetic-continuous-contract identity** (Phase 18.0/18.2)
  — unchanged, explicitly out of scope again.
- **Fingerprint/reconstruction not persisted anywhere** — `fingerprint()`
  and `reconstruction_version` are now computable and present on every
  `MarketRealitySnapshot` object, but nothing yet writes a
  `(date, as_of, fingerprint, reconstruction_version)` record anywhere
  for later comparison — that remains Phase 18.2 §7's own identified
  gap (a future backtest engine's own responsibility to persist what it
  actually used, per this phase's explicit "do not build a backtest
  engine" boundary).
- **`observation_id`'s `repr(payload)` order-sensitivity** (Phase 18.2
  §6) — untouched; `fingerprint_state()`'s `sort_keys=True` approach is
  the more robust pattern and was reused here, but the older
  `market_observation.engine._observation_id()` mechanism itself was
  not modified (out of this phase's stated scope — that function
  belongs to Reality-tier identity, not snapshot identity).
- **Cross-instrument synchronization tolerance** (Phase 18.2 §4) — now
  *observable* (every component exposes `observed_at`), but still not
  *enforced* — the builder still makes no attempt to align or warn
  when components diverge; a future consumer must still check
  `observed_at` values itself to detect a divergent state. This phase
  closed the visibility gap, not the alignment gap — that remains a
  deliberate, disclosed boundary, not an oversight.
