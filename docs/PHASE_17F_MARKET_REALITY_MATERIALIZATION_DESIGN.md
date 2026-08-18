# Phase 17F — Market Reality Materialization Layer: Architecture Audit & Design

**Status: DESIGN ONLY. No code. No implementation.**

Phase 17E (Layer 0 Raw Observation Store) is complete: 78 tests, full
regression green at 5,250. This document audits the dependency order,
designs the materialization layer that turns Layer 0 observations into
deterministic derived datasets, and defines the Market Memory Engine.

> Make Bujji capable of remembering what markets actually did, before
> teaching it what markets might do next.

---

## Part 1 — Dependency Order Audit

### 1.1 The target chain

```
Raw Reality  →  Market Time Series  →  Market Memory  →  Market Context
             →  Market Intelligence  →  Strategy Selection  →  Execution
```

### 1.2 Verdict

**Layers 0→1 are now correctly ordered and correctly founded.** Layer 0
exists, is immutable, certification-gated, lineage-complete, and
replayable. Nothing above it can be built without it, and nothing in it
depends on anything above it. That is the first time in this project's
history the foundation has been genuinely load-bearing.

**Above Layer 1, four architectural mistakes remain.** All four are
pre-existing, none was introduced by 17E, and all four must be resolved
in or before 17F — because each one, left alone, will silently corrupt
the memory layer this phase exists to build.

### 1.3 Mistake 1 — `Candle` has no lineage. This breaks the rebuild proof.

**Severity: blocking for 17F.**

`market_timeseries.models.Candle` carries `instrument, kind, interval,
window_start, window_end, open, high, low, close, volume, tick_count,
open_interest, schema_version`. It has **no field recording which Layer 0
observations produced it**.

Phase 17D Part 2.1 requires every derived record to carry forward the
`transformation_history` of its inputs and append its own step. A `Candle`
cannot do that today. Without it:

- `REPLAY_VERIFIED` (17D Part 5) cannot be proven — there is no chain to
  resolve back to Layer 0 record ids.
- A candle produced by a *changed* aggregation rule is
  indistinguishable from one produced by the original rule.

**Resolution: 17F must extend the derived-record schema with a lineage
block before any materializer writes a single row.** This is not
optional polish; it is the difference between a derived store and an
auditable one.

### 1.4 Mistake 2 — No `as_of` on any read path. Look-ahead is not a type error.

**Severity: blocking for 17F.4/17F.5.**

`CandleStore.recent()` and `.range()` take no `as_of`. Phase 16A designed
this ("`as_of` is a required parameter on every read — a caller cannot
forget it, so look-ahead becomes a type error rather than a review
comment") and it was never implemented.

Level-interaction memory is *precisely* the place this matters: computing
"what happened after price touched 24,500 on 2026-03-14" while able to
see 2026-08-12 data is the canonical look-ahead bug, and it produces
memory that looks brilliant and is worthless.

**Resolution: `as_of` becomes mandatory on every Layer 1 and Layer 2 read
before Layer 2 is populated.**

### 1.5 Mistake 3 — `context_window` sits on derived intelligence, not on memory. This is an inversion.

**Severity: architectural; must be named now, resolved later.**

`bujji/context_window/` is literally named "Market Context Window" (Phase
11) and produces `HistoricalContext`, `SessionContext`, `HorizonSummary`.
Its own docstring states it "consumes a plain list of already-persisted
per-cycle record dicts (the SAME shape `IntelligenceCycleRecorder` already
produces)".

So a *Market Context* layer already exists — and it is built on
**derived intelligence output**, not on market memory. In the target
chain, Context sits *above* Memory and *below* Intelligence. The existing
package inverts that: it reads intelligence conclusions to synthesize
context.

This is the same class of error as the original one this whole Phase-17
arc was created to fix (intelligence built before perception), one layer
up. It is not urgent — `context_window` is honest about what it does and
harms nothing today — but **17F must not feed it, and a future phase must
decide whether it is re-founded on Layer 2 or retired.** Naming it now
prevents Layer 2 from being wired into an inverted consumer by accident.

### 1.6 Mistake 4 — Three packages named "memory", three different meanings.

**Severity: naming collision; resolve before building Layer 2.**

| Package | What it actually stores | Relationship to Market Memory |
|---|---|---|
| `bujji/market_regime_memory/` | **Current** regime state, persisted for restart continuity | Not historical fact memory. A state snapshot. |
| `bujji/outcome_memory/` | **Decision** outcomes — what happened to *trades Bujji took* | Different axis entirely: memory of *our actions*, not of *the market*. |
| `bujji/state_persistence/` (Observation Memory recovery) | Session state rehydration | Infrastructure, not memory. |
| **Market Memory (17F.4, new)** | **Market** facts: what price/volume/OI did at a level, independent of whether Bujji traded | The genuinely missing one. |

Three of four are named "memory" and none of them is market memory.

**Resolution: the new layer must be named unambiguously (proposal:
`bujji/market_memory/`), and its docstring must state the distinction
explicitly.** `outcome_memory` remembers what *we* did; `market_memory`
remembers what *the market* did. Conflating them would let trade outcomes
contaminate market facts — which is exactly how a memory layer becomes a
biased sample of only the situations we happened to trade.

### 1.7 One more thing to keep out

`bujji/market_timeseries/indicators.py` exists inside the Layer 1
package. Indicators are Layer 3+ concerns. 17F's materializers must not
import it, and the safety test must forbid it — otherwise the layer
boundary erodes from inside the package that defines it.

### 1.8 Corrected chain, with real package names

```
Layer 0  Raw Observation Store        bujji/market_reality/        ✅ 17E — DONE
            ↓
Layer 1  Market Time Series           bujji/market_timeseries/     ⚠ exists, unwired, no lineage, no as_of
            ↓
Layer 2  Market Memory                bujji/market_memory/         ❌ 17F — new, name to be claimed
            ↓
Layer 3  Market Context               (context_window/ is INVERTED — re-found or retire)
            ↓
Layer 4  Market Intelligence          msi_* packages               ⏸ frozen, untouched
            ↓
Layer 5  Strategy Selection           msi_strategy_*               ⏸ frozen, untouched
            ↓
Layer 6  Execution                    execution_engine             ⏸ frozen, untouched
```

---

## Part 2 — Phase 17F Design: Market Reality Materialization Layer

### 2.0 What a Materializer is (a definition with teeth)

A component qualifies as a Materializer **only if** it is a deterministic
function of Layer 0 records:

1. **Deterministic** — same inputs, same outputs, byte-identical, always.
2. **No wall-clock, no randomness, no network, no hidden state.** Any
   "now" must arrive as data, not be read.
3. **Replayable** — dropping its entire output and re-running it against
   Layer 0 reproduces that output exactly.
4. **Lineage-preserving** — every output record carries the Layer 0
   observation ids it derived from, plus its own `calc_version`.
5. **No look-ahead** — reads are `as_of`-scoped; a materializer computing
   a fact for time T may not read any observation after T.
6. **Pure where possible** — stateful accumulation is permitted only
   where genuinely required (streaming aggregation), and then only in a
   form whose output depends solely on the ordered input sequence.

A component that cannot satisfy these **is not a Materializer** and does
not belong in Layer 1/2 — it belongs at Layer 3+, where interpretation
lives. This is a structural definition, not a testing preference.

### 2.1 `calc_version` — the mechanism that makes staleness detectable

`epistemics.lineage.calc_version_for(definition_source, parameters)`
already exists and produces a **content hash of the calculation**, not a
hand-maintained integer. Its own docstring states why: "a silently-drifted
version is worse than none, because it makes two incompatible definitions
look like one series."

Every materializer stamps its `calc_version` on every record it produces.
Consequences, all of them free:

- Changing a materializer's logic changes its `calc_version`
  automatically — nobody can forget to bump it.
- Derived records produced under an old `calc_version` are mechanically
  identifiable and can be re-materialized.
- The `REPLAY_VERIFIED` proof can distinguish "the rebuild differs
  because the code changed" from "the rebuild differs because something
  is wrong" — a distinction that is otherwise guesswork.

### 2.2 The materialization pipeline

```
Layer 0 Raw Observation Store  (immutable, as_of-scoped read view)
              │
              ▼
   ┌──────────────────────────────────────────┐
   │  MATERIALIZERS (deterministic, pure)     │
   │                                          │
   │  17F.1  Tick → Candle                    │──► Layer 1  candles
   │  17F.2  Futures Memory                   │──► Layer 1  futures stats
   │  17F.3  Option Chain                     │──► Layer 1  chain snapshots
   │  17F.4  Price Level Memory               │──► Layer 2  level interactions
   └──────────────────────────────────────────┘
              │
              ▼
   17F.5  Historical Context Query Engine   (read-only, sample-size honest)
```

Each materializer emits a **materialization manifest** entry per run:
`{materializer_id, calc_version, input_range, input_observation_ids,
output_records, run_at}` — the receipt that a given derived output came
from a specific, identifiable set of Layer 0 facts.

---

### 17F.1 — Tick → Candle Materializer

**Audit finding: this already exists and must be adapted, not rebuilt.**

`market_timeseries.aggregator.CandleAggregator` (Phase 15Q) is genuinely
good work. It already:

- floors windows to **wall-clock boundaries, not first-tick** — so a
  mid-session restart produces identical boundaries, and two instruments
  share a common time axis;
- **refuses to fabricate a bar from an empty window** — a gap stays a gap,
  never a synthetic flat candle;
- distinguishes `FormingCandle` (field named `last`, not `close`) from a
  settled `Candle`, so an in-progress bar cannot be silently consumed as
  history;
- reuses `live_observation` for tick-window mechanics including
  late/out-of-order ticks.

Those properties are exactly what a materializer needs. **Rebuilding this
would be the duplication error this project keeps guarding against.**

**What must change:**

| Gap | Change |
|---|---|
| Fed by an arbitrary caller | Feed it from Layer 0's `as_of`-scoped replay stream instead |
| Ticks discarded after folding | No longer relevant — Layer 0 already holds them permanently; the fold is now purely additive |
| `Candle` carries no lineage | Add `source_observation_ids` + `calc_version` to the derived record |
| Only 1m/5m | Add 15m, 1H, Daily; **each materialized independently from Layer 0**, never derived from a coarser Layer 1 bar, so a bug in one timeframe cannot propagate into another |
| Stateful (internal dicts) | Acceptable: output depends solely on the ordered input sequence. Must be proven by a replay-determinism test, not assumed. |
| Sources both `MARKET_TICK` and `CANDLE` kinds | A broker-provided `CANDLE` (historical echo) and a tick-aggregated candle for the same window must be stored **both, tagged**, never merged — with a `reconciliation_flag` if they disagree (17D Part 6.2) |

**Output:** Layer 1 candles, per instrument per timeframe, immutable,
idempotent, conflicting-content-raises (the existing `CandleStore`
discipline, preserved).

---

### 17F.2 — Futures Memory Materializer

**Purpose:** derive per-window futures statistics from Layer 0
`MARKET_DEPTH` and `QUOTE` observations.

**Inputs:** Layer 0 `MARKET_DEPTH` (the only kind carrying futures OI —
established live 2026-08-12) and `QUOTE` observations for futures
instruments.

**Outputs — facts only, per instrument per window:**

| Field | Definition |
|---|---|
| `oi_open` / `oi_close` | First and last observed OI in the window |
| `oi_change` | `oi_close - oi_open` — arithmetic on two observed values, recorded as a number |
| `volume_window` | Volume traded in the window |
| `price_open/high/low/close` | From the same window's candle |
| `bid_ask_spread_samples` | Observed spreads, raw |
| `depth_imbalance_samples` | `total_buy_qty` vs `total_sell_qty`, raw pairs |
| `observation_count` | Evidence density — how much we actually saw |

**Explicitly NOT produced here:** "long buildup", "short covering",
"accumulation", "distribution". Those are the classic four-quadrant
OI×price *interpretations* — they are Layer 3 conclusions. Layer 1 records
that OI rose 4.66% while price fell 0.46%; naming that pattern is
somebody else's job, later.

---

### 17F.3 — Option Chain Materializer

**Purpose:** turn Layer 0 `OPTION_CHAIN` observations into a queryable,
time-indexed snapshot series.

**Inputs:** Layer 0 `OPTION_CHAIN` observations.

**Outputs — per underlying per timestamp:**

| Field | Definition |
|---|---|
| `strikes[]` | Per strike: `strike, ce_oi, pe_oi, ce_volume, pe_volume, ce_ltp, pe_ltp, ce_bid/ask, pe_bid/ask` — exactly as observed |
| `atm_strike` | The strike nearest the observed underlying price. A *lookup*, not a prediction. |
| `total_ce_oi` / `total_pe_oi` | Sums |
| `pcr_oi` | `total_pe_oi / total_ce_oi` — **a raw ratio, not a sentiment label** |
| `spot_reference` | The underlying price recorded in the same observation |

**Explicitly NOT produced:** implied volatility, Greeks, max-pain,
"bullish/bearish positioning", support/resistance from OI walls. IV and
Greeks are already forbidden at Layer 0 by runtime rejection; they remain
forbidden here. If a later phase wants IV, it is a Layer 3 materializer
with its own model version — and its model choice must be visible in
lineage, not buried in a "raw" table.

---

### 17F.4 — Price Level Memory Materializer  *(Layer 2 — the new capability)*

**Purpose:** record, as fact, every interaction between price and a price
band. This is the component that makes the first intelligence question
answerable.

**Inputs:** Layer 1 candles (`as_of`-scoped) + 17F.2 futures stats +
17F.3 chain snapshots.

**Band definition:** fixed-width buckets (proposal: 50 points for NIFTY,
so 24,500 means the band 24,475–24,525). Width is a **parameter recorded
in `calc_version`** — changing it produces a demonstrably different
dataset rather than silently reinterpreting the old one.

**One `LevelInteraction` record per entry into a band:**

| Field | Definition — all measurements, no judgments |
|---|---|
| `level_band` | e.g. `24475-24525` |
| `entered_at` / `exited_at` | Event timestamps |
| `approach_direction` | `FROM_ABOVE` / `FROM_BELOW` — determined by the prior candle's close relative to the band |
| `bars_within_band` | Time spent interacting |
| `max_penetration` | Furthest price travelled beyond the far edge |
| `close_beyond_count` | How many bars closed beyond the band |
| `exit_direction` | `UPWARD` / `DOWNWARD` / `SESSION_END` |
| `volume_during` | Summed volume across interaction bars |
| `volume_vs_trailing_median` | Ratio to the trailing-N-bar median — a number, not "high volume" |
| `futures_oi_at_entry` / `_at_exit` / `_change` | From 17F.2 |
| `option_chain_ref` | Pointer to the nearest 17F.3 snapshot |
| `forward_refs` | Lazy pointers to candles at +1/+5/+20 bars — **resolved only once that time has genuinely elapsed** |
| `source_observation_ids` | Full lineage to Layer 0 |

#### The hard case: "rejection or acceptance"

This is the one place in 17F where interpretation could smuggle itself
into memory, so it gets handled explicitly rather than by instinct.

"Rejection" and "acceptance" are **not raw observations** — they are
classifications with a threshold. But they are also exactly what the
first question asks for. The resolution:

- Memory stores the **measurements** (`max_penetration`,
  `close_beyond_count`, `bars_within_band`, `exit_direction`) — pure
  numbers, no threshold, no opinion.
- Memory *additionally* stores a **mechanically-derived
  `interaction_outcome`** ∈ `{REJECTED, ACCEPTED, INDETERMINATE}`,
  computed by an explicit, parameterized rule (e.g. *rejected* = exited
  the band in the direction it came from with `close_beyond_count == 0`;
  *accepted* = ≥N consecutive closes beyond; else *indeterminate*).
- **The rule's parameters are part of `calc_version`.** Changing the
  threshold produces a visibly different dataset that can be recomputed
  from Layer 0 — it cannot silently rewrite history.
- `INDETERMINATE` is a first-class outcome, not a rounding error. A level
  interaction that is genuinely ambiguous is recorded as ambiguous.

What memory must **never** store: `"strong resistance"`, `"key level"`,
`"support holding"`, a strength score, or any ranking. `interaction_outcome`
is a measurement under a stated rule. "Strong resistance" is a judgment
about the future. The first is Layer 2; the second is Layer 3, and only
after Layer 2 has proven itself.

---

### 17F.5 — Historical Context Query Engine

**Purpose:** answer historical questions about stored facts. Read-only.

**Reuse — the pattern already exists.** `bujji/outcome_memory/query.py`
(Phase 15N) is the precedent to follow, close to verbatim:

- Every result carries `sample_size` **and** `status`
  (`SUFFICIENT` / `INSUFFICIENT_HISTORY`), with `MIN_SAMPLE_SIZE`
  enforced, so a caller "can never mistake a tiny sample for a confident
  conclusion."
- Strictly read-only; never writes, never mutates.
- AST-enforced boundary: never imports strategy selection, decision
  synthesis, trade intent, or execution.
- Its architectural rule transfers directly:
  `Market → Memory → (queries ABOUT history)`, **never**
  `Memory → Decision`. That feedback loop is a later phase with a much
  higher trust bar.

**Query surface (illustrative, not an API spec):** interactions for a
band; filtered by direction/outcome/date-range; forward-movement
distributions; volume and OI context per interaction. Every response
`as_of`-scoped.

---

## Part 3 — The First Intelligence Question: Minimum Data Path

> "NIFTY is currently approaching 24,500. Has this level mattered before?"

### 3.1 End-to-end path

```
Layer 0    QUOTE/MARKET_TICK (spot) ─┐
           MARKET_DEPTH (futures OI) ─┼─► immutable, certification-gated
           OPTION_CHAIN              ─┘
                    │  as_of-scoped replay
                    ▼
17F.1     candles (multi-timeframe, lineage-stamped)
17F.2     futures per-window stats
17F.3     chain snapshots
                    │
                    ▼
17F.4     LevelInteraction records for band 24475-24525
                    │
                    ▼
17F.5     query → LevelProfile{sample_size, status, facts...}
```

### 3.2 Each sub-question, and exactly what answers it

| Sub-question | Answered by | Source |
|---|---|---|
| How many historical touches? | `count(LevelInteraction)` for the band | 17F.4 |
| From which direction? | `approach_direction` distribution | 17F.4 |
| Rejection or acceptance? | `interaction_outcome` distribution + the raw measurements behind it | 17F.4 (rule versioned in `calc_version`) |
| Volume behaviour? | `volume_during`, `volume_vs_trailing_median` per interaction | 17F.1 → 17F.4 |
| Futures OI behaviour? | `futures_oi_change` per interaction | 17F.2 → 17F.4 |
| Option positioning? | `option_chain_ref` → strike-level OI/PCR at that moment | 17F.3 → 17F.4 |
| Time spent around level? | `bars_within_band` distribution | 17F.4 |
| Subsequent movement? | `forward_refs` resolved at +1/+5/+20 bars | 17F.1 → 17F.4 |

### 3.3 The honest answer shape

The system must be able to return, for example:

> Band 24475–24525. **17 interactions** between 2026-02-03 and
> 2026-08-11 (`status: SUFFICIENT`). Approached from below 11×, from
> above 6×. Outcome under rule `CV-a3f9…`: rejected 9, accepted 6,
> indeterminate 2. Median bars within band: 4. Median volume vs trailing
> median: 1.34×. Median futures OI change during interaction: +2.1%.
> Median move at +20 bars: −0.4%.

And, when history is thin, it must be equally willing to return:

> Band 24475–24525. **2 interactions** (`status: INSUFFICIENT_HISTORY`).

**It must never return "strong resistance."** Not because that statement
is necessarily wrong, but because it is a different kind of statement —
and the entire architecture depends on those two kinds never being stored
in the same place.

### 3.4 Honest precondition

Answering this question at all requires **historical data that does not
yet exist in Layer 0**. Today Layer 0 is empty: 17E built the store, not
the collector. The path above is only real once (a) a collector writes
live observations and (b) FYERS historical candles are backfilled as
`CANDLE`-kind Layer 0 records. Both are 17F/17G work and both are in the
sequence below. No amount of materializer design substitutes for
actually having observed the market.

---

## Part 4 — Market Memory Engine Architecture

### 4.1 The governing distinction

**Memory stores facts. Intelligence draws conclusions. They live in
different packages, and the boundary is enforced by test, not by
discipline.**

| Memory MAY store | Memory MUST NOT store |
|---|---|
| "touched 17 times" | "strong resistance" |
| "rejected 9 times under rule CV-a3f9" | "likely to reject again" |
| "median +20-bar move: −0.4%" | "bearish level" |
| "median OI change +2.1%" | "smart money distributing" |
| "sample_size: 2, INSUFFICIENT_HISTORY" | a confidence score |

The test of whether something belongs in memory: **could a
disagreeing analyst dispute it without disputing the data?** If yes, it
is a conclusion, and it belongs at Layer 3.

### 4.2 Structure

```
bujji/market_memory/
  taxonomy.py     bands, directions, outcomes, status vocabulary
  models.py       LevelInteraction, LevelProfile — frozen, no logic
  materializer.py 17F.4 — deterministic, as_of-scoped, lineage-stamped
  query.py        17F.5 — read-only, sample-size honest
  store.py        persistence (see 4.3)
```

### 4.3 Storage

Layer 2 is a **derived materialization**, never primary truth. It is
physically separate from Layer 0, fully rebuildable, and — per 17D Part
3.4 — the materializer holds **no write credential to Layer 0 at all**.
The rebuild test is the correctness test: drop Layer 2 entirely,
re-materialize from Layer 0/1, and every record must return
byte-identical.

Backend follows the existing precedent rather than inventing one: SQLite
with WAL, `synchronous=FULL`, idempotent writes and
conflicting-content-raises — the `CandleStore` pattern, which is already
correct.

### 4.4 Sample-size honesty is structural

`LevelProfile` carries `sample_size` and `status` as **required fields**,
following `OutcomeSample`'s existing design. A level touched twice and a
level touched fifty times must be visibly different objects, not two
similar-looking summaries. This is the single most important defense
against the memory layer becoming quietly misleading.

---

## Part 5 — Implementation Sequence

Priority order as specified: **A. Production safety → B. Data correctness
→ C. Replay proof → D. Memory usefulness.** No step begins before the
previous one's gate passes.

| Step | Scope | Gate | Priority |
|---|---|---|---|
| **17F.0** | Derived-record lineage block + `calc_version` stamping + mandatory `as_of` on Layer 1/2 reads | Existing 5,250-test regression stays green; `as_of` omission is a type error | A + B |
| **17F.1a** | Collector: wire live capture into Layer 0 (spot first — the only fully certified, lowest-risk instrument) | Observations land; completeness monitor reports HEALTHY; zero impact on any existing runtime path | **A** |
| **17F.1b** | Historical backfill as `CANDLE`-kind Layer 0 records, tagged, never merged with live | Backfilled and live records for the same window coexist, both tagged | B |
| **17F.1c** | Tick→Candle materializer: adapt `CandleAggregator` to read Layer 0; add timeframes | Byte-identical rebuild from Layer 0 alone | B + C |
| **17F.2** | Futures memory materializer (OI/volume/spread per window) | Rebuild proof passes | B + C |
| **17F.3** | Option chain materializer | Rebuild proof passes | B + C |
| **17F.RP** | **`REPLAY_VERIFIED` proof** — delete all derived data, rebuild from Layer 0, diff | Three-state result: `REPLAY_VERIFIED` / `REPLAY_PARTIAL` / `REPLAY_FAILED`, no ambiguity | **C** |
| **17F.4** | Price Level Memory materializer — NIFTY, daily first, then intraday | Rebuild proof; no-look-ahead proof (a profile computed `as_of` T must be identical whether run at T or a year later) | D |
| **17F.5** | Historical Context Query Engine | The first question answerable end-to-end, with every component traceable to a Layer 0 record id | D |
| **17F.V** | Answerability validation: the 24,500 question, answered from real stored data | An honest answer, including an honest `INSUFFICIENT_HISTORY` if that is the truth | D |

**Explicitly out of scope until 17F.V passes:** strategies, indicators, AI
reasoning, predictions, confidence scores, regime classification, any
`msi_*` modification, any Layer 3+ work. Layer 3 begins with a separate
document, a separate review, and a demonstrated Layer 2.

---

## Part 6 — Existing Code That Must Be Reused

Audited directly this session, not assumed.

| Asset | Location | Verdict |
|---|---|---|
| **`EventStore`** | `state_persistence/store.py` | **Reuse unmodified.** Append-only JSONL, per-record flush+fsync, atomic line writes, torn-trailing-line tolerance. Already Layer 0's durability; materialization manifests should use it too. |
| **`deduplicated_events()` / `PersistedEvent`** | `state_persistence/` | **Reuse unmodified.** |
| **`RecoveryReport`** | `state_persistence/models.py` | **Reuse the three-state shape** for the replay proof's vocabulary. |
| **`Observation` + `ObservationProvenance`** | `market_observation/models.py` | **Reuse unmodified.** Already carries `transformation_history`. |
| **`build_observation()`** | `market_observation/engine.py` | **Reuse.** The only site that mints an `observation_id`. |
| **`Lineage` + `calc_version_for()` + `look_ahead_violation()`** | `epistemics/lineage.py` | **Reuse — this is the backbone of 17F.** Content-hashed calc versions, accumulate-never-summarise `descends_from()`, and an explicit look-ahead predicate. Do not write a parallel lineage type. |
| **`resolve_calculation_identity()`** | `epistemics/identity.py` | **Reuse** for materializer identity stamping. |
| **`CandleAggregator`** | `market_timeseries/aggregator.py` | **Reuse and adapt — do not rebuild.** Wall-clock-aligned windows, no fabricated bars, `FormingCandle` separation. Change its *input* (Layer 0) and *output lineage*, not its arithmetic. |
| **`CandleStore`** | `market_timeseries/store.py` | **Reuse and extend.** Immutability discipline is already correct; add `as_of`, lineage columns, `FUTURES` kind, timeframes. |
| **`live_observation`** | `bujji/live_observation/` | **Reuse** for tick-window mechanics (late/out-of-order handling) — already a dependency of the aggregator. |
| **`outcome_memory/query.py`** | `bujji/outcome_memory/` | **Reuse the pattern**, not the code: `sample_size` + `status` on every result, `MIN_SAMPLE_SIZE`, AST-enforced read-only boundary. |
| **`market_reality`** | `bujji/market_reality/` | **Reuse** — 17F's sole input. Materializers read via its `as_of`-scoped replay. |

### Must NOT be reused or touched

| Asset | Why |
|---|---|
| `market_timeseries/indicators.py` | Indicators are Layer 3+. Importing it from a materializer erodes the boundary from inside the package that defines it. Forbid by safety test. |
| `context_window/` | Inverted (Part 1.5). 17F must not feed it. |
| `market_regime_memory/`, `outcome_memory/` (as storage) | Different meanings of "memory" (Part 1.6). Reuse `outcome_memory`'s *pattern* only. |
| Every `msi_*`, `trading_brain`, `execution_engine`, `production_runtime`, `mic_replay`, broker module | Frozen. Layer 3+ or infrastructure below Layer 0. |

---

## Part 7 — What 17F Will and Will Not Prove

**Will prove:**
- Derived data is byte-identically rebuildable from Layer 0 alone
  (`REPLAY_VERIFIED`) — the claim 17E could set up but not demonstrate.
- Every derived fact traces to specific Layer 0 observation ids.
- Historical questions about price levels are answerable from stored
  fact, with honest sample sizes.
- No-look-ahead is structurally enforced, not reviewed for.

**Will not prove, and must not claim:**
- That any level "matters" in a predictive sense. 17F establishes what
  *happened*; whether it *means* anything is Layer 3.
- That the memory is complete. It will be exactly as complete as the
  observations captured — and the Completeness Monitor will say so.

---

## Part 8 — Gate Status

| Gate | Status |
|---|---|
| Dependency-order audit | **Complete** — Part 1; four mistakes identified |
| Materializer definition + pipeline | **Complete** — Part 2 |
| 17F.1–17F.5 designs | **Complete** — Part 2 |
| First-question data path | **Complete** — Part 3 |
| Market Memory Engine architecture | **Complete** — Part 4 |
| Implementation sequence | **Complete** — Part 5 |
| Reuse audit | **Complete** — Part 6 |
| Design review / acceptance | **Pending — awaiting operator review** |
| Any implementation | **Not started. Not authorized.** |

### Open questions for review

1. **Layer 2 package name** — `bujji/market_memory/` proposed, to
   disambiguate from `market_regime_memory` and `outcome_memory`. Agreed?
2. **Band width** — 50 points for NIFTY proposed. This is a real modeling
   choice with real consequences for touch counts; it is recorded in
   `calc_version`, but the initial value is yours to set.
3. **`interaction_outcome` rule parameters** — the rejection/acceptance
   thresholds (Part 2, 17F.4). I have proposed a shape, deliberately not
   fixed the numbers.
4. **`context_window`** — re-found on Layer 2, or retire? Not urgent, but
   it should not drift further.
5. **Backfill depth** — how much FYERS history to pull. More history
   makes level memory meaningful; it also lengthens 17F.1b considerably.
