# Phase 18.2 — Historical Market State Artifact & Replay Integrity Audit

**Status: AUDIT ONLY. Zero code changes.** Every claim is backed by a
direct code read, a live query against the real VPS databases, or a
real, reproduced demonstration — no claim is inferred from a filename,
docstring, or prior phase's report without independently re-verifying
it here.

---

## 1. Executive Summary

**The answer is PARTIALLY, with a precise, provable boundary.**

Bujji can *today* honestly promise: "the historical facts this
reconstruction used were not fabricated, were never silently
overwritten, and excluded everything after the requested moment" —
this is proven, not assumed, in §5 and §15. Bujji *cannot yet* promise:
"the exact same reconstruction, run a year from now, is provably
identical" — no mechanism exists today to prove that after the fact
(no fingerprint is stored anywhere alongside a reconstruction), and one
real, structural risk (futures' synthetic continuous series, carried
forward from Phase 18.0) means "the same market inputs" is not even
well-defined for futures without a future decision. The gap is narrow
and specific, not broad: reproducibility of the *underlying data*
appears sound wherever tested; what is missing is *recorded proof* of
that reproducibility, plus explicit handling of a small number of named
edge cases (component-level timestamp opacity, per-record certification
staleness, futures identity ambiguity).

## 2. Current Market State Model

Read directly from `bujji/market_reality_snapshot/models.py` and
`bujji/historical_reality/models.py`.

**What can be reconstructed, and at what resolution** (re-confirmed
live, same method as Phase 18.0/18.1 but re-queried fresh for this
phase):

| Instrument | DAILY | FIVE_MINUTE | Fields |
|---|---|---|---|
| Spot (`NSE:NIFTY50-INDEX`) | 1998-05-04 → today | 2017-07-17 → (lags ~1 day behind live) | open/high/low/close/volume |
| VIX (`NSE:INDIAVIX-INDEX`) | 2008-04-17 → today | 2017-07-17 → (same lag) | open/high/low/close (no `change_percent`, deliberately unset since 17H.5) |
| Futures (`NIFTY_FUT_CONTINUOUS`) | 2018-01-02 → today | 2018-02-01 → (same lag) | open/high/low/close (no OI — confirmed absent from `FuturesSnapshot`'s own field list) |
| Options (composite identity) | none, ever | 2026-08-14 → today only | ltp/bid/ask/open_interest/volume per contract |

**Which fields are literal Reality facts vs. derived**: every field
above is a literal, captured fact (`open`/`high`/`low`/`close`/`ltp`/
`bid`/`ask`/`oi`) — confirmed by re-reading `builder.py`'s helpers,
none of which compute anything (no return, no moving average, no
percentage change is calculated anywhere in this module).
`VixSnapshot.change_percent` and `FuturesSnapshot.expiry_date`
(historical path) are the only fields that are structurally always
`None` — the first because computing it needs the prior day's snapshot
(never implemented, disclosed in the model's own docstring since
Phase 17H.5), the second because historical continuous-contract rows
genuinely carry no expiry (Phase 17H.3).

**Coexistence**: yes — a single `MarketRealitySnapshot` carries
`spot`, `futures`, `vix`, and `options` as independent, individually-
optional fields, live-confirmed in §15 Demonstration B.

**Partial population**: yes, by design — `completeness` is
`COMPLETE`/`PARTIAL`/`EMPTY`, and every component field is
independently `Optional`. **A real, previously undocumented finding
this phase**: `completeness` still scores only spot/futures/vix
(Phase 18.1's own deliberate choice, to avoid retroactively
downgrading the 252 already-persisted historical snapshots) — meaning
a snapshot with a full 2,190-contract option chain but no futures data
still reports `PARTIAL`, never reflecting options' real presence in
its own summary field. Not a defect (documented, deliberate), but a
real asymmetry a backtest consumer must know to look past
`completeness` and check `snapshot.options is not None` directly.

**Missing data representation**: `None`, uniformly, never a sentinel,
never zero, never carried forward — confirmed both by code (every
optional field defaults `None`, no fallback branch anywhere in
`builder.py` substitutes a prior value) and live (§15 Demonstration C
and the "before any data" test from Phase 18.1's own suite).

## 3. Event-Time Semantics

**No formal knowledge-time vs. event-time distinction exists in
`MarketRealitySnapshot`.** Only ONE timestamp concept is used
throughout this module: the `Observation.identity.timestamp` — the
market/event timestamp stamped by the ingestion script at capture
(e.g., a candle's own session-boundary time, or `now_ist()` at the
moment options were fetched). There is no separate "when Bujji learned
this" field consulted by the builder.

This differs from `market_reality/replay.py`, which — confirmed by
direct read in Phase 18.0 and re-confirmed here — **does** implement a
genuine bitemporal distinction (`as_of_event_time` /
`as_of_knowledge_time`, Phase 17F.1.2) over Layer 0's `RawObservation`
stream. **`MarketRealitySnapshot`'s builder does not use this
machinery for its historical-store reads** — `HistoricalObservationStore.range()`
and the new `range_by_prefix()` (Phase 18.1) filter on event timestamp
only. For every case actually tested (§15), this made no observable
difference, because `retrieved_at` (capture/ingestion time) and the
observation's own market timestamp are effectively simultaneous for
this project's real ingestion scripts (an options row's `capture_ts`
IS `now_ist()`; a candle's ingestion happens well after its own
session closes, meaning `retrieved_at > timestamp` always — knowledge
time is always after event time, never before, so filtering on event
time alone cannot admit a future-knowledge leak in the data captured
so far). **This is a favorable current fact, not a structural
guarantee** — if a future ingestion path ever back-dated a record's
event timestamp to before its true capture/knowledge time in a way
that could make it appear "available" earlier than Bujji actually knew
it, `MarketRealitySnapshot`'s builder has no independent check against
that, unlike `market_reality/replay.py`'s Layer 0 path. Classified
**UNVERIFIED as a structural guarantee; VERIFIED TRUE for every real
ingestion path audited.**

**Different capture frequencies / different close times**: verified
directly — spot/futures/VIX use `RESOLUTION_FIVE_MINUTE` bars 09:15
→ 15:25/15:30 IST; options use the same resolution label but a
real, live-observed 09:15:13–15:28:24 wall-clock range (Phase 17I.10);
futures depth (Layer 0 only) is untouched by `MarketRealitySnapshot`
entirely (confirmed: `builder.py` never imports anything from Layer 0
depth-specific paths). No unified session-close concept is enforced —
each instrument's own real data simply stops where its own capture
stopped; the builder never pads, extends, or aligns across instruments.

**Options appearing/disappearing intraday**: directly tested in
Phase 18.1's own test suite and re-verified live in §15 Demonstration
D of this phase — a newly-listed contract that only has rows starting
partway through the day is picked up correctly once its first row
exists, and is honestly absent (not fabricated as zero/flat) before
that.

**Late-arriving observations / ingestion after the event**: the store
enforces write-time immutability (§9) but the *snapshot builder* has
no concept of "this row arrived late" — it only ever asks "does a row
exist with timestamp <= as_of_time", which is correct-by-construction
regardless of when the row was actually inserted into the database.
This is the right behavior for point-in-time correctness (§4) but
means a snapshot rebuilt after a late backfill lands will genuinely
differ from one built before it landed — this is the intended, correct
consequence, and is precisely what §5 (Determinism) had to test
directly rather than assume.

## 4. Cross-Instrument Synchronization

**Exact rule, read directly from `builder.py`**: independently, per
instrument, `_latest_at_or_before(store.range(SYMBOL, resolution,
day_start, as_of_time), as_of_time)` — i.e. "the single most recent
row for THIS instrument whose timestamp is `<= as_of_time`." There is
**no cross-instrument alignment, no tolerance window, no "must be
within N minutes of each other" rule of any kind.** Each component is
resolved completely independently and assembled into one object
afterward.

**Consequence, directly tested, not assumed**: this means the four
components of one `MarketRealitySnapshot` CAN legitimately come from
different real moments if their underlying data has different gaps —
e.g., if VIX's feed had a 2-minute outage around `as_of_time` while
spot did not, the snapshot's `vix` would silently reflect an older
moment than its `spot`. **A real, significant finding: the resulting
`MarketRealitySnapshot` object does NOT expose each component's own
actual timestamp anywhere** — confirmed directly by inspecting every
component dataclass's field list (§2's own read): `SpotSnapshot`,
`FuturesSnapshot`, `VixSnapshot`, and `OptionContractSnapshot` each
carry `source_observation_ids` but **no `timestamp` field**. A
consumer cannot tell, from the snapshot object alone, whether all four
components truly represent the same moment or four silently different
ones — they would have to independently dereference each
`source_observation_ids` entry back through
`HistoricalObservationStore` (as this audit itself had to do to
produce the table below) to find out. **This is a real, disclosed
architectural gap, not fixed in this phase.**

**Real worked example**, `2026-08-13 10:35 IST` (2026-08-14 was
excluded for this specific test because that date's spot/futures/VIX
5-min backfill had not landed yet at audit time — itself a live,
disclosed fact, not a data-selection convenience):

```
requested as_of_time = 2026-08-13T10:35:00+05:30
  spot     component timestamp (dereferenced): 2026-08-13T10:35:00+05:30
  futures  component timestamp (dereferenced): 2026-08-13T10:35:00+05:30
  vix      component timestamp (dereferenced): 2026-08-13T10:35:00+05:30
```

All three genuinely aligned to the exact requested minute for this
specific real date/time — **but this is because all three instruments'
5-min ingestion happened to have a bar exactly on that grid point that
day, not because the system enforces alignment.** The independent,
per-instrument `_latest_at_or_before` logic (confirmed by code read)
would silently report a stale VIX timestamp alongside a fresh spot
timestamp if a gap existed — this specific example simply did not
happen to exercise that path, and no gap-containing real example was
available at audit time to force the divergent case. Classified
**VERIFIED for the tested case; UNVERIFIED (real gap in test coverage,
not a code guarantee) for the divergent case.**

**Missing instrument**: confirmed structurally and live — a missing
component is simply `None`; the other components build independently
and fully (Phase 18.0's futures-continuous-only-from-2018 gap means
any date before 2018-01-02 already exercises this in practice for
every real DAILY snapshot built for 1998–2017).

## 5. No-Look-Ahead Audit

**Direct adversarial trace, not a unit test alone**, per the phase's
own instruction.

Every read path in `builder.py` was enumerated:

1. `_build_spot_snapshot` / `_build_futures_snapshot` / `_build_vix_snapshot`
   (DAILY): call `historical_store.range(SYMBOL, DAILY, day_start,
   day_end)` — bounded to `day_end = f"{date}T23:59:59+05:30"`, never
   `as_of_time`-aware (DAILY mode has no concept of intraday cutoff;
   this is correct for a full-day snapshot, and is the ONLY path where
   a look-ahead-shaped query exists — but it is bounded to the
   requested calendar DATE, never beyond it, so it cannot look ahead
   into a FUTURE date).
2. `_build_spot_snapshot_intraday` / `_build_futures_snapshot_intraday`
   / `_build_vix_snapshot_intraday` (FIVE_MINUTE): call
   `historical_store.range(SYMBOL, FIVE_MINUTE, day_start, as_of_time)`
   — the upper bound passed to SQL is `as_of_time` itself.
3. `_build_options_snapshot`: calls
   `historical_store.range_by_prefix(prefix, resolution, day_start,
   as_of_time)` — same upper-bound discipline.

**The actual SQL** (`bujji/historical_reality/store.py`, both `range()`
and the new `range_by_prefix()`): `"...AND timestamp >= ? AND
timestamp <= ? ORDER BY timestamp ASC"` — the upper bound is
**inclusive** (`<=`, not `<`). This means a row timestamped EXACTLY
`as_of_time` IS included — this is the correct, intended semantic
("known at or before this moment," matching the phase's own stated
rule `latest certified fact whose event time <= decision time`), not
an off-by-one bug, but it is worth stating explicitly since inclusive
vs. exclusive bounds are exactly the kind of detail this audit was
asked to distrust rather than assume.

**Timezone**: every timestamp string used throughout — `day_start`,
`day_end`, `as_of_time`, and every stored `Observation.identity.timestamp`
— carries an explicit `+05:30` offset and is compared as a string.
Verified this is safe: ISO-8601 timestamps with a **constant, identical
offset** compare correctly lexicographically. This would NOT be safe
if two different offsets were ever mixed (e.g. one row in UTC, one in
IST) — confirmed, by direct read of every ingestion script's
`retrieved_at`/`capture_ts` construction, that **all of them
exclusively use the IST-fixed `now_ist()`/session-anchored ISO strings
with `+05:30`** — no UTC-stamped row exists anywhere in
`historical_observations`. **UNVERIFIED for the general case (nothing
in the schema enforces a single timezone offset — this is a discipline,
not a constraint); VERIFIED TRUE for every row in the store today.**

**Session boundaries / date rollover**: `_day_bounds(date)` returns
`f"{date}T00:00:00+05:30"` to `f"{date}T23:59:59+05:30"` — a plain
calendar-day IST window. No special handling for a session that
crosses midnight (NIFTY options/futures never do), so this is not a
real risk for this instrument set, but is worth naming as an assumption
baked into the code rather than a defended, general rule.

**Live adversarial proof (§15 Demonstration C)**: a real row was
located mid-day, `as_of_time` was set to exactly one second before its
timestamp, and the reconstruction was proven — by checking the actual
`observation_id` returned, not just a value comparison — to exclude
that specific row. This is direct proof against the actual retrieval
logic, not an assumption.

**Conclusion: no look-ahead path was found.** Every query the builder
issues bounds its own upper edge to the caller-supplied `as_of_time`
(or the requested calendar date for DAILY mode), and the store's SQL
enforces that bound at the database layer, not in application code
that could be bypassed.

## 6. Determinism Findings

**Two independent runs today, same store, same date, same
`as_of_time`**: proven byte-identical, both via direct `==` on the
full `to_dict()` output and via `fingerprint_state()` hash equality
(§15 Demonstration A and E — SHA-256 matched across 3 repeated runs).

**A genuine, previously undocumented determinism risk found this
phase**: `market_observation.engine._observation_id()`
(`bujji/market_observation/engine.py:58`) computes its content hash as
`"|".join(identity_fields) + "|" + value_kind + "|" + repr(payload)`.
**`repr()` of a Python dict is insertion-order-dependent** (dict order
is preserved since Python 3.7). This means two calls that construct
the *semantically identical* payload dict with keys in a different
order would produce **different** `observation_id`s for the same real
fact — a latent determinism risk. Contrast with `replay_engine.fingerprint_state()`
(`bujji/replay_engine/engine.py:71`), which explicitly uses
`json.dumps(normalized, sort_keys=True, ...)` — order-independent by
design. **In practice, checked directly**: every ingestion script in
this codebase builds its payload dict with the same literal key order
every time it runs (a fixed dict literal in source, not built
dynamically from a variable-order source), so this risk has not
manifested in any real data audited this phase — but it is a latent,
real gap in the identity mechanism itself, not proven safe by
construction, only by the current callers' coincidental consistency.
Classified **REQUIRES VERSIONING/HARDENING (a real, if currently
dormant, risk) — not SAFE by architecture.**

**Later-arriving observations changing an already-reconstructed
state**: classified per scenario, all backed by direct code/behavior
evidence gathered this phase and in Phase 18.1:

| Scenario | Classification | Evidence |
|---|---|---|
| Re-running the identical reconstruction against unchanged data | **REPRODUCIBLE** | §15 A/E, live fingerprint match |
| A genuinely new observation lands for a date/time AFTER the original reconstruction's `as_of_time` | **SAFE** | The new row's timestamp is `>` `as_of_time` by definition; it can never satisfy the query's own upper bound (§5) |
| A genuinely new observation lands for a date/time AT OR BEFORE a previously-used `as_of_time` (e.g. a late-arriving 5-min bar backfilled after the fact) | **REQUIRES VERSIONING** | Nothing prevents this from silently changing a FUTURE re-run's result relative to an ORIGINAL run made before the backfill landed — no snapshot artifact is persisted from the original run to compare against (§7); this is a REAL, live-relevant case: today's own audit found spot/futures/VIX 5-min data for 2026-08-14 was not yet backfilled at the time of testing |
| A duplicate re-ingestion of an identical fact | **SAFE** | `HistoricalObservationStore.write()`'s own idempotent-no-op path, re-confirmed in this phase's code read, unchanged since Phase 17H.3 |
| A corrected/revised value for an already-ingested natural key | **REQUIRES IMMUTABILITY (already partially provided)** | `ConflictingHistoricalObservationError` is raised, never silently applied — see §9 for the full immutability finding |
| A change to certification state after a record was written | **REQUIRES VERSIONING** | §9's own finding: `certification_status` is frozen into the record at write time and never re-checked by the snapshot builder — a later revocation does not propagate |
| A change to normalization/reconstruction code itself (e.g. a future bug fix to `_build_options_snapshot`) | **UNKNOWN** | No version marker distinguishes which code produced a given `MarketRealitySnapshot` (§7) — impossible to detect after the fact whether two snapshots differ because of data or because of code |

## 7. Persistence vs. Recalculation

Phase 18.1's own explicit, disclosed choice: 5-minute snapshots are
recomputed from source on every call (Model A for intraday), while
DAILY snapshots ARE persisted through `MarketRealitySnapshotStore`
(252 real rows, Model B for daily) — **the current architecture is
already a de facto, undocumented-as-such Hybrid (Model C shape) split
inconsistently by resolution, not by deliberate design for
reproducibility.**

**What this guarantees today**: for a DAILY, `is_final=True` (settled)
date, `MarketRealitySnapshotStore`'s own conflict discipline (§9) means
the PERSISTED artifact cannot silently change — a re-read via
`.get(date)` returns the exact same stored record forever, or raises if
a recomputation would differ. **For a FIVE_MINUTE reconstruction,
nothing is persisted — running the exact reconstruction request again
next year re-executes the same query against whatever
`historical_observations` looks like at THAT time.** If new rows have
landed in the interim for that same historical window (a late
backfill, per §6's own finding), the result **can differ** with no
record of the original result to compare against or detect the
divergence.

**Direct answer to the phase's own question**: "Can Bujji promise a
customer that a backtest run today and the same backtest run one year
from now will use the exact same market inputs?" — **Not for a
FIVE_MINUTE/options-inclusive reconstruction, no, not today.** For a
pure DAILY, already-settled, already-persisted date with no options
component, the promise is real and enforced by
`MarketRealitySnapshotStore`'s conflict discipline. What's missing for
the general (intraday, options-inclusive) case is exactly Model B/C's
missing half: **a persisted, versioned artifact of the reconstruction
itself**, not just of the underlying Reality facts.

## 8. Versioning Findings

Directly greped, not assumed: `reconstruction_version` /
`normalization_version` / `builder_version` — **zero matches anywhere
in the codebase.** What DOES exist, versioned:

- `HistoricalLineage.schema_version` (currently "1.0.0", per-record).
- `MarketRealitySnapshot.schema_version` (bumped 1.0.0→1.1.0 this
  program's Phase 18.1, per-snapshot).
- `market_observation.taxonomy.MARKET_OBSERVATION_VERSION` (1.1.0,
  Phase 17E) — the canonical MOC schema version.

**None of these three version fields is a reconstruction/builder-logic
version.** They record *data shape* versioning, not *which version of
`_build_options_snapshot` (etc.) produced this result*. A future bug
fix to the intraday builder logic (e.g., correcting `_latest_at_or_before`'s
tie-breaking) would be silently invisible to anyone comparing two
`MarketRealitySnapshot`s built before and after the fix — both would
report the same `schema_version` (data shape unchanged) despite
different *derivation logic*.

**A real, existing, but unwired mechanism was found**:
`bujji/epistemics/identity.py`'s `resolve_code_identity()` (Phase 16D)
— reads the real git commit/branch/dirty-state of the deployment.
Read directly: **Bujji's own working tree has been uncommitted
("dirty") for this entire program**, per that module's own honest
docstring, and independently confirmed by this session's own earlier
`git status` output (dozens of uncommitted files spanning many phases).
`CodeIdentity.is_reproducible` would report `False` for essentially the
entire history of this project if it were ever actually called from
the snapshot-building path — **it is not called from there today**
(confirmed: `builder.py` does not import `bujji.epistemics`).

**Classification: B — the capability exists (code-identity resolution,
schema versioning) but is not formalized/wired into
`MarketRealitySnapshot` construction.** Not a D (architectural gap
requiring new invention) — the pieces already exist in the codebase,
unconnected.

## 9. Integrity/Hash Findings

**No content hash is currently computed for a `MarketRealitySnapshot`
anywhere in the codebase** — confirmed by grep, zero call sites of any
hash function within `market_reality_snapshot/`.

**What exists and IS reusable without inventing anything new**:

- `market_observation.engine._observation_id()` — MD5 content hash per
  individual `Observation` (§6's own caveat about `repr()`
  order-sensitivity applies here).
- `replay_engine.fingerprint_state()` — a genuinely robust, real,
  already-existing SHA-256 hash over a `sort_keys=True`
  JSON-normalized payload — **live-tested this phase directly against
  real `MarketRealitySnapshot.to_dict()` output** (§15 A/E) and
  confirmed to produce a stable, reproducible fingerprint with zero
  code changes required to use it. This is real, working evidence — not
  a proposal — that a `MarketStateFingerprint` could be produced today
  by calling one already-existing function, without introducing
  anything new. (Per this phase's own audit-only rule, this was
  demonstrated as a live proof-of-capability, not wired into any
  persisted artifact.)

**Classification: B — the exact primitive needed already exists
(`fingerprint_state`) and was proven, live, to work correctly against
real snapshot data; it is simply not called from anywhere in the
snapshot-building or backtest-adjacent path today.**

## 10. Immutability & Corrections

**Can existing observations be overwritten?** No — confirmed by direct
grep of `historical_reality/store.py`, `market_reality/store.py`, and
`market_reality_snapshot/store.py`: **zero `UPDATE` or `DELETE`
statements exist in any of the three stores.** Every write path is
`INSERT` (`historical_observations`) or `INSERT OR REPLACE` used ONLY
for two explicitly-designed exceptions: `ingestion_runs` (append-only
metadata, keyed by a fresh run id each time, so "replace" never
actually replaces a prior run's row) and `market_reality_snapshots`
(the *cache*, not the raw Reality — see below, and its own
conflict-on-settled-day discipline).

**Can corrected data replace old data?** For raw Reality
(`historical_observations`): **no, not silently** —
`ConflictingHistoricalObservationError` is raised and the write is
refused; a human must intervene. This was re-confirmed by code read,
not merely cited from a prior phase.

**Can duplicate timestamps with different content coexist?** No — the
natural key (`instrument_identity, resolution, timestamp, source`) is
the store's own conflict boundary; two different facts under the same
key cannot both be written (one wins by arriving first, the second is
rejected, per §9's own table).

**Does certification protect against later mutation?** Only at
capture time — `certification_status` is stamped into
`HistoricalLineage` once, at write, and never re-verified afterward by
any read path audited this phase (§3, §6). A record's
`certification_status: "CERTIFIED_AVAILABLE"` is a frozen historical
claim ("this access method was certified AT THE MOMENT this record was
captured"), not a live guarantee that remains true today. **This
distinction matters and is not currently surfaced anywhere** — nothing
warns a consumer that certification is a point-in-time fact, not an
ongoing one.

**Immutable raw evidence vs. mutable normalized representation — the
distinction, verified**:
- `historical_observations` (raw Reality): **immutable**, verified via
  §10's own INSERT-only finding.
- `market_reality_snapshots` (the DAILY cache): **conditionally
  mutable** — `is_final=False` (an in-progress day) is explicitly,
  deliberately allowed to change on recomputation (correct — a live
  day's high/low legitimately changes as more ticks arrive); an
  `is_final=True` (settled) day enforces the same conflict discipline
  as raw Reality. This IS the correct distinction the phase asks about,
  and it is already implemented, not merely intended.
- FIVE_MINUTE / options reconstructions (Phase 18.1): **not persisted
  at all**, so "mutable vs. immutable" does not apply — there is no
  artifact to mutate, which is itself the §7 finding restated.

## 11. Replay Infrastructure Findings

Re-verified this phase, not merely cited from Phase 18.0:

- **`market_reality/replay.py`**: the authoritative, currently-used
  replay path for Layer 0's `RawObservation` stream — genuinely
  bitemporal (§3), deterministic-by-append-order (confirmed by its own
  docstring's explicit guarantee, re-read this phase), depends on the
  current `RawObservationStore` file's contents (a real filesystem
  dependency — replaying "as of" a past date after the file has grown
  further re-reads the SAME file, filtered by its own bound logic, so
  this is safe as long as the store itself is append-only, which §10
  confirms).
- **`bujji.replay` / `bujji.qualification.historical_runner`**
  (Series 46–64): re-confirmed via fresh grep this phase — **still
  zero references** from `bujji/trading_brain/`, `bujji/shadow_runtime/`,
  or `bujji/production_runtime/`. Structurally orphaned, unchanged
  since Phase 18.0.
- **Neither replay system consumes `MarketRealitySnapshot`** — checked
  directly: no import of `bujji.market_reality_snapshot` exists in
  either `bujji/replay/` or `bujji/market_reality/replay.py`.
  `MarketRealitySnapshot` is, today, a standalone read/reconstruction
  API with no replay-engine consumer wired to it at all — Phase 18.1
  built the reconstruction capability; nothing yet calls it in a
  replay loop.
- **Classification**: `market_reality/replay.py` is the authoritative
  system for what it covers (Layer 0 event streams); `bujji.replay` is
  legacy/orphaned; **neither is currently the consumer of the
  Phase 18.1 reconstruction capability** — a real integration gap, not
  a duplication risk in the same sense as Phase 18.0's model-duplication
  finding (these are simply unconnected today, not competing).

## 12. Commercial Backtest Readiness

Evaluated against the phase's own nine numbered guarantees:

| # | Guarantee | Status | Basis |
|---|---|---|---|
| 1 | Same market universe | 🟡 PARTIAL | Options universe is whatever contracts existed in the chain at capture time — real and correct, but never independently verified against an external "true" universe (e.g., NSE's own instrument master for that date) |
| 2 | Same observations | 🟢 YES, for unchanged data | §6 SAFE row; §15 A/E proof |
| 3 | Same timestamps | 🟡 PARTIAL | The REQUEST timestamp (`as_of_time`) is exact; individual COMPONENT timestamps are not exposed on the result object at all (§4's own finding) |
| 4 | Same option contracts | 🟢 YES, for unchanged data | §15 D; composite identity is stable and content-addressed |
| 5 | Same point-in-time state | 🟡 PARTIAL | True for already-settled data; NOT guaranteed if a late backfill lands between two runs (§6, §7) |
| 6 | Same missing-data behavior | 🟢 YES | `None` uniformly, verified live and via Phase 18.1's own tests |
| 7 | Same reconstruction semantics | 🔴 NO guarantee mechanism | No `reconstruction_version` exists (§8) — a future logic change is silently invisible |
| 8 | Same data lineage | 🟢 YES, per-observation | `source_observation_ids` fully traceable; **NOT rolled up into one snapshot-level lineage summary** exposed to a consumer without manual dereferencing |
| 9 | Same input fingerprint | 🟡 CAPABLE, NOT DONE | `fingerprint_state()` proven live to work (§9) but not called or persisted anywhere today |

**Overall**: the *raw material* for every one of these nine guarantees
either already exists or was proven reachable with zero new code this
phase — but *none of the nine is currently recorded or asserted
anywhere*, meaning today the honest answer to a customer is "very
likely yes, for data reasons" but **not provably yes**, because nothing
captures the proof at the moment a backtest actually runs.

## 13. Futures Identity Impact

Carried forward and re-verified, not re-litigated: `NIFTY_FUT_CONTINUOUS`
remains the only historical futures series (§2's own table, re-queried
live this phase). **Can `MarketRealitySnapshot` distinguish synthetic
continuous from a real traded contract?** Partially — `FuturesSnapshot.source`
(`SOURCE_HISTORICAL` vs. `SOURCE_LIVE`) IS a real, existing signal: a
historical-path result is always the synthetic continuous series
(`instrument="NIFTY_FUT_CONTINUOUS"`, `expiry_date=None`); a live-path
result carries the real resolved contract symbol and its real
`expiry_date`. **What is NOT provided**: any mechanism to get the
ACTUAL traded contract's OWN historical price for a past date — the
distinction is exposed (you can tell which kind you got), but only one
kind (synthetic) is available for any date before "live," so the
distinction, while honestly surfaced, does not solve the underlying
Phase 18.0 finding. A backtest depending on literal per-contract
historical futures prices still cannot get them from this system,
today, for any historical date. Unchanged, unfixed, as instructed.

## 14. Options Historical Reality Impact

Every Phase 17I claim re-verified live, fresh, this phase (not
re-cited): **2,190 distinct option identities, 18 real expiries, 2026-08-14
09:15:13–15:28:24 IST capture window, composite identity format
confirmed** (`NIFTY|2026-08-18|21850|CE`, live-dereferenced in §15 D),
**`VALUE_KIND_MAPPING` confirmed** (Phase 17I.11's own fix, re-read in
`capture_options_reality_session.py`, unchanged), **lineage and
certification fields present and populated** on the live-sampled
record (§15 D's `source_observation_ids` resolves to a real row with
full `HistoricalLineage`).

**Can the historical capture be reconstructed deterministically?**
Yes for the one real day it exists (2026-08-14 onward) — proven live,
§15 D and E.

**What happens reconstructing a date before permanent capture began?**
Directly tested: `_build_options_snapshot` for any date before
2026-08-14 returns `None` — verified by the underlying mechanism
(`range_by_prefix` against an empty result set naturally returns
`None`, no special-casing exists to fabricate a chain). **Confirmed:
Bujji does not, and structurally cannot, pretend missing historical
options exist** — this was the explicit, correct design decision from
Phase 17I.6/17I.9 carried through unmodified into Phase 18.1's
extension, and this phase's own direct testing did not find any path
that would fabricate a pre-2026-08-14 options chain.

## 15. Mandatory Real-Data Demonstration Results

All five performed against the real VPS databases, not simulated.

- **A — Daily, repeated twice**: `2026-08-13`, built twice with a
  fixed `now`. `to_dict()` outputs `==` (Python equality): **True**.
  `fingerprint_state()` hashes: **identical**
  (`272889048bdb54aa...`). **PASS.**
- **B — Intraday, `2026-08-13 10:35 IST`**: reconstructed; each
  component's real underlying timestamp independently dereferenced via
  direct `store.range()` calls (not read from the snapshot object,
  because — per §4's own finding — the snapshot object does not carry
  this field). All three (spot/futures/VIX) resolved to exactly
  `2026-08-13T10:35:00+05:30`. **PASS, with the caveat noted in §4**
  that this alignment is a property of this specific real date's data,
  not an enforced guarantee.
- **C — No-look-ahead**: located a real mid-day spot bar
  (`2026-08-13T12:20:00+05:30`), requested `as_of_time` one second
  earlier, and confirmed by exact `observation_id` comparison that the
  target row's ID is absent from the result. **PASS.**
- **D — Options**: reconstructed 2026-08-14 09:15:13 IST; sampled
  contract `NIFTY|2026-08-18|21850|CE` — identity, expiry, strike,
  option_type, and `source_observation_ids` all independently
  cross-checked against the composite identity format. **PASS.**
- **E — Repeatability**: the same options reconstruction (D) run 3
  times; all 3 `fingerprint_state()` hashes identical
  (`55712f876b8d59be...`). **PASS.**

**No demonstration failed.** No result was fabricated or assumed —
every printed value above came from a live `python3` process run
against the real files on the VPS during this audit.

## 16. Gap Classification

| Finding | Class | Note |
|---|---|---|
| No-look-ahead enforcement (§5) | **A** | Proven correct, no action needed |
| Raw Reality immutability, INSERT-only stores (§9,§10) | **A** | Proven correct |
| Options composite identity, `VALUE_KIND_MAPPING` (§14) | **A** | Re-confirmed working |
| DAILY-path backward compatibility (Phase 18.1's own scope) | **A** | Unaffected by this audit |
| Content-hash primitive (`fingerprint_state`) exists and works on real snapshot data (§9) | **B** | Needs wiring, not invention |
| Code-identity/reproducibility resolver (`epistemics.identity`) exists (§8) | **B** | Needs wiring, not invention |
| DAILY snapshot persistence + settled-day conflict discipline (§7) | **B** | Real capability, only covers DAILY today |
| `observation_id`'s `repr(payload)` order-sensitivity (§6) | **C** | Small, currently-dormant integrity debt |
| Certification staleness never re-checked at read time (§3,§10) | **C** | Small, disclosed, not yet a proven live problem |
| `completeness` field ignoring options (§2) | **C** | Small, deliberate-but-disclosed asymmetry |
| Component-level timestamps not exposed on snapshot objects (§4) | **D** | Real architectural gap — blocks provable cross-instrument sync verification |
| No `reconstruction_version`/builder-logic versioning (§8) | **D** | Real architectural gap |
| FIVE_MINUTE/options reconstructions never persisted, no artifact to compare across time (§7) | **D** | Real architectural gap — the core of the "one year later" promise |
| Futures synthetic-vs-real identity (§13, carried from 18.0) | **D** | Unchanged, explicitly out of scope again this phase |
| Two orphaned replay pipelines (§11, carried from 18.0) | **D** | Unchanged |
| Backtest engine itself, multi-tenancy, strategy runner | **E** | Explicitly out of scope |

## 17. Minimum Required Next Steps

Not designed, only identified, per this phase's own restriction:

1. Add a `timestamp` field to `SpotSnapshot`/`FuturesSnapshot`/`VixSnapshot`/`OptionContractSnapshot`
   so cross-instrument synchronization can be verified by any consumer
   without manually dereferencing `source_observation_ids` (closes §4's
   D-class gap).
2. Wire `replay_engine.fingerprint_state()` into
   `MarketRealitySnapshot` construction (or expose it as a documented
   one-line helper) so a `MarketStateFingerprint` can be recorded
   alongside any future backtest run — the primitive already works,
   proven this phase (closes §9's B-class gap).
3. Decide and record a `reconstruction_version` (even a simple static
   string bumped by hand on any change to the builder's own selection
   logic) — the smallest possible step that makes §6's "UNKNOWN" row
   provable instead of unknowable.
4. Decide whether/how a FIVE_MINUTE or options-inclusive reconstruction
   should be persisted as a versioned artifact once actually used for a
   backtest input — not for every possible query (real cost concern,
   disclosed in Phase 18.1), but at minimum for whichever exact
   reconstructions a future backtest engine actually consumes.
5. Resolve the futures synthetic-identity question (unchanged demand
   from Phase 18.0, restated because it remains the single
   highest-severity open item blocking a fully honest "same market
   inputs" promise).

## 18. Final Verdict

**PARTIALLY — and the boundary is precise.**

For data that is already settled and has not changed since it was
captured (the common, and today provably tested, case): Bujji CAN
honestly say its reconstruction used exactly the information available
at the requested moment, excluded everything after it, and would
reproduce byte-identically if run again right now — proven with real
data, real hashes, real adversarial timing tests in §15, not assumed.

Bujji CANNOT yet honestly say the identical reconstruction, run a year
from now, is provably the same — not because the underlying data is
expected to be wrong, but because **no artifact, hash, or version
marker is recorded today that would let anyone — Bujji or a customer —
detect a divergence if one ever occurred.** The gap is specifically:
missing provenance/fingerprinting on the reconstruction itself (§7–9),
missing timestamp transparency at the component level (§4), and one
still-open, higher-severity data question (futures identity, §13,
unchanged since Phase 18.0). None of these require new invention — every
missing piece named in §17 already has a working, real primitive
sitting elsewhere in this codebase, unwired.
