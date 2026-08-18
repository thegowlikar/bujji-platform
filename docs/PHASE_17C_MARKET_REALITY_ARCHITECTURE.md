# Phase 17C — Market Reality Architecture: Deep Review & Final Design

**Status: DESIGN ONLY. No code exists for anything in this document.** This
is the architecture review requested before any Layer 0–2 implementation
begins — it answers whether the build order is correct, whether Market
Reality should become the foundation before more intelligence/strategy
work, and what the final architecture looks like. It builds directly on
`docs/PHASE_17B_MARKET_REALITY_FOUNDATION.md` (the repository audit and
initial Layer 0/1 proposal) and the Phase 17A.5 certification results —
Spot, Futures, and Options are all now `CERTIFIED_AVAILABLE`.

> Bujji does not lack strategies. Bujji lacks market memory.

---

## Part 0 — Direct Answers

### 1. Are we building in the correct order?

**No, not until now — but the damage is small and fully recoverable.**

The repository audit (17B, Part 1) found ~150 subpackages, including a
complete strategy/intelligence stack (`msi_strategy_selection_foundation`,
`msi_trade_intent`, `msi_decision_synthesis`, and ~35 other `msi_*`
packages) built and unit-tested *before* any raw market data was ever
durably stored. That is backwards: those packages were validated against
synthetic fixtures and short-lived in-memory state, not against a real,
replayable record of what the market actually did.

The mitigating fact: every one of those packages is a **pure function over
explicit inputs** (`assess_all_families(mdi, mssi, consensus, vsb,
liquidity, ...)` — see `msi_strategy_selection_foundation/engine.py`), not
a component coupled to storage. Nothing needs to be rewritten to fix the
ordering mistake — it needs a real memory layer placed *underneath* them,
which they will then consume the same way they already consume synthetic
fixtures in tests. The cost of having built strategy logic first was
**validation debt, not architectural debt.** Phase 15H's replay engine
already surfaced this exact bill: VSB and everything downstream of it
"cannot be independently reconstructed" because the inputs it was tested
against were never durably, replayably stored.

**Correcting course now, before writing more strategy or indicator code,
is the right move and is cheap. Waiting longer is not.**

### 2. Should Market Reality become the foundation before more intelligence or strategies?

**Yes — as a hard gate, not a preference.**

Three independent reasons, each already demonstrated inside this
codebase, not hypothetical:

- **Replay is currently circular.** `bujji/replay_engine/` (Phase 15H) can
  only replay derived intelligence against other derived intelligence
  (`market_snapshots.jsonl` → recompute → compare to `intelligence_cycle.jsonl`).
  There is no raw ground truth underneath either side. A strategy that
  "passes replay" today has only been checked for internal consistency,
  never for correctness against what the market actually did.
- **The certification gate already enforces this principle one layer up.**
  Phase 17A.5's rule — "Certified data enters the Market Reality Database.
  Non-certified data cannot feed MSI, MIC, strategy engines, memory, or
  decision synthesis" — is meaningless if there is no Market Reality
  Database for certified data to enter. The gate exists; the thing it
  gates does not yet.
- **Every `msi_*` package is presently answering questions from a
  standing start each cycle.** None of them can ask "has this happened
  before," because nothing stores "before." A situation classifier
  (Layer 4) built on top of nothing will hallucinate structure with the
  same confidence it would report real structure — the failure mode is
  silent, not loud.

**Recommendation: a moratorium on new strategy, indicator, and signal work
until Layer 0–2 below exist and pass their own validation framework (Part
7).** This does not mean freezing the existing `msi_*` packages — they can
stay exactly as they are, dormant or shadow-running, until real memory is
available for them to consume.

### 3. What should the final Market Reality Architecture look like?

A seven-layer stack, in strict dependency order, each layer consuming only
the layer directly beneath it:

```
Layer 0 — Raw Market Observation Store     (NEW — this document, in depth)
Layer 1 — Market Time Series               (EXTEND existing market_timeseries)
Layer 2 — Market Memory Engine             (NEW — this document, in depth)
─────────────────────────────────────────────────────────────────────────
Layer 3 — Market Structure Intelligence    (interface only — NOT designed here)
Layer 4 — Market Situation Classification  (interface only — NOT designed here)
Layer 5 — Strategy Intelligence            (existing msi_* — untouched)
Layer 6 — Execution Intelligence           (existing execution_engine — untouched)
```

Layers 0–2 are Bujji's eyes, ears, and memory — the only thing this
document designs in depth, per instruction. Layers 3–6 are named only to
show that 0–2's design is sufficient to feed them; their internals are
explicitly out of scope.

---

## Part 1 — Grounding: What Already Exists vs. What's Missing

Carried forward from the 17B audit, restated only where it directly shapes
this design:

| Component | Status | Role in this design |
|---|---|---|
| `bujji/state_persistence.EventStore` | Real, proven across 5 phases (RegimeMemoryState, PaperBroker, Observation Memory, Outcome Memory) | **Reused** as Layer 0's write pattern |
| `bujji/market_timeseries` (CandleStore) | Real schema, immutable, idempotent, unwired in production | **Extended**, not replaced, as Layer 1 |
| `ObservationIdentity` / `ObservationValue` (`market_observation/models.py`) | Real, working | **Reused** as Layer 0's identity shape |
| Phase 17A.5 certification framework | Real, all three instruments now `CERTIFIED_AVAILABLE` | **Reused** as Layer 0's write gate |
| `depth()` wiring (`bujji/broker/fyers.py`) | Just landed — futures OI now reachable in production | **Feeds** Layer 0 futures observations |
| Raw tick storage | **Missing entirely** — no `TickStore` anywhere | **Built** as Layer 0's core |
| Historical price-revisit / memory index | **Missing entirely** — nothing in the repo answers "when was price here before" | **Built** as Layer 2 |
| Contract lifecycle tracking | **Missing** — expiry indistinguishable from feed failure | **Built** into Layer 0 identity |
| `as_of` no-lookahead reads | Designed once (16A), never implemented | **Required** at Layer 1 and Layer 2 |

---

## Part 2 — Layer 0: Raw Market Observation Store

### 2.1 Principle

**Raw market observation is the only truth. Nothing here is ever
discarded, coerced, or overwritten.** Every fact this layer records is
exactly what was received, tagged with exactly how and when it was
received. Interpretation starts at Layer 2, not before.

### 2.2 What gets recorded

One immutable record per observation, of these kinds:

| Observation kind | Source call | Instruments |
|---|---|---|
| `TICK` | Live websocket feed (`FyersTickFeed`, currently Stack-A-only — must be rewired to Stack B) | Spot, Futures, Options |
| `QUOTE_POLL` | `broker._call("ltp", ...)` | Spot, Futures, Options |
| `DEPTH_SNAPSHOT` | `broker._call("depth", ...)` (just wired) | Futures (OI), Options (future extension) |
| `OPTION_CHAIN_SNAPSHOT` | `broker._call("optionchain", ...)` | Full chain, per underlying |
| `HISTORICAL_CANDLE_ECHO` | `broker._call("historical", ...)` | Backfill only — a broker-provided candle, recorded as-received, never conflated with a live tick or a Layer-1-derived candle |
| `VIX_POLL` | `broker.get_vix()` | India VIX |

### 2.3 Record shape (conceptual — not a schema/code artifact)

Every record carries, without exception:

**Identity** (reusing `ObservationIdentity`'s real, working shape):
`instrument`, `instrument_type` (SPOT/FUTURE/OPTION/INDEX), `exchange`,
`segment`, `expiry` (where applicable), `strike` (where applicable),
`option_type` (where applicable).

**Payload** — exactly what was received, no defaults, no coercion: `ltp`,
`open/high/low/close` (candle-echo only), `volume`, `oi`, `bid`, `ask`,
`bid_qty`, `ask_qty` — absent fields are recorded as absent, never as
zero.

**Lineage** (the cross-cutting requirement, detailed fully in Part 7):
`source` (`fyers`), `access_method` (`direct_sdk_fyers_broker_py`, the
same vocabulary the certification schema already uses), `certification_status`
at write time, `observed_at` (exchange feed timestamp, when the broker
provides one) and `received_at` (local wall-clock at capture) — kept
**distinct**, never collapsed into one field; the gap between them is
itself diagnostic.

**Quality/confidence**: `integrity_flags` (future timestamp / duplicate /
impossible-OHLC checks, reusing `_validate_candles()`'s exact logic from
`scripts/verify_fo_access.py`), and a `confidence` tier derived
mechanically from certification status — `CERTIFIED_AVAILABLE` sources get
`confidence=HIGH`; nothing else is allowed to write at all (see write gate
below). This is not a judgment call at write time, it's a lookup.

**Contract lifecycle** (the gap 16G/16H flagged and nothing has closed
yet): `first_seen`, `expired_at`, `settlement_price` tracked per contract
identity, so a series stopping because a future/option expired is
distinguishable from a series stopping because the feed broke.

### 2.4 Write gate (non-negotiable)

A source may write to Layer 0 only if its `access_method`/instrument pair
currently holds a `CERTIFIED_AVAILABLE` record in `data_certification/`.
As of the most recent certification run, that's all three (Spot, Futures,
Options) — but the gate is a live check against the certification
artifacts at write time, not a one-time decision. If a future
re-certification ever demotes an instrument, Layer 0 writes for it stop
immediately, automatically, without a code change.

### 2.5 Immutability guarantee

Identical to `market_timeseries`'s existing, already-correct pattern:
same-content writes at the same identity+timestamp key are idempotent;
differing-content writes at the same key are a hard error, **never** an
overwrite. A correction to a previously-recorded observation is itself a
new observation with a `supersedes` pointer, not a mutation — Layer 0's
history is permanent, including its own mistakes.

### 2.6 Storage strategy

Reuse the `EventStore` pattern exactly (JSONL, append-only, generic
`PersistedEvent` envelope) as the **write path** — it's the one
persistence primitive in the codebase proven to survive restart and
rehydrate correctly via pure reducers, across five separate phases. A
SQLite mirror, schema-shaped like `market_timeseries`'s existing `candles`
table (`instrument, kind, observation_type, timestamp, ...` with a
composite primary key and the same idempotent-write/hard-error-on-conflict
behavior), is the **read path** for anything needing indexed, ranged
queries — built by replaying the JSONL log, exactly how `RegimeMemoryState`
and `PaperBroker` already rehydrate from `EventStore` today. The JSONL log
is the source of truth; the SQLite mirror is a rebuildable index, never
authoritative on its own.

### 2.7 Ingestion pipeline

```
FYERS (broker source)
   │
   ├── Live WebSocket (FyersTickFeed) ──► TICK observations
   │     (rewire from deprecated Stack A to Stack B; capture all
   │      23 available fields, not the 2 currently kept)
   │
   ├── Periodic REST poll (ltp/depth/optionchain) ──► QUOTE_POLL /
   │     (interval configurable per instrument; not a replacement    DEPTH_SNAPSHOT /
   │      for the tick feed, a fallback/cross-check for it)          OPTION_CHAIN_SNAPSHOT
   │
   └── Historical backfill (one-time or scheduled) ──► HISTORICAL_CANDLE_ECHO
         (explicitly tagged as backfill — never silently merged
          with live-captured data for the same window; see Part 7)
                    │
                    ▼
         Certification gate check (per instrument/access_method)
                    │
              ┌─────┴─────┐
         CERTIFIED    NOT CERTIFIED
              │              │
              ▼              ▼
      Layer 0 EventStore   Rejected, logged,
      (JSONL, append-only)  never silently dropped
              │
              ▼
      SQLite mirror (rebuilt by replay)
```

---

## Part 3 — Layer 1: Market Time Series

### 3.1 Principle

**Candles are derived products, not primary truth.** Layer 1 is a
materialized view over Layer 0 — at any point it can be dropped and
rebuilt from scratch by replaying Layer 0 through the same aggregation
logic, and that rebuildability is the test of whether Layer 1 is designed
correctly.

### 3.2 What changes from the existing `market_timeseries`

The existing schema, immutability behavior, and idempotent-write
discipline are already correct and are **kept as-is**. Three concrete
extensions, each already implied by the 17B gap list:

1. **Source inversion**: the aggregator currently folds ticks into
   candles and discards the ticks. It now folds ticks *that were already
   written to Layer 0*, so the fold is additive, not destructive.
2. **`kind` enum gains `FUTURES`** (currently SPOT/VIX/OPTION only).
3. **`as_of` becomes a required parameter on every read method**
   (`recent()`, `range()`) — a caller cannot forget it, so look-ahead
   becomes a type error, not a review comment. This was designed once
   (16A) and never implemented; it's implemented here.

### 3.3 Timeframes

Beyond the currently-live 5-minute: 1-minute (already a defined constant,
never exercised), 15-minute, 1-hour, Daily, Weekly. Each timeframe is an
independent materialization from Layer 0 — not derived from a
finer-grained Layer 1 timeframe, so a bug in one timeframe's aggregation
can never propagate into another's.

### 3.4 Processing pipeline

```
Layer 0 (raw observations, any kind)
        │
        ▼
Per-timeframe aggregator (1m / 5m / 15m / 1H / D / W)
        │  — idempotent: re-running over the same Layer 0 window
        │    produces byte-identical candles, always
        ▼
Layer 1 CandleStore (SQLite, extended schema, as_of-gated reads)
```

Backfill and live aggregation use the **same** aggregator code path — a
historical day and today's forming candle are produced by identical logic,
just pointed at different Layer 0 time windows. This is what makes replay
(Part 8) trustworthy: there is no separate "backtest aggregation" logic to
drift from the live one.

---

## Part 4 — Layer 2: Market Memory Engine

This is the layer that directly answers the target question: *"Nifty is
at 24500 today. When was it here before? How many times? What happened
after? Was volume accumulation or distribution? What did futures OI show?
What did options positioning show? Is today's behaviour similar to
previous events?"*

### 4.1 Principle — memory, not intelligence

Layer 2 **retrieves and links facts. It does not classify, score, or
signal.** "Accumulation or distribution" as a *label* is a Layer 3
judgment; Layer 2's job is to hand Layer 3 the exact raw
volume/OI/price-path facts needed to make that judgment honestly, and to
never make it first. This boundary is what keeps Layer 2 from becoming an
indicator — every field it produces is a recorded fact ("volume traded in
this window was X, OI change was Y"), never a computed opinion ("this
looks like accumulation").

### 4.2 Core components

**Price-Level Visit Index.** Every time price (from Layer 1, at a
configurable granularity — start with Daily and 5-minute) crosses into a
price bucket (e.g. NIFTY in 25-point buckets), a `PriceLevelVisit` record
is appended: `instrument`, `price_bucket`, `timestamp`, `direction`
(approaching from above/below), `session_context` (open/mid/close of that
session). This is a pure fact log — "price was here, at this time,
arriving from this direction" — nothing more.

**Outcome Linkage.** Each `PriceLevelVisit` carries forward-pointers to
what Layer 1 actually recorded in the N bars/days *after* that visit
(configurable horizons — e.g. next 1/5/20 daily bars). These pointers are
lazy references (instrument+timestamp+timeframe), not copied data — so
Layer 2 never duplicates Layer 1's truth, it only indexes into it. This is
what answers "what happened after" without Layer 2 having its own opinion
about what happened.

**Volume/OI Context Recall.** At each visit, the raw volume traded in
that window and the raw futures OI level/change (from Layer 1's
FUTURES-kind candles once populated) are recorded alongside the visit —
again as facts, not scored. "Was volume accumulation or distribution" is
answered by handing Layer 3 the actual volume-at-price history across all
visits to that bucket, not by Layer 2 pre-deciding the answer.

**Options Positioning Recall.** At each visit, a link to the nearest
`OPTION_CHAIN_SNAPSHOT` observation (Layer 0) within that session is
recorded — OI by strike, PCR as a raw ratio (not a sentiment label) — so
"what did options positioning show" is answerable by retrieving the actual
chain shape at that historical moment, not a derived interpretation of it.

**Similarity Index (explicitly Phase 2 of this layer, not immediate).**
A feature-vector index (price path shape, volume profile shape, OI
trajectory — raw, normalized, but not classified) over fixed-length
windows, enabling "was today similar to X" via nearest-neighbor lookup.
This is deliberately sequenced *after* the three components above are
built and validated, because it's the one component with any risk of
smuggling a judgment (a similarity metric is a modeling choice) into a
layer that is supposed to be judgment-free. It is named here so Layer 3's
eventual consumers know it's coming, not designed in this document.

### 4.3 Database design (conceptual)

Two new tables, both derived from and rebuildable from Layers 0–1 (never
primary truth themselves):

- `price_level_visits`: `visit_id, instrument, price_bucket, timestamp,
  timeframe, direction, session_context, volume_at_visit, futures_oi_at_visit,
  futures_oi_change_at_visit, nearest_option_chain_snapshot_ref,
  outcome_refs[1_bar, 5_bar, 20_bar]` — indexed on `(instrument,
  price_bucket, timestamp)` for the exact "when was price here before"
  query shape.
- `option_chain_snapshot_index`: a queryable index over the raw
  `OPTION_CHAIN_SNAPSHOT` records already in Layer 0 — per-strike OI,
  PCR, keyed by `(underlying, timestamp)` — so Layer 2's options recall
  doesn't need to linear-scan Layer 0's JSONL log at query time.

### 4.4 Processing pipeline

```
Layer 1 candle close (any timeframe)
        │
        ▼
Price-bucket check: did this candle's range cross into a new bucket?
        │
        ▼ yes
Append PriceLevelVisit (volume/OI context pulled from the same candle
and the nearest Layer 0 option-chain snapshot)
        │
        ▼
Outcome-ref backfill: once N bars have elapsed after a prior visit,
resolve that visit's outcome_refs (lazy, pointer-only)
```

Entirely incremental — no full-history recomputation on each new candle.
A full rebuild (from Layer 0/1 alone) is always possible and is the
correctness test for this layer, same as Layer 1's rebuildability test.

### 4.5 Example query shape (illustrative, not an API design)

Answering the target question becomes: look up all `price_level_visits`
for `(NIFTY, bucket=24500)`, across however much history Layer 0/1
contains; for each, follow the outcome_refs to see what Layer 1 recorded
next; read the recorded `volume_at_visit`/`futures_oi_change_at_visit` for
each; join to `option_chain_snapshot_index` for the positioning context at
each visit. Every part of that answer traces back to a Layer 0 fact —
nothing in the answer is synthesized.

---

## Part 5 — Layers 3–6 (interface only, not designed)

Named only to demonstrate Layers 0–2 are sufficient to feed them — their
internals, thresholds, and logic are explicitly out of scope for this
document and this phase.

| Layer | Would consume from Layer 2 | Not designed here |
|---|---|---|
| 3 — Market Structure Intelligence | `price_level_visits` (for support/resistance), volume/OI context (for structure classification) | Any threshold, scoring, or zone-detection logic |
| 4 — Market Situation Classification | Layer 3's output + the Similarity Index (once built) | Any regime label, pattern name, or classifier |
| 5 — Strategy Intelligence | Already exists (`msi_*`); would eventually consume Layer 4 instead of live-cycle-only inputs | Untouched — no changes proposed |
| 6 — Execution Intelligence | Already exists (`execution_engine`) | Untouched — no changes proposed |

---

## Part 6 — Cross-Cutting: Data Lineage & Validation Framework

### 6.1 Lineage — mandatory on every record, at every layer

`source`, `timestamp` (both `observed_at` and `received_at`, kept
distinct), `symbol`/`instrument identity`, `quality` (integrity-check
result), `confidence` (mechanically derived from certification status).
No record at any layer is permitted to omit any of these — this is
enforced at the write boundary (Layer 0's `_write` path), not left to
convention.

### 6.2 Historical vs. live merge policy

**Never merged without a tag, and never merged by overwrite.** A
`HISTORICAL_CANDLE_ECHO` backfill and a live-aggregated Layer 1 candle for
the same instrument/timeframe/window are both written, both kept, both
tagged with their `access_method`/`observation_kind` provenance. If they
disagree, that disagreement is itself recorded (a `reconciliation_flag` on
the affected Layer 1 window, pointing at both source records) — resolved
by an explicit, later reconciliation pass, never by one silently
replacing the other. This is a direct application of Layer 0's
immutability guarantee one layer up: a correction is a new fact with a
pointer, not a mutation.

### 6.3 Every inference separated from raw observation

Enforced structurally, not by convention: Layer 0 and Layer 1 have no
field anywhere that represents a judgment (no "trend," no "strength," no
"signal"). Layer 2 has no field that represents a *classified* judgment
(volume/OI are recorded as numbers, never as "accumulation"/"distribution"
labels). The first layer permitted to emit a labeled judgment is Layer 3,
which does not yet exist. This means a bug can be traced to a specific
layer boundary by asking one question: "is this field a fact or an
opinion" — if a fact-only layer ever contains an opinion, that's a defect
by definition, not a judgment call.

### 6.4 Validation framework (what Layer 0–2 must prove before Layer 3 begins)

Reusing and extending the certification framework's own discipline
(three-state classification, integrity checks, symbol-echo verification)
rather than inventing a new one:

1. **Write-gate compliance**: every Layer 0 record's `access_method`
   actually held `CERTIFIED_AVAILABLE` status at write time — auditable
   after the fact by cross-referencing `data_certification/` history.
2. **Rebuildability**: Layer 1, dropped and rebuilt from Layer 0 alone,
   produces byte-identical candles to what was live-aggregated. Layer 2,
   dropped and rebuilt from Layers 0–1 alone, produces byte-identical
   visit records. This is the direct test of "candles are derived, not
   primary truth."
3. **Integrity, at every layer**: the same no-future-timestamp /
   no-duplicate / no-impossible-OHLC checks already built for
   certification, run continuously on live Layer 0/1 writes, not just at
   a one-time certification moment.
4. **Answerability test**: the target question ("Nifty at 24500, when
   before, what happened, volume/OI/options context, similarity") must be
   answerable end-to-end from Layer 2 alone, with every component of the
   answer traceable to a specific Layer 0 record. This is the acceptance
   criterion for declaring Layer 2 "done," not a specific line-count or
   schema completeness checklist.

---

## Part 7 — Implementation Phases

Each phase is small, independently testable, and reversible — consistent
with the project's standing "narrowly-scoped, test-verified additions"
methodology. No phase here is authorized by this document; this is the
sequencing this document recommends once implementation is approved.

| Phase | Scope | Depends on |
|---|---|---|
| 17C.1 | Layer 0: `EventStore`-pattern raw observation log + SQLite mirror, write-gated on certification status. Spot only initially (already fully certified, lowest risk). | This document's acceptance |
| 17C.2 | Rewire live tick capture from deprecated Stack A to Stack B, capturing all available fields (not the 2 currently kept). Extend to Futures/Options observation kinds. | 17C.1 |
| 17C.3 | Layer 1: extend `market_timeseries` (FUTURES kind, `as_of` parameter, additional timeframes), retrofit aggregator to read from Layer 0. Prove rebuildability. | 17C.1–2 |
| 17C.4 | Historical backfill ingestion (tagged `HISTORICAL_CANDLE_ECHO`) for as much real history as FYERS's REST API exposes, into Layer 0/1. | 17C.3 |
| 17C.5 | Layer 2: `price_level_visits` + outcome linkage, single instrument (NIFTY), Daily granularity first. | 17C.3–4 |
| 17C.6 | Layer 2: volume/OI/options-positioning context linking, intraday granularity added. | 17C.5 |
| 17C.7 | Validation framework run: rebuildability proof, integrity proof, and the answerability test against the target question. | 17C.1–6 |
| (deferred) | Similarity Index (Layer 2's Phase 2) — only after 17C.7 passes. | 17C.7 |
| (deferred, separate mandate) | Layer 3 (Market Structure Intelligence) — a new document, new review, not scoped here. | 17C.7 |

---

## Part 8 — Explicit Non-Goals

- No strategies. No indicators. No signals. No scoring, labeling, or
  classification of any kind — those begin at Layer 3, which this
  document does not design.
- No code, no schema migration, no ingestion pipeline execution — this is
  architecture only, submitted for review.
- No changes to any `msi_*` package, `execution_engine`, or any existing
  intelligence layer.
- No similarity/embedding index yet — explicitly sequenced after the
  three fact-only Layer 2 components are built and validated.
- No resumption of strategy/indicator work until the Part 7 validation
  framework passes (the moratorium recommended in Part 0, Q2).

---

## Part 9 — Gate Status

| Gate | Status |
|---|---|
| Architecture review (this document) | **Complete** |
| Q1/Q2/Q3 answered | **Complete** — Part 0 |
| Layer 0–2 design | **Complete** — Parts 2–4 |
| Lineage/validation framework | **Complete** — Part 6 |
| Implementation phase plan | **Complete** — Part 7 |
| Design review / acceptance | **Pending — awaiting operator review** |
| Any implementation (17C.1 onward) | **Not started. Not authorized.** |
