# Phase 17D — Market Reality Data Contract

**Status: DESIGN ONLY. No code exists for anything in this document.**
Phase 17C (Market Reality Architecture) is accepted. This document is the
exact contract between reality capture (Layer 0) and every future
consumer — Layer 1 materializers now, Layers 2–6 later. Nothing above
Layer 0 may read anything that doesn't conform to this contract, and Layer
0 may not write anything that doesn't conform to it either.

> No strategies. No indicators. No trading logic. This is Bujji's
> permanent market memory foundation, and nothing else.

---

## Part 1 — Layer 0 Raw Observation Store: Schema

### 1.0 The common envelope

Every observation, of every kind below, is one immutable record built
from three parts. No observation kind may omit any part; no part may
contain a field belonging to another part.

```
OBSERVATION
 ├── IDENTITY     — what this observation is about (Part 1.1–1.6 below)
 ├── PAYLOAD      — exactly what was received, per kind (Part 1.1–1.6 below)
 └── LINEAGE      — how this observation came to exist (Part 2, in full)
```

**Hard rule, enforced at the write boundary, not by convention**: PAYLOAD
fields are exactly what the broker returned — no defaulting, no
coercion, no derived arithmetic (not even a subtraction like `oi_change`
unless the broker itself returned that exact field). An absent field is
recorded absent (`null`/not present), never `0` and never omitted
silently. Anything computed from a payload — a spread, an OI delta, an
implied volatility — is **not a Layer 0 field**; it belongs to a
materializer's output (Layer 1+), where its derivation is itself part of
that record's lineage.

### 1.1 Tick Observation

The highest-frequency, lowest-latency raw fact: one trade or one quote
update as delivered by the live feed.

| Field | Type | Notes |
|---|---|---|
| `instrument` | string | Exact broker symbol as subscribed, e.g. `NSE:NIFTY26AUGFUT` |
| `instrument_type` | enum | `SPOT` / `FUTURE` / `OPTION` / `INDEX` |
| `ltp` | float or null | Last traded price, if this tick carries one |
| `ltq` | float or null | Last traded quantity, if present |
| `volume` | float or null | Cumulative session volume, exactly as delivered |
| `bid` / `ask` | float or null | Top-of-book, if the feed's tick payload carries it |
| `exch_feed_time` | epoch or null | The exchange's own timestamp for this tick, if the feed provides one — distinct from `observed_at`/`received_at` in Lineage |

No field beyond what the live feed's tick payload actually contains — the
21 fields currently discarded by the Stack-A-only tick handler
(`fyers_ws.py`) must all be representable here; which subset is
*populated* on any given tick is whatever the feed actually sent, per
tick, not a fixed set.

### 1.2 Futures Observation

| Field | Type | Notes |
|---|---|---|
| `instrument` | string | e.g. `NSE:NIFTY26AUGFUT` |
| `underlying` | string | e.g. `NIFTY` |
| `expiry` | date | Contract expiry, resolved from the instrument master, not parsed from the symbol string |
| `ltp` | float or null | |
| `volume` | float or null | From the `quotes`/`ltp` action's `v.volume` |
| `oi` | float or null | **Only obtainable via the `depth` action** (confirmed 2026-08-12: absent from `quotes`/`ltp` and `optionchain` for futures) — a Futures Observation with `observation_kind=DEPTH_SNAPSHOT` is the only kind permitted to carry a non-null `oi` |
| `pdoi` | float or null | Previous-day OI, when the depth response carries it |
| `bid` / `ask` | float or null | From the `quotes`/`ltp` action or, when richer, the `depth` action's top-of-book |
| `ohlc` | struct or null | Only present on a `HISTORICAL_CANDLE_ECHO` kind — a broker-provided candle, never fabricated from ticks at this layer |
| `contract_lifecycle_flag` | enum or null | `FIRST_SEEN` / `NEAR_EXPIRY` / `EXPIRED` — set by the Collector cross-referencing the instrument master, not inferred from a gap in the feed |

### 1.3 Option Observation

| Field | Type | Notes |
|---|---|---|
| `instrument` | string | e.g. `NSE:NIFTY2681824350CE` |
| `underlying` | string | e.g. `NIFTY` |
| `expiry` | date | Resolved from instrument master |
| `strike` | float | |
| `option_type` | enum | `CE` / `PE` |
| `ltp` | float or null | |
| `volume` | float or null | |
| `oi` | float or null | From `optionchain` (confirmed reliable for options, unlike futures) |
| `prev_oi` | float or null | |
| `bid` / `ask` | float or null | Confirmed populated for a live, near-ATM contract; a stale/far-OTM contract legitimately has `bid=ask=0` — this is a real market fact, not a data gap, and must be recorded as-is, not treated as missing |
| `ohlc` | struct or null | `HISTORICAL_CANDLE_ECHO` only, same rule as Futures |
| `contract_lifecycle_flag` | enum or null | Same semantics as Futures — options expire weekly, this flag is what makes that distinguishable from a feed failure |

**Explicitly excluded from this schema**: implied volatility, delta,
gamma, theta, vega. These are computed, not observed — they belong to a
materializer's output, never to Layer 0, no matter how standard or
"basically raw" they may seem. This is the same discipline already
applied to `oi_change` above.

### 1.4 Depth Observation

The 5-level order book snapshot, from the `depth` action (just wired into
production this phase).

| Field | Type | Notes |
|---|---|---|
| `instrument` | string | |
| `bids` | list of `{price, volume, order_count}` | Up to 5 levels, exactly as returned |
| `asks` | list of `{price, volume, order_count}` | |
| `total_buy_qty` / `total_sell_qty` | float | |
| `oi` / `pdoi` / `oi_percent` | float or null | Present for futures; not yet confirmed for options via this action — record whatever the response actually contains, never assume |
| `ohlcv` | struct or null | The `depth` action's own embedded OHLCV, when `ohlcv_flag=1` is requested — recorded as a `HISTORICAL_CANDLE_ECHO`-equivalent sub-fact, not conflated with a live tick |

Depth Observations are the only observation kind currently proven to
carry futures OI — this is a load-bearing fact for the ingestion pipeline
(Part 4) and must not be silently duplicated by treating a Quote-Poll's
absent `oi` as "confirmed unavailable."

### 1.5 Volatility Observation

Raw, *published* volatility facts only — never a computed implied
volatility (excluded above) and never a Layer 2+ classification of
"high/low vol regime."

| Field | Type | Notes |
|---|---|---|
| `instrument` | string | `NSE:INDIAVIX-INDEX` today; extensible if FYERS ever exposes another published vol instrument |
| `level` | float | India VIX's own published value — a real, traded/published index level, not a derived figure |
| `prev_close` | float or null | |
| `ohlc` | struct or null | `HISTORICAL_CANDLE_ECHO` only |

If a future phase wants an ATM straddle premium as a volatility proxy,
that is **not** a new Volatility Observation kind — it's already fully
representable as an Option Observation (or a pair of them); no new schema
is needed for it.

### 1.6 Shared `observation_kind` tag

Every record of every schema above also carries exactly one of:

`TICK` / `QUOTE_POLL` / `DEPTH_SNAPSHOT` / `OPTION_CHAIN_SNAPSHOT` /
`HISTORICAL_CANDLE_ECHO` / `VIX_POLL`

This tag is what Part 6's replay/validation framework and Part 3's
storage tiers key off of — it is never inferred from which fields happen
to be populated, it's set explicitly by the Collector at capture time
(Part 4).

---

## Part 2 — Lineage Model

Every observation, regardless of kind, carries this block in full. No
field here is optional; a record missing any of these is not a valid
Layer 0 record and cannot be written (Part 4's Validator rejects it).

| Field | Type | Definition |
|---|---|---|
| `source` | string | `fyers` today — the broker/data vendor, not the access path |
| `access_method` | string | `direct_sdk_fyers_broker_py` — the exact code path, matching the vocabulary the certification schema already uses; this is what a future re-certification demotes/promotes |
| `observed_at` | ISO-8601 UTC or null | The exchange/broker's own timestamp for the event, when one is provided (tick `exch_feed_time`, a candle's own bar timestamp). Null when the source genuinely doesn't provide one (e.g. a bare LTP poll) — never backfilled from `received_at`. |
| `received_at` | ISO-8601 UTC | Local wall-clock at the moment this process captured the response. Always present. The gap `received_at - observed_at`, when both exist, is retained as diagnostic data, not discarded. |
| `instrument_identity` | struct | The full identity block from Part 1's relevant schema (not duplicated as separate top-level fields — referenced as one unit so identity and lineage never drift independently) |
| `certification_status` | enum | `CERTIFIED_AVAILABLE` / `PARTIAL_CERTIFICATION` / `NOT_CERTIFIED` — the status of this `(access_method, instrument_type)` pair **at write time**, read live from `data_certification/`, not cached at process start. A record's certification status is frozen at write time even if the source is later re-certified differently — history is never rewritten (Part 6.1). |
| `certification_ref` | string | The exact certification artifact filename + its own `timestamp` field, so any Layer 0 record's certification claim is independently auditable against a real, dated certification run — never a bare assertion. |
| `quality` | struct | `{integrity_ok: bool, issues: [string]}` — the same checks already proven in `_validate_candles()` (future timestamps, duplicates, ordering, impossible OHLC) run at write time on any record carrying an OHLC/timestamp series; for non-candle kinds, the equivalent applicable checks (e.g. `bid <= ask` when both present, non-negative volume/OI). |
| `confidence` | enum | `HIGH` / `LOW` — mechanically derived: `HIGH` iff `certification_status == CERTIFIED_AVAILABLE` and `quality.integrity_ok == true`; `LOW` otherwise. Never set by hand, never a judgment call — a pure function of the two fields above. |
| `transformation_history` | list of struct | See 2.1 below — the field that makes Part 6's replay proof possible. |

### 2.1 `transformation_history` — the traceability chain

At Layer 0, every record's `transformation_history` contains **exactly
one entry**: `{step: "RAW_CAPTURE", actor: "Collector", at: received_at}`.
This is not a placeholder — it is the contract's explicit statement that
Layer 0 performs zero transformation. Any component that appends a second
entry to a Layer 0 record's history is, by definition, not part of Layer
0 and has violated this contract.

Every derived record at Layer 1 or above **must** carry forward the
`transformation_history` of every Layer 0 record it was built from,
appending its own step (`{step: "5MIN_AGGREGATION", actor:
"CandleMaterializer", at: ..., inputs: [layer_0_record_ids...]}`), so any
derived fact is traceable, mechanically, back to the exact Layer 0 records
that produced it. This chain — not a separate audit log — is what Part 6's
"can we recreate it from Layer 0" proof actually checks.

---

## Part 3 — Storage Architecture

Four tiers, each with a distinct purpose and a distinct mutability
contract. No tier is a cache of another in the sense of being
independently correctable — each is either the source of truth (hot +
archive, together forming Layer 0) or fully rebuildable from it (replay
storage, derived materializations).

### 3.1 Hot storage

**Purpose**: fast, indexed reads for anything running live (Collector's
own recent-window sanity checks, a live materializer's incremental
aggregation, an operator inspecting "what just happened").

**Shape**: the `EventStore` JSONL append-only log (write path, proven
across five phases) plus a SQLite mirror (read path), exactly as
specified in 17B/17C — rebuilt from the JSONL log by replay, never
independently authoritative. Retention: a rolling recent window (e.g. the
current trading session plus N prior days — exact N is an operational
tuning decision, not an architectural one).

### 3.2 Historical archive

**Purpose**: permanent retention of everything hot storage rolls off,
with the exact same immutability guarantee, just colder and cheaper.

**Shape**: the same JSONL records, rolled into dated, append-only segment
files once they age out of the hot window — never rewritten, never
compacted in a way that loses a record. A segment file, once closed
(session ended, day rolled over), is read-only at the filesystem level, not
just by convention. The archive is not a different schema from hot
storage — it's the same Layer 0 contract at a different access-latency
tier.

### 3.3 Replay storage

**Purpose**: a stable, point-in-time-consistent read surface for the
replay/validation framework (Part 6) and for any future Layer 2+
backfill, so replay never races against live ingestion and never sees a
half-written record.

**Shape**: not a separate physical copy — an `as_of`-scoped read view over
hot storage + archive together, guaranteeing that a replay run pinned to
timestamp T sees exactly the Layer 0 records that existed at T and nothing
written after. This is the same `as_of` discipline Layer 1 already commits
to (17C §3.2) applied one layer down, at the source.

### 3.4 Derived materializations

**Purpose**: Layer 1 candles, and later Layer 2's memory indexes — never
primary truth, always reconstructable.

**Shape**: physically separate storage from Layer 0 (a materialization
must never be writable back into the Layer 0 store — that boundary is
enforced by the Materializer having no write credential to Layer 0's
store at all, not just by policy). Every materialization run produces a
**materialization manifest** entry: `{materializer_id, version, input_layer0_record_ids_or_range,
output_records_produced, run_at}` — this manifest is what Part 6's proof
checks against; it is the receipt that a given derived output was in fact
produced from a specific, identifiable set of Layer 0 facts.

---

## Part 4 — Ingestion Pipeline

```
FYERS
  │
  ▼
COLLECTOR
  — subscribes/polls per the configured schedule per instrument
  — tags observation_kind explicitly (never inferred)
  — stamps received_at; forwards observed_at exactly as the source gave it
  — performs ZERO interpretation, ZERO filtering, ZERO enrichment beyond
    identity resolution (looking up expiry/underlying from the instrument
    master — this is identity lookup, not a transformation of the payload)
  │
  ▼
VALIDATOR
  — checks certification_status live against data_certification/ for this
    (access_method, instrument_type) pair
  — runs the quality/integrity checks (Part 2's `quality` field)
  — computes confidence mechanically
  — ACCEPT: forwards the fully-lineage-tagged record downstream
  — REJECT: the record is NOT silently dropped — it is written to a
    separate, equally-permanent Rejected Observations log, carrying the
    same lineage block plus a `rejection_reason`, so a rejection is
    itself an auditable fact, not a silent gap in Layer 0's history
  │
  ▼ (accepted only)
RAW OBSERVATION STORE (Layer 0, Part 1 + Part 3.1/3.2)
  — append-only write, idempotent on identical content, hard error on
    conflicting content at the same identity+timestamp key
  │
  ▼
MATERIALIZERS
  — read ONLY from the Raw Observation Store (via the Replay Storage's
    as_of view, even when running live — there is no separate "live read
    path" that bypasses as_of semantics)
  — produce Layer 1 candles (17C §3), later Layer 2 memory records (17C §4)
  — write a materialization manifest entry per run
  — NEVER write back into Layer 0
```

**Why the Validator sits before the store, not after**: a record that
fails certification or integrity checks must never exist in Layer 0 at
all, even temporarily — Layer 0's contract is "everything here is
trustworthy raw truth," not "everything here plus a flag saying whether
to trust it." Untrustworthy data gets its own permanent, equally-honest
home (Rejected Observations), never a foothold in the trusted store.

---

## Part 5 — Replay and Validation Framework

### 5.1 The proof this framework exists to run

**"If we delete all derived intelligence, can we recreate it from Layer
0?"**

Concretely: given only the Raw Observation Store (hot + archive) and the
Materializer code, can every Layer 1 candle and every future Layer 2
memory record be regenerated, byte-identical to what existed before
deletion?

### 5.2 Procedure

1. Snapshot the current derived output (Layer 1, and later Layer 2) —
   this snapshot is the expected answer, kept only for comparison, not as
   a fallback.
2. Delete all derived materializations entirely (not archive them — a
   real deletion, because a proof that only works when a backup exists
   isn't a proof of rebuildability).
3. Re-run every Materializer against the Raw Observation Store alone,
   using the Replay Storage's `as_of` view pinned to the snapshot's own
   timestamp, so the rebuild sees exactly the Layer 0 state that produced
   the original.
4. Diff the rebuilt output against the Part 5.2-step-1 snapshot, field by
   field.
5. Cross-check every rebuilt record's `transformation_history` against
   its materialization manifest entry — the chain from Part 2.1 must
   resolve back to real, still-present Layer 0 record IDs, not a
   dangling reference.

### 5.3 Classification — three states, no ambiguity, mirroring the certification framework's own discipline

| Result | Meaning |
|---|---|
| `REPLAY_VERIFIED` | Rebuilt output is byte-identical to the snapshot, for every record, with every `transformation_history` chain resolving cleanly. |
| `REPLAY_PARTIAL` | Some records reconstruct correctly; others differ or have a broken lineage chain — itemized, never averaged into a pass/fail percentage that hides which specific records failed. |
| `REPLAY_FAILED` | The rebuild cannot run at all, or produces systematically different output (e.g. a Materializer turns out to depend on wall-clock time or unlogged external state — this itself is the most valuable possible failure mode to catch, since it means the Materializer was never actually deterministic). |

No `UNKNOWN`/`PENDING`-forever result is permitted once a replay run is
attempted — the same "no ambiguous status" discipline from Phase 17A.5's
certification classifier applies here without modification.

### 5.4 What makes a Materializer eligible for this proof at all

A component only qualifies as a "Materializer" under this contract if it
is a **pure, deterministic function of Layer 0 records** — no hidden
state, no wall-clock read except a value that itself arrived as part of a
Layer 0 record's own `observed_at`/`received_at`, no network call, no
randomness. This is a design constraint on Part 3.4 and Part 4's
Materializer stage, not just a testing convenience — a Materializer that
can't pass this proof by construction is not a valid Layer 1/2 component
under this contract, full stop.

### 5.5 Cadence

This proof is not a one-time acceptance gate — it is re-run on every
change to any Materializer's logic (a code change that alters how Layer 1
or Layer 2 derives its output must re-earn `REPLAY_VERIFIED` before that
change is trusted), and periodically against the full historical archive
as a standing regression check, the same way the certification framework
is meant to be re-run rather than trusted indefinitely from one pass.

---

## Part 6 — What This Document Does Not Do

- No strategies, no indicators, no trading logic — nothing in this
  contract computes a signal, a score, or a classification of any kind.
- No code, no schema migration, no running ingestion — this is the
  contract specification only, submitted for review.
- No changes to Layer 3+ (unscoped, per 17C).
- No implied volatility, Greeks, or any computed options analytic at
  Layer 0 — explicitly excluded in Part 1.3, deferred to a materializer.
- No resolution of *how* the Collector schedules polling intervals or
  *which* specific historical backfill window to prioritize — those are
  17E-scope operational decisions once this contract is accepted, not
  contract-level concerns.

---

## Part 7 — Gate Status

| Gate | Status |
|---|---|
| Phase 17C architecture | **Accepted** (prior) |
| Layer 0 schema (5 observation kinds) | **Complete** — Part 1 |
| Lineage model | **Complete** — Part 2 |
| Storage architecture (4 tiers) | **Complete** — Part 3 |
| Ingestion pipeline (Collector → Validator → Store → Materializers) | **Complete** — Part 4 |
| Replay/validation framework | **Complete** — Part 5 |
| Design review / acceptance | **Pending — awaiting operator review** |
| Any implementation | **Not started. Not authorized.** |
