# Phase 17B — Market Reality Foundation: Architecture Audit + Design Proposal

**Status: DESIGN ONLY. No code exists for anything in this document.** This
phase audits everything the repository already has, and proposes — but does
not build — the canonical Market Reality Database (MRD).

> Data availability is not proven by code existence. Data availability is
> proven only by successful observation through the complete production path.
> — Phase 17A.5 doctrine, carried forward here.

> DATA IS THE HEART OF BUJJI. Never calculate intelligence without verified
> stored reality.

---

## Part 1 — Repository Architecture Audit

This section reports what was found by direct inspection of the live VPS
repository (`/opt/bujji/app`) — reading actual source, actual schemas, actual
on-disk files, and cross-checking every doc claim against code. Findings are
marked **DOC CLAIM** (asserted by a prior document, not independently
re-verified this pass) or **CODE-VERIFIED** (confirmed by reading the real
file/schema/import graph this session).

### 1.1 What already exists — inventory

| Layer | Package(s) | What it actually is |
|---|---|---|
| Canonical observation identity | `bujji/market_observation/models.py` | `ObservationIdentity` (deterministic md5 `observation_id`), `ObservationValue` (closed variant: SCALAR/OHLC/MAPPING/TEXT). Real, working. |
| Options observation model | `bujji/options_observation/models.py` | `OptionObservation` with explicit strike/expiry/option_type/underlying fields (not string-parsed). Bid/ask/sizes declared but **never populated from Bhavcopy**. |
| Futures observation model | `bujji/futures_observation/models.py` | `FuturesObservation` identity fields exist. **No live producer was ever built.** |
| Candle store | `bujji/market_timeseries/{models,store,aggregator,indicators,subscription}.py` | Real SQLite schema (detailed in §1.2), well-designed, immutable, idempotent — **built, tested, never wired into production. Zero `.db` file exists on disk anywhere.** |
| Generic event persistence | `bujji/state_persistence/{models,store,paper_broker,regime_memory}.py` | `PersistedEvent` / `EventStore` (JSONL, append-only) + pure-reducer rehydration. **The one genuinely reused persistence primitive in the codebase** — used by RegimeMemoryState, PaperBroker, Observation Memory, Outcome Memory. |
| Outcome memory | `bujji/outcome_memory/{models,engine,query,recovery}.py` | Reuses `EventStore`. Real integration (`epistemics/adapters.py`, `shadow_lifecycle/orchestrator.py`), but **zero real records exist** — no position has completed open→close in an unattended live session yet. |
| Raw-ish per-cycle snapshot | `market_snapshots.jsonl` (per shadow session dir) | The closest thing to a raw observation log that is actually written live, every cycle, by `ShadowSessionRunner`. Only 3 sessions exist on disk, all from 2026-08-06. Linear-scan JSONL, not queryable, not indexed. |
| Derived intelligence log | `intelligence_cycle.jsonl` | Per-cycle *conclusions* (PSI, MSSI, consensus, VSB, etc.) — explicitly derived, not raw. |
| Replay (Phase 15H, current) | `bujji/replay_engine/` | Replays derived reducers against `market_snapshots.jsonl`. No production importer. Admits VSB+downstream fields are only *referenced* from `intelligence_cycle.jsonl`, not *reconstructed* — because the spot candles a cycle used were never persisted. |
| Replay (Series 59, qualification) | `bujji/replay/` | Separate lineage — historical-corpus builder + legacy candle/backtest replay for the qualification pipeline. No real historical corpus has ever been populated (fixtures only). |
| MIC replay / counterfactual replay | `bujji/mic_replay/`, `bujji/msi_counterfactual_replay/` | Narrower, domain-specific replay (MIC v2 publication parity; phenomena counterfactuals). Not raw-tick replay engines. |
| Live tick feed | `bujji/broker/fyers_ws.py` (`FyersTickFeed`) | Real, reconnect-safe websocket subscription — but wired only into deprecated Stack A (`bujji/app.py`), and even there keeps only `symbol`+`ltp`, **discarding 21 of 23 full-mode fields** (bid/ask/OI/volume/exch_feed_time, etc.). |
| Legacy trade log | `data/bujji.db` (`trades` table) | Stack-A only (deprecated), 0 rows currently. |
| Position-group event log | `data/options_os_shadow_position_group_journal.db` | Structured, versioned schema exists; **0 rows** — never populated. |
| Domain journals | `bujji/journal/` (23 modules) | One append-only JSONL per subsystem (capital_brain, risk_brain, execution_engine, etc.). Actively written, ad hoc schemas, truth-of-record for their own domain only. |
| Instrument reference data | `data/instrument_master/fyers_fo_NSE.csv` | Public FYERS NFO symbol master (15MB). Source-of-truth reference, not observation. |
| Data certification records | `data_certification/*.json` | **Updated 2026-08-12T09:27:14Z, live market hours.** All three `CERTIFIED_AVAILABLE`: Spot; Futures (OI now obtained via the SDK's `depth()` method, not previously wired into the certification script's calls — `oi=12,645,685` confirmed live); Options (the prior `PARTIAL_CERTIFICATION` was traced to the certification script testing a stale, hardcoded strike ~5,000 points out-of-the-money — fixed by resolving a live ATM contract from real-time spot at run time; re-certified clean at `NSE:NIFTY2681824350CE`). See §1.6a below — this changes the Layer 0 write-gate in Part 2. |
| Knowledge/learning layer | `bujji/msi_market_learning/` (MLE) | 8 modules, full 12-stage promotion lifecycle, fully built and tested — **zero internal importers**. Fully disconnected. |

### 1.2 `market_timeseries` — the closest existing thing to an MRD, in detail

Real schema (SQLite, WAL mode, `synchronous=FULL`), table `candles`:

- Key: `(instrument, interval, window_start)`
- Columns: `instrument, interval, window_start, window_end, kind` (SPOT / VIX / OPTION — **no FUTURES kind**), `open, high, low, close, volume` (nullable, never coerced to 0), `tick_count` (evidence density), `open_interest` (options only, nullable), `schema_version`
- Indexes: `(instrument, interval, window_start DESC)`, `(kind, window_start)`
- Writes are append-only: identical-content writes are idempotent; conflicting-content writes to the same key **raise `ConflictingCandleError`** rather than overwrite.
- Only `FIVE_MINUTE` is actually exercised; `ONE_MINUTE` is a defined constant with no live producer.
- Raw ticks feeding the aggregator are **folded into candles and discarded** — never persisted anywhere.
- No `as_of` parameter exists on any read method (`recent()`, `range()`) — a caller cannot be prevented from reading data that wouldn't have existed yet at a given point in time (look-ahead is not a type error today, contrary to the design already proposed in 16A).

**This module is the right shape for a candle layer and should not be
rebuilt from scratch** — see Part 2's reuse decision below. Its core defect
is not its schema; it's that (a) nothing feeds it in production, and (b) it
has no raw layer beneath it.

### 1.3 What replay can and cannot do today

No component in the repository can replay a full trading day purely from
stored raw ticks or candles, because **no raw ticks are ever stored**, and
the candle store that could hold aggregated history is unwired and empty.
"Replay" today means re-running derived reducers against
`market_snapshots.jsonl`, which is itself incomplete for full downstream
reconstruction (Phase 15H's own disclosed finding: VSB and everything after
it cannot be independently reconstructed, because the spot candles a cycle
actually used were never persisted alongside it).

### 1.4 The team already wrote the answer, one phase ago

`docs/PHASE_17A_DATA_REALITY_AUDIT.md` §6 (dated one phase before this one):

> "No raw-market-data storage exists yet outside `bujji/market_timeseries/`
> ... there is simply nothing yet that stores raw ticks or raw
> broker-historical candles as an independent, append-only, never-modified
> layer beneath `market_timeseries`... the eventual raw layer must sit below
> `CandleStore`, not beside or inside it."

`docs/PHASE_16A...` self-correction after an earlier draft worried about
duplicating "seven observation subsystems":

> "The seven 'observation subsystems' are less duplicative than they
> appeared... this is specialisation, not duplication... No merges are
> recommended... The only genuinely missing component is a `TickStore` — a
> new capability, not a replacement for one."

`docs/PHASE_17A5_DATA_CERTIFICATION.md` — the binding gate this design must
respect:

> "Certified data enters the Market Reality Database. Non-certified data
> cannot feed MSI, MIC, strategy engines, memory, or decision synthesis...
> any future Phase 17B ingestion pipeline must check
> `validation_result == 'CERTIFIED_AVAILABLE'`... A `PENDING` or
> `NOT_CERTIFIED` source is not wired into storage, even provisionally."

These three statements, read together, already constitute the mandate for
this document: **build one new raw layer beneath the existing candle store,
reuse rather than replace what already works, and gate every write on
certification status.**

### 1.5 Concrete gaps against the Market Reality Database target

Target: ticks, prices, candles (multi-timeframe), futures (correct
contract/expiry), full options chain, OI (with history), volume, VIX — all
immutable, timestamped, replayable, queryable by instrument+time.

| Requirement | Status |
|---|---|
| Raw tick storage | **Missing entirely.** No `TickStore` class exists anywhere in the codebase. |
| Live tick capture wired to Stack B | Missing — `FyersTickFeed` only feeds deprecated Stack A, and drops 21/23 available fields even there. |
| Candles beyond 5-minute | Missing — only 5m is live-exercised; no 15m/30m/1H/Daily/Weekly. |
| Futures candle kind | Missing from `market_timeseries` schema (`kind` enum has no FUTURES value). |
| Futures OI | **Certification-reachable, production-missing.** OI is absent from the `quotes`/`ltp` and `optionchain` endpoints the broker's `_call()` dispatch table exposes, but confirmed present and correct via the SDK's `depth()` method (`oi=12,645,685`, live 2026-08-12) — `depth()` was never wired into `bujji/broker/fyers.py`'s action tables, so no production code path can reach it today. A small, well-scoped broker-layer addition (not attempted here, per this phase's "no code" instruction), not a FYERS data gap. |
| Options bid/ask | **Certification-reachable, production-missing.** Declared in schema, never populated by any live producer. The certification's original "missing" finding was a false negative — the test contract had drifted ~5,000 points out-of-the-money and was genuinely untraded; a live near-ATM contract (`NSE:NIFTY2681824350CE`) shows full bid/ask. Production code still needs a live producer wired to populate these fields — the certification only proves the broker path can supply them, not that anything currently stores them. |
| Options historical candles | **Resolved at the certification level** — the earlier `NO_DATA` was the same stale-strike artifact as above; a live ATM contract returns full historical candles. Still not persisted anywhere in production (see `market_snapshots.jsonl`/`market_timeseries` gaps above). |
| OI history (time series) | Missing entirely — only point-in-time OI exists anywhere (snapshots, certification). |
| VIX persistence | Missing — REST access path is reachable, nothing stores it. |
| Contract lifecycle (first_seen/expired_at/settlement) | Missing — an expired contract's series stopping is indistinguishable from a feed failure. |
| `as_of`-scoped, no-lookahead reads | Designed in a prior doc, not implemented in `market_timeseries.store` or anywhere else. |
| Market depth | Absent — no model, no capture. |

### 1.6a Certification update (2026-08-12, post-design) — Futures and Options both re-certified `CERTIFIED_AVAILABLE`

After this document's original certification snapshot (§1.6, `2026-08-11T23:46:35Z`,
pre-market), both `PARTIAL_CERTIFICATION` results were investigated live and
resolved — **the underlying broker/FYERS data reality is better than the
original certification run showed; the gaps were in the certification
script's own test parameters and endpoint coverage, not in FYERS data
availability**:

- **Futures OI**: not present in the `quotes`/`ltp` action's response for
  futures (confirmed — no `oi` key in the raw `v` dict), and not present in
  `optionchain`'s underlying/futures row either (has `fp`, no `oi`). It
  **is** present via the FYERS SDK's `depth()` method (`FyersModel.depth`),
  which was never wired into `bujji/broker/fyers.py`'s `_call()` action
  tables. `scripts/verify_fo_access.py` was updated to call `depth()`
  directly (bypassing `_call()`, same pattern already used for raw
  quote/historical access) and now records real, live OI
  (`12,645,685`, 2026-08-12 during market hours).
- **Options bid/ask/historical**: the certification script's `OPTION_SYMBOL`
  constant was a strike (29350) hardcoded once and never re-checked against
  spot — spot had since moved to ~24,320, making it a dead, ~5,000-point
  out-of-the-money contract with no market maker quoting it (`lp=0.05`,
  `bid=ask=0`, zero volume). The script now resolves a live near-ATM
  contract from real-time spot at run time via `InstrumentMaster.resolve_atm()`
  (the same resolution path production code already uses elsewhere) instead
  of a fixed symbol. Re-run against the resulting live contract
  (`NSE:NIFTY2681824350CE`) shows full bid/ask, historical candles, volume,
  and OI.

Both `data_certification/*.json` artifacts were overwritten with the new,
live results. **This is a certification-layer finding, not a production
capability** — see the updated §1.5 rows above: the broker layer
(`bujji/broker/fyers.py`) still has no `depth()` action wired for
production use, and no live producer populates options bid/ask anywhere in
the running system. What changed is the answer to "can Bujji reach this
data at all through some access path" (yes, for all three now), not "does
Bujji's production runtime currently capture and store it" (still no, for
anything beyond what §1.1–1.5 already describe).

### 1.6 One correction surfaced during this audit

`BUJJI_MARKET_DATA_ARCHITECTURE_ASSESSMENT.md`'s claim that
"`ShadowSessionRunner` imports ZERO observation packages" is stale.
Independently confirmed: `ShadowSessionRunner` → `IntelligenceCycleRecorder`
→ `MarketStateBuilder.process()` (called every cycle) → internally calls
`observation_bridge.py` / `option_observation_bridge.py` / `event_bridge.py`
/ `episode_bridge.py`, which do reach `market_observation`,
`options_observation`, and `live_market_events`. The observation packages
are reached transitively, just not via a direct top-level import. Noted here
so Phase 17B's design isn't built on a false "these packages are unused"
premise — flagging this drift for the team's docs is a separate, small
follow-up, not part of this design.

---

## Part 2 — Phase 17B Design Proposal: The Market Reality Database

### 2.1 Design principle

**Add one new layer. Reuse everything that already works. Change nothing
that already works.** Per the audit, the codebase does not need a rebuild —
it needs exactly one missing capability (a raw, immutable observation
layer) sitting *beneath* the candle store that already exists, with the
candle store becoming a materialized derivative of the new layer instead of
an independent, unfed island.

Three layers, each already justified by something either built or written
by the team:

```
LAYER 0 — RAW OBSERVATION LOG        (NEW — the actual gap)
    every tick, every quote poll, every option-chain snapshot,
    every OI reading — exactly as received, append-only, immutable

LAYER 1 — CANDLE / TIME-SERIES STORE  (EXISTING — market_timeseries)
    materialized aggregates derived FROM Layer 0,
    rebuildable from Layer 0 at any time, never the source of truth itself

LAYER 2 — DERIVED INTELLIGENCE         (EXISTING — everything downstream)
    MSI, MIC, strategy engines, memory, decision synthesis —
    unchanged by this phase, still forbidden from reading anything
    that isn't CERTIFIED_AVAILABLE, per Phase 17A.5's architecture rule
```

### 2.2 Layer 0 — Raw Observation Log (the new component)

**Purpose:** the single, permanent, append-only record of every market
observation as it was actually received from the broker — before any
aggregation, transformation, or interpretation. This is the "permanent
truth" the mission statement asks for.

**Storage pattern:** reuse the existing `state_persistence.EventStore`
pattern (JSONL, append-only, generic envelope) rather than inventing a new
persistence mechanism — this is the one primitive in the codebase already
proven across five phases (RegimeMemoryState, PaperBroker, Observation
Memory, Outcome Memory) to survive restart and rehydrate correctly via pure
reducers. A dedicated SQLite table (mirroring `market_timeseries`'s existing
schema conventions: explicit `kind`, `schema_version`, idempotent writes,
`ConflictingObservationError` on differing content at the same key) is the
likely eventual production backend for query performance at tick volume,
but the *event-sourced write pattern* should be identical to what already
works, not a new design.

**What it records, per observation** (conceptual field set — not a
schema/code artifact, a description of what must be captured):
- Identity: instrument, instrument_type (spot/future/option/index), exchange,
  segment, expiry (where applicable), strike (where applicable), option_type
  (where applicable) — reusing `ObservationIdentity`'s existing shape rather
  than the never-implemented `InstrumentIdentity` from 16H, since
  `ObservationIdentity` is real and working.
- Observation kind: TICK / QUOTE_POLL / OPTION_CHAIN_SNAPSHOT / CANDLE_ECHO
  (a broker-provided historical candle, recorded as received, distinct from
  a Layer-1 *derived* candle).
- Payload: LTP, OHLC (where the observation is a candle-echo), volume, OI,
  bid, ask, bid_qty, ask_qty — whichever fields the specific observation
  kind and instrument type actually carry. No field is fabricated or
  defaulted when absent; absence is recorded explicitly, not silently
  coerced.
- Provenance: exact source (`direct_sdk_fyers_broker_py` vs. any other
  access path — the same `access_method` vocabulary already established in
  the certification schema), the certification status of that source at
  write time, and the exchange-provided feed timestamp *and* the
  local-receipt timestamp (both, distinctly — the gap between them is
  itself diagnostic data, not noise).
- Integrity flags: the same checks already built and proven in
  `scripts/verify_fo_access.py`'s `_validate_candles()` (no future
  timestamps, no duplicates, ascending order, no impossible OHLC) apply here
  too, computed at write time, not deferred to read time.

**Write gate (non-negotiable, per Phase 17A.5's architecture rule):** a
source may only write to Layer 0 once its `access_method`/instrument pair
has an actual `CERTIFIED_AVAILABLE` record in `data_certification/`. **As of
2026-08-12T09:27:14Z (updated, live market hours — see §1.6a), all three —
Spot, Futures, and Options — hold `CERTIFIED_AVAILABLE` records** and are
gate-eligible at the certification level.

This is necessary but not sufficient for Futures/Options to actually write
to Layer 0, though: certification proves the data is *reachable*, not that
production has a *path* to capture it yet. Per §1.5/§1.6a, the specific
production gaps remain:
- **Futures OI** requires `depth()` to be wired into `bujji/broker/fyers.py`'s
  `_call()` action tables — it currently only exists in the certification
  script's own direct SDK access, not in any path production code can call.
- **Options bid/ask** requires a live producer to actually poll and persist
  them — the certification confirms FYERS will return them for a live ATM
  contract, but nothing in the running system does that polling today.

So the practical sequencing is: Spot can proceed to Layer 0 on
certification status alone; Futures and Options are certification-cleared
but still need the specific broker-layer/producer work above *before* their
write path exists — that work is Phase 17C+ scope, not something this
design document authorizes.

**Immutability guarantee:** identical to `market_timeseries`'s existing
pattern — same-content writes at the same identity+timestamp key are
idempotent; differing-content writes at the same key are a hard error, never
a silent overwrite. This is what makes Layer 0 a legitimate "permanent
truth" rather than just another mutable cache.

### 2.3 Layer 1 — Candle / Time-Series Store (extend, don't replace)

`bujji/market_timeseries` is architecturally sound and should be **kept as
the aggregate layer**, not rebuilt. Two changes are needed, described here
as requirements, not code:

1. **Source of truth inversion:** candles become a materialized view
   *derived from* Layer 0, rebuildable from raw observations at any time,
   rather than the only thing that exists. The current
   tick-in/candle-out/tick-discarded aggregator becomes
   tick-in/**write to Layer 0**/candle-out — the fold no longer destroys
   information.
2. **Schema extensions** (already implied by the gap list in §1.5, not new
   ideas): a `FUTURES` value added to the `kind` enum; an `as_of` parameter
   required on every read method, so a caller cannot accidentally read data
   that would not have existed yet at a given simulated point in time —
   this was already designed once (16A) and simply never implemented.

No change to the existing `candles` table's write-idempotency/immutability
behavior is needed — it already matches the discipline Layer 0 needs.

### 2.4 Layer 2 — Derived Intelligence (unchanged)

Out of scope for this phase entirely, by explicit instruction. The only
statement worth making here is the boundary condition: everything currently
reading from `market_snapshots.jsonl` or computing on the fly continues to
do so unmodified until a future phase explicitly migrates a given consumer
onto Layer 0/1 — this document does not propose touching MSI, MIC, strategy
engines, or any `msi_*` package.

### 2.5 Replay, reconsidered under this design

Once Layer 0 exists and is actually fed, `bujji/replay_engine/` (Phase 15H)
gains what it is currently missing: a full day could be reconstructed by
replaying Layer 0 observations through the same aggregation logic that
produces Layer 1 live, rather than depending on `market_snapshots.jsonl`
alone. This is a natural consequence of the design, not a new build — it's
the same `ReplayEngine` orchestration pattern already proven, pointed at a
complete raw source instead of an admittedly incomplete derived one.
`bujji/replay/` (the Series 59 qualification-corpus lineage) is a separate
concern and is not addressed by this proposal — it solves historical
*backtest corpus* sourcing, not live raw-observation storage.

### 2.6 What this document explicitly does NOT propose

- No merging or consolidation of the seven existing observation subsystems
  — 16A's correction stands; they are specialization, not duplication.
- No changes to `bujji/outcome_memory`, `bujji/portfolio_intelligence`, or
  any `msi_*` package.
- No connection of `bujji/msi_market_learning` (MLE) — it remains
  deliberately disconnected until its own phase.
- No live wiring, no ingestion pipeline code, no `TickStore` class, no
  schema migration — those are Phase 17C+ implementation work, gated on
  this design being reviewed and accepted.
- No writing of futures or options data into Layer 0 until the specific
  production-path work in §2.2/Part 3 (items 2–3: wiring `depth()`, building
  an options bid/ask producer) exists — certification status alone
  (now `CERTIFIED_AVAILABLE` for both, per §1.6a) proves the data is
  reachable, not that production has a path to capture it yet.

---

## Part 3 — What Phase 17C would need to do (forward-looking only, not scoped now)

Listed for context only — none of this is approved or started:

1. ~~Re-run `scripts/verify_fo_access.py` during live market hours to
   resolve whether futures OI and options bid/ask/historical are true
   FYERS gaps or artifacts of the pre-market test window.~~ **Done
   2026-08-12, per §1.6a — both were script-side artifacts (missing
   `depth()` action; a stale far-OTM test strike), not FYERS gaps. All
   three instruments are now `CERTIFIED_AVAILABLE`.**
2. Wire `depth()` into `bujji/broker/fyers.py`'s `_call()` action tables
   as a real, production-callable action (currently only reachable via the
   certification script's direct SDK access) — needed before Futures OI
   can actually flow into Layer 0.
3. Build a live producer for options bid/ask (nothing in the running
   system currently polls or stores them, even though the broker path can
   supply them).
4. Implement Layer 0 as a concrete `EventStore`-pattern module (or the
   SQLite variant described in §2.2), write-gated on certification status.
5. Wire `FyersTickFeed` (or a new subscription path) to Stack B, capturing
   all 23 available fields rather than the 2 currently kept.
6. Retrofit `market_timeseries`'s aggregator to write through Layer 0
   instead of discarding ticks after folding.
7. Add `as_of` to `market_timeseries.store`'s read methods.
8. Only after 2–7: re-evaluate whether `bujji/replay_engine/` can be pointed
   at Layer 0 for full-day reconstruction.

None of this is authorized by this document. This document is the design
proposal only, per instruction.

---

## Part 4 — Gate status

| Gate | Status |
|---|---|
| Repository architecture audit | **Complete** — this document, Part 1 |
| Phase 17B design proposal | **Complete** — this document, Part 2 |
| Spot certification | `CERTIFIED_AVAILABLE` |
| Futures certification | `CERTIFIED_AVAILABLE` (updated 2026-08-12 — see §1.6a; production `depth()` wiring still needed before write-eligible in practice) |
| Options certification | `CERTIFIED_AVAILABLE` (updated 2026-08-12 — see §1.6a; production bid/ask producer still needed before write-eligible in practice) |
| Design review / acceptance | **Pending — awaiting operator review** |
| Any implementation | **Not started. Not authorized.** |

Consistent with the standing instruction for this mission: no code was
written to produce this document. The certification script fixes and re-run
referenced in §1.6a were a separate, explicitly authorized action (fixing
and re-running `scripts/verify_fo_access.py`), not part of this document's
own production. No storage was created. No intelligence layer was touched.
This is architecture only, submitted for review.
