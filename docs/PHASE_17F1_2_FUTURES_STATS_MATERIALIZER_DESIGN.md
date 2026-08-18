# Phase 17F.1.2 — Futures Statistics Materializer: Design

**Status: DESIGN ONLY. No code. Awaiting review before implementation.**

---

## Part 1 — Audit Findings

### 1.1 Futures observation structures — what actually exists

Verified by reading `bujji/market_reality/taxonomy.py`/`capture.py` directly
(current, post-17F.1 state), not assumed:

| Fact | Status |
|---|---|
| `INSTRUMENT_FUTURE` requires identity field `expiry` | Confirmed (`REQUIRED_IDENTITY_FIELDS`) |
| `KIND_MARKET_DEPTH` required payload keys | `("bids", "asks")` only — **`oi` is not a required key**, confirmed |
| Where futures price lives | `KIND_QUOTE` / `KIND_MARKET_TICK` (`ltp` field), same as any instrument |
| Where futures OI lives | **`KIND_MARKET_DEPTH` only.** Confirmed this engagement, live: `quotes`/`ltp` and `optionchain`'s underlying row both lack an `oi` field for futures; `depth()` is the only endpoint that returns it (`oi`/`pdoi`/`oi_percent`), now wired into `bujji/broker/fyers.py`. |
| Depth payload shape | `{bids: [{price, volume, order_count}, ...], asks: [...], oi: <float|absent>, pdoi: <float|absent>}` — verified against the actual live-captured response this engagement |
| Legacy `bujji/futures_observation/` package | Identity-only models; **no live producer was ever built** (established finding, 17B/17C audits) — not a candidate for reuse, confirmed still true, not re-investigated further since nothing has changed there |
| Absence semantics already enforced at Layer 0 | `MARKET_DEPTH` with `bids: []`/`asks: []` (present, empty) passes validation as a real fact, distinct from no `MARKET_DEPTH` record existing at all (17F.0.4 §3, Rule 2) |

**Conclusion: OI is confirmed single-sourced from `MARKET_DEPTH`.** Any
Futures Statistics field derived from OI must trace to a `MARKET_DEPTH`
observation specifically — never inferred from `QUOTE`/`MARKET_TICK`,
which structurally cannot carry it.

### 1.2 Existing statistics/analytics infrastructure — what's reusable

| Component | Finding | Reuse decision |
|---|---|---|
| `market_timeseries/indicators.py` | Pure functions over `List[Candle]`. Already implements `realised_volatility()` (stdev of close-to-close returns, "insufficient history is not zero" rule built in) and `atr()`. Computed on demand, never persisted back into Layer 1 storage. | **Reuse `realised_volatility()` directly** for the futures price-volatility statistic — do not reimplement variance math. |
| `market_timeseries/materializer.py` (17F.1, this engagement) | The Tick→Candle materializer. Establishes the exact pattern this phase must follow: pure function of `(reality_store, as_of, calc_version)`, no persistence inside the function, provenance stamped via `epistemics.lineage.calc_version_for`, gap-overlap cross-referenced against `CaptureEvent`s. | **Reuse the pattern wholesale** — same function shape, same provenance discipline, same read-only contract. Also reuse its **output directly**: futures price OHLC is already produced by this materializer for `kind=FUTURES` (added this phase). Futures Statistics must not recompute price OHLC — it references the existing Candle. |
| `market_timeseries/store.py` (`CandleStore`) | SQLite, WAL, PK includes `calc_version` (this phase's own change), mandatory `as_of`+`calc_version` on `recent()`/`range()`, idempotent-write/`ConflictingCandleError` discipline. | **Reuse the pattern, not the class.** The record shape (OI/depth/basis facts) is different enough from OHLC that forcing it into `CandleStore`'s table would conflate two meanings in one schema — the same reasoning that made `CaptureEvent` a sibling record rather than a reused `Observation` (17F.0.1 §2.3). A new store class, built to the identical pattern, is the right level of reuse. |
| `epistemics/lineage.py` | `calc_version_for()` (content-hashed, never hand-maintained), `Lineage.descends_from()` (accumulates parent source ids, never summarised away), `look_ahead_violation()`. | **Reuse directly**, same as 17F.1. |
| `epistemics/uncertainty.py` | **The load-bearing find of this audit.** A canonical `Uncertainty` carrier (`state`, `confidence`, `limiting_factor`, `completeness`, `provenance`) with a `GAP` state, a `DEGRADED` state, and a documented composition rule: *"a derived belief may never be stronger than its weakest CRITICAL input, and whenever it is weakened, it must name what weakened it."* This is exactly the "gap events must affect confidence/quality metadata" requirement (§6 of the brief) — already built, already designed for precisely this purpose, and unused by anything in this engagement so far. | **Reuse directly, do not invent a new quality field.** Every Futures Statistics record's quality is an `Uncertainty` value, composed from its inputs' presence/absence — never a bespoke `quality_score` float. |
| `market_reality/replay.py` (`replay`, `replay_capture_events`) | As built in 17F.1. **See §1.3 — a real gap found here, not a clean reuse.** | Reuse with a caveat; extension recommended, not assumed sufficient. |

**No duplicate calculator is proposed anywhere in this design.** Every
numeric transformation below either delegates to an existing function or
is genuinely new arithmetic with no prior implementation to duplicate
(OI delta, basis, book-state classification).

### 1.3 A genuine pre-existing gap found during this audit: the bitemporal contract is only half-implemented

Phase 17F.0.1 designed a **two-bound** bitemporal contract:
`as_of_event_time` and `as_of_knowledge_time`, independently. The actual
`replay()`/`materialize_candles()` shipped in 17F.1 implements **one**
bound: `event.timestamp` (itself `event_timestamp or capture_timestamp`,
collapsed at write time) compared against a single `as_of`.

For Tick→Candle this was a reasonable approximation — ticks/quotes are
rarely late enough for the collapse to matter. **Futures Statistics is
more exposed to it**: `MARKET_DEPTH` is polled on a slower cadence
(17F.0.4 §11 blocking item 2, still undecided), so the gap between an
observation's `event_time` and when it was actually *known*
(`capture_timestamp`) is plausibly larger — and OI/basis are exactly the
kind of fact where "was this known yet" matters (a delayed poll response
landing after 10:30 must not answer a "what did Bujji know at 10:30"
query just because its `event_time` happens to read ≤ 10:30).

**This audit does not silently work around it.** Recommendation (Part 8):
extend `replay()`/`replay_stream()` with optional, independent
`as_of_event_time`/`as_of_knowledge_time` parameters — backward
compatible (a single `as_of` continues to mean "both bounds equal,"
unchanged for every existing caller) — and have the Futures Statistics
Materializer be the first caller to use the stricter two-bound form. This
is flagged as a design decision requiring approval, not decided
unilaterally here.

---

## Part 2 — What "Futures Statistics" Means (Scope Lock)

### Allowed (per the brief, confirmed consistent with every prior phase's forbidden-list discipline)

Deterministic transformations of Layer 0 facts only — arithmetic, never
judgment:

- OI history (the raw series itself — a materialization, not a new fact)
- OI change (computed from two stored OI observations; never stored as if it were raw)
- Price change (delegates to the existing Candle, does not recompute)
- Volume statistics (from Candle + Depth observation counts)
- OI–price joint observation (the two deltas, reported side by side — **never** narrated as "buildup"/"unwinding")
- Futures premium/discount vs. spot ("basis") — **only** when both a futures and a spot price exist for the same window
- Basis history (the materialized series)
- Volatility of futures price — via the **existing** `realised_volatility()`, not new math
- Liquidity **measurements** from depth snapshots (top-of-book size, order count, book-state) — raw numbers, never a "thin/thick" label

### Forbidden (restated, binding, per 17F.0.4 §8 and 17G §2.0/§2.6, unchanged by this phase)

Trader intent, bullish/bearish labels, "smart money" narrative,
support/resistance, signals, predictions, "accumulation/distribution"
conclusions, any dealer-positioning inference. **None of these are
proposed anywhere below.** Where a field's *name* could tempt a future
reader toward interpretation (e.g. calling OI-up-price-up "long
buildup"), this design deliberately uses the flat, joint-fact name
instead (`oi_change`, `price_change`, reported together — never fused
into one causal-sounding field).

---

## Part 3 — Data Contract

### 3.1 Record: `FuturesStatistics`

One record per `(instrument, interval, window_start, calc_version)` —
identical key shape to `Candle`, deliberately, so the two series compose
naturally (same windows, same as_of/calc_version query discipline).

**Identity & lineage** (per brief §4, every item accounted for):

| Field | Purpose |
|---|---|
| `instrument` | The futures contract symbol |
| `interval`, `window_start`, `window_end` | Same windowing as the paired Candle |
| `source_observation_ids` | Layer 0 `MARKET_DEPTH`/`QUOTE` observation ids **directly** folded (OI, depth, liquidity fields) |
| `referenced_candle_keys` | **New, explicit pointers** — `(instrument, interval, window_start, calc_version)` tuples identifying the futures price Candle and (when basis is computed) the spot price Candle this record read. A pointer, never a copy of price data — see §3.2. |
| `materializer_id` | `"futures_stats"` |
| `calc_version` | Content hash (via `calc_version_for`) of this materializer's transformation definitions — **independent** of the Tick→Candle materializer's own `calc_version`, since a change to OI/basis logic must not silently invalidate price candles and vice versa |
| `transformation_history` | **New field, recommended** — see §3.3 |
| `first_event_time` / `last_event_time` | Real observed span of the `MARKET_DEPTH`/`QUOTE` facts folded in |
| `knowledge_boundary` | The `as_of` this record was computed under |
| `capture_event_overlap` | `CaptureEvent` ids overlapping this window — same mechanism as Candle |
| `quality` | An `epistemics.uncertainty.Uncertainty` value (serialized) — see §3.4 |
| `schema_version` | This record type's own version |

### 3.2 Why price is referenced, not duplicated

The Tick→Candle materializer already produces a `kind=FUTURES` Candle for
the same `(instrument, interval, window_start)`. Storing `price_open`/
`price_close` again on `FuturesStatistics` would be **exactly** the
duplicate-persistence problem the brief warns against. Instead:

- `price_change` is **computed at read time** (or once, at materialization
  time, and stored as a derived scalar — see below) from the referenced
  Candle's `open`/`close`, never re-observed from Layer 0.
- `referenced_candle_keys` makes the link explicit and auditable — a
  consumer (or a test) can always resolve "which Candle did this
  statistic's price_change come from" without guessing.

**Design choice requiring approval (Part 8, Q1):** should `price_change`
be stored as a materialized scalar on `FuturesStatistics` (cheap to
query, but a second place price-derived data lives), or computed
on-demand by any consumer holding both records (no duplication at all,
at the cost of every consumer doing the join)? This document's
recommendation: **store it** — it's a single scalar, not a duplicated
series, and the `referenced_candle_keys` pointer keeps it auditable
against its source. But this is a real trade-off, not an obvious answer.

### 3.3 `transformation_history` — a gap in 17F.1 this phase should not repeat

Auditing `Candle` (17F.1) against this brief's own requirement: `Candle`
carries `materializer_id` + `calc_version` (functionally "what produced
this, under what rule") but **no explicit `transformation_history`
tuple** — unlike `ObservationProvenance` at Layer 0, which has carried
one since 17E. For a single-hop derivation (Layer 0 → Candle) this is a
minor omission. For `FuturesStatistics`, which is a **two-hop**
derivation in the basis case (Layer 0 → Candle → FuturesStatistics), it
matters more: "which raw observations produced this?" currently requires
manually chasing `referenced_candle_keys` rather than reading one
self-contained field.

**Recommendation:** add `transformation_history: Tuple[str, ...]` to
`FuturesStatistics` now, e.g.
`("RAW_CAPTURE", "CANDLE:tick_to_candle:{calc_version}", "FUTURES_STATS:futures_stats:{calc_version}")`,
and **separately, as a follow-up, retrofit the same field onto `Candle`**
for consistency (additive, zero migration cost — the candle table has
never held a production row). This retrofit is **not** included in this
phase's scope; naming it here so it isn't lost.

### 3.4 Quality — composed via `Uncertainty`, not invented

Every `FuturesStatistics` record's `quality` field is built via
`epistemics.uncertainty` composition, not a bespoke score:

| Condition | Resulting state/confidence |
|---|---|
| No `MARKET_DEPTH` observation in the window | `oi_open`/`oi_close`/`oi_change` are `None`; `quality.state = GAP` if a `CaptureEvent` explains it, else the record simply has lower `completeness` |
| `MARKET_DEPTH` present but `oi` key absent from the payload | Same as above — **absence, not zero** (per §5) |
| `capture_event_overlap` non-empty | `quality.state = GAP`, `confidence` demoted, `limiting_factor` names the capture event's reason |
| Spot Candle missing for the window (basis inputs incomplete) | `basis`/`basis_percent` are `None`; this alone does not gate the rest of the record — OI/volume statistics can still be `KNOWN` even when basis is `NOT_AVAILABLE` |
| Fewer than `realised_volatility()`'s required trailing bars | `price_volatility = None` (inherited "insufficient history is not zero" from `indicators.py`, for free) |

This directly satisfies the brief's rule: *"gap events must affect
confidence/quality metadata... no silent interpolation."* Nothing here
ever substitutes a number for an absence.

---

## Part 4 — Schema Proposal (fields, not DDL — no code yet)

```
FuturesStatistics
├── identity: instrument, interval, window_start, window_end
├── lineage: source_observation_ids, referenced_candle_keys,
│            materializer_id, calc_version, transformation_history,
│            first_event_time, last_event_time, knowledge_boundary,
│            capture_event_overlap, schema_version
├── oi_open: Optional[float]              # first observed OI in window, None if no MARKET_DEPTH
├── oi_close: Optional[float]             # last observed OI in window, None if no MARKET_DEPTH
├── oi_change: Optional[float]            # oi_close - oi_open, ONLY if both present
├── oi_observation_count: int             # depth observations carrying a non-null oi
├── depth_observation_count: int          # ALL depth observations in window (oi or not)
├── volume: Optional[float]               # from the referenced futures Candle (not re-derived)
├── price_change: Optional[float]         # referenced Candle close - open (see 3.2 open question)
├── book_state: Enum[OBSERVED_NONEMPTY, OBSERVED_EMPTY, NOT_OBSERVED]
├── top_bid_size_last: Optional[float]    # only when book_state == OBSERVED_NONEMPTY
├── top_ask_size_last: Optional[float]    # only when book_state == OBSERVED_NONEMPTY
├── basis: Optional[float]                # futures_close - spot_close, ONLY if both exist
├── basis_percent: Optional[float]        # basis / spot_close * 100, ONLY if basis exists and spot_close != 0
├── price_volatility: Optional[float]     # realised_volatility() over a trailing window, reused unmodified
└── quality: Uncertainty                  # composed, never invented (§3.4)
```

**Deliberately absent from this schema:** anything resembling `oi_trend`,
`positioning_bias`, `liquidity_score`, or a fused "OI+price interpretation"
field. The schema stops at the two raw deltas, reported side by side.

---

## Part 5 — Transformation Definitions

All of the following are **pure functions of already-stored facts**,
computed once per `(instrument, interval, window_start, calc_version)`,
never recomputed differently by different callers (that's what
`calc_version` exists to prevent).

1. **`oi_open`/`oi_close`**: first/last `oi` value among `MARKET_DEPTH`
   observations for this instrument whose `event_time` falls in
   `[window_start, window_end)`, ordered by `event_time`. `None` if zero
   such observations carry a non-null `oi`.
2. **`oi_change`**: `oi_close - oi_open`, computed **only** if both are
   non-`None`. This is the one place this design performs arithmetic on
   two raw values, exactly the pattern already approved for `oi_change`
   in 17D/17F.0 ("not even a subtraction unless both real values exist").
3. **`book_state`**: from the **last** `MARKET_DEPTH` observation in the
   window — `OBSERVED_NONEMPTY` if `bids`/`asks` are both non-empty lists,
   `OBSERVED_EMPTY` if both keys are present but empty (a real, structural
   fact per 17F.0.4 §3 Rule 2), `NOT_OBSERVED` if no `MARKET_DEPTH`
   observation exists in the window at all. Three states, never two —
   collapsing `OBSERVED_EMPTY` and `NOT_OBSERVED` into one would be
   exactly the "empty book vs. no observation" conflation the brief
   explicitly warns against.
4. **`price_change`**: from the referenced futures `Candle` for the same
   key — `close - open`. Never recomputed from raw ticks a second time.
5. **`basis`/`basis_percent`**: requires the futures Candle's `close`
   **and** a spot Candle for `NIFTY50-INDEX` at the identical
   `(interval, window_start, calc_version)`. If the spot Candle for that
   exact key doesn't exist (spot had a gap, or was never materialized at
   that `calc_version`), `basis` is `None` — never approximated from a
   nearby window.

   **SEMANTIC CONTRACT (added post-implementation, explicit rather than
   left implicit in code):**

   > Futures basis is defined as the difference between the futures
   > Candle's close and the corresponding spot index Candle's close for
   > the SAME interval window. Both Candles share the identical
   > `interval`, `window_start`, `window_end`, `as_of` boundary (both
   > reads are bounded by the same `materialize_futures_stats(as_of=...)`
   > call), and `calc_version` lineage (both were produced by the SAME
   > Tick->Candle materializer run, per `candle_calc_version`). Basis
   > does NOT represent a live futures-premium/discount snapshot pairing
   > an instantaneous futures quote against an instantaneous spot quote
   > -- it is a comparison of two already-closed, already-windowed bars.

   ```
   basis = futures_candle.close - spot_candle.close
   basis_percent = basis / spot_candle.close * 100   # only if spot_candle.close != 0
   ```

   This distinction matters because a naive reading of "basis" could
   assume it is computed from two simultaneous live ticks -- it is not.
   `referenced_candle_keys` on the `FuturesStatistics` record carries
   BOTH Candle keys explicitly, so either side's real
   `first_event_time`/`last_event_time`/`window_end` remains
   independently inspectable rather than asserted by this paragraph
   alone.

   **`basis` is a measurement, not an interpretation.** Explicitly, per
   the scope lock (Part 2): a negative basis is NOT a bearish signal; a
   positive basis is NOT a bullish signal. `FuturesStatistics` reports
   the observed relationship between two closed prices and nothing about
   what caused it or what it predicts. A future consumer asking "can we
   use basis to detect institutional positioning?" must answer that from
   a later Understanding-layer interpretation built ON TOP of this
   record -- never by reading intent into this field itself.
6. **`price_volatility`**: `market_timeseries.indicators.realised_volatility(candles, period)`
   called against the trailing futures Candles ending at this window,
   unmodified. `period` is a materializer parameter, part of
   `calc_version`'s hashed definition (changing it produces a
   distinguishable version, per the already-established PK discipline).
7. **`top_bid_size_last`/`top_ask_size_last`**: from the last
   `MARKET_DEPTH` observation's first bid/ask level `volume`, only when
   `book_state == OBSERVED_NONEMPTY`.

---

## Part 6 — Lineage Design

Follows 17F.1's materializer contract exactly, with the one addition
(`transformation_history`) and the one extension under review (dual-bound
`as_of`, §1.3):

- **Deterministic**: same Layer 0 + same Candle inputs → byte-identical
  output, always.
- **No wall-clock, no randomness** inside the materializer — `as_of`
  (and, pending approval, `as_of_knowledge_time`) supplied by the caller.
- **Replayable**: takes no persistent state; calling it twice against the
  same inputs is the correctness test, identical in spirit to 17F.1's own
  `test_rebuild_from_layer0_alone_is_byte_identical`.
- **No look-ahead**: enforced at the `replay()` boundary this
  materializer calls into — inherits whatever guarantee that boundary
  provides, which is why §1.3's gap matters here specifically.

---

## Part 7 — Storage Design

**Reuse the pattern, add one small store.** Per the audit (§1.2),
`CandleStore`'s SQLite/WAL/PK-with-calc_version/mandatory-as_of-and-
calc_version-on-reads pattern is correct and proven; `FuturesStatistics`
gets its **own table**, via a new, structurally identical store class
(tentatively `FuturesStatsStore`, in a new `bujji/market_timeseries/futures_stats_store.py`),
rather than being force-fit into the `candles` table.

**Open question (Part 8, Q3):** same `.db` file as `CandleStore`, or a
separate file? Both are Layer 1 derived stores for the same instrument
universe, computed on the same cadence — a shared file is operationally
simpler (one file to back up, one WAL). A separate file keeps the two
schemas fully independent (no risk of one's migration touching the
other's table). No production data exists yet in either case, so this
costs nothing to decide either way — flagged for your preference, not a
technical blocker.

**No new persistence *mechanism*** is proposed — SQLite/WAL, the same
`_run()` retry-on-locked helper, the same idempotent-write/
`ConflictingCandleError`-equivalent discipline, are all reused verbatim
in pattern.

---

## Part 8 — Unresolved Questions — ALL DECIDED, see PHASE_17F1_2_Q1_Q2_Q3_Q5_DECISIONS.md

1. **DECIDED: store it as a materialized scalar** on `FuturesStatistics`, per §3.2's recommendation. See the decisions doc for full reasoning.
2. **DECIDED: yes to both**, as two separate changes — `transformation_history` ships on `FuturesStatistics` in this phase; the `Candle` retrofit is authorized as an independent, additive follow-up (not bundled into this phase's delivery).
3. **DECIDED: separate file** — `bujji/market_timeseries/futures_stats.db`, via a new `FuturesStatsStore`. Matches this codebase's one-store-per-file convention and keeps blast radius isolated per store.
4. **Extend `replay()`/`replay_stream()` with independent
   `as_of_event_time`/`as_of_knowledge_time`, or accept the single-bound
   approximation for this phase too?** (§1.3) This is the one question
   with real correctness stakes — recommendation: extend it, backward
   compatible, and have this materializer be the first stricter caller.
5. **DECIDED: 60-second polling cadence** for futures `MARKET_DEPTH`, as a starting value revisited once live rate-limit certification data exists — not a schema/materializer dependency, since `oi_observation_count`/`depth_observation_count` make the actual sampling density self-describing regardless of cadence.

---

## Part 9 — Test Plan

Mirrors 17F.1's materializer test suite structure, extended for the
facts specific to this materializer:

**Reality consumption**
- Futures `QUOTE`/`MARKET_TICK` price observations feed the referenced
  Candle correctly (delegation, not duplication — assert
  `referenced_candle_keys` resolves to a real Candle whose `close`
  matches `price_change`'s basis).
- `oi_open`/`oi_close` only populate from `MARKET_DEPTH` observations —
  a `QUOTE` observation carrying a stray `oi`-shaped payload key (if any)
  must never be read for OI (structural test: feed a `QUOTE` with an
  `oi` field the code must not look at).
- Missing `MARKET_DEPTH` in a window → `oi_open`/`oi_close`/`oi_change`
  all `None`, not `0.0`.

**Determinism / replay**
- Same Layer 0 + Candle state → identical `FuturesStatistics`, twice.
- Two different `calc_version`s (e.g. different `price_volatility`
  `period`) → two distinct, coexisting records, never a
  `ConflictingCandleError`-equivalent collision.
- Delete all materialized `FuturesStatistics`, re-materialize from Layer
  0 + Candles alone, compare byte-for-byte.

**No look-ahead**
- A `MARKET_DEPTH` observation with `event_time` after the query's
  `as_of` must not appear in `oi_close`.
- (Pending Q4) If the dual-bound extension is approved: an observation
  with `event_time <= as_of` but `knowledge_time > as_of` must also be
  excluded — the specific gap this audit found in §1.3.

**Gap handling**
- A `CaptureEvent` overlapping the window sets `capture_event_overlap`
  **and** demotes `quality` via the `Uncertainty` composition rule, with
  a `limiting_factor` naming the capture reason.
- No interpolation anywhere: assert that no code path ever fills a
  `None` OI/basis value with a neighboring window's value.

**Absence semantics**
- `MARKET_DEPTH` with `bids: []`/`asks: []` (structurally empty, present)
  → `book_state = OBSERVED_EMPTY`, distinct from a window with zero
  `MARKET_DEPTH` observations → `book_state = NOT_OBSERVED`. Both must
  be independently assertable, not collapsed.
- Missing spot Candle → `basis`/`basis_percent` are `None`; OI/volume
  fields on the same record remain independently populated (basis
  absence does not cascade into unrelated fields going missing too).

**Schema / forbidden fields**
- AST or field-enumeration test asserting `FuturesStatistics` has no
  field name matching the forbidden list (`regime`, `signal`, `score`,
  `sentiment`, `bias`, `trend`, `liquidity_score`, etc.) — same mechanism
  already used for Layer 0's `FORBIDDEN_PAYLOAD_FIELDS` enforcement.

**Reuse verification**
- `price_volatility` output matches calling
  `indicators.realised_volatility()` directly against the same candles —
  proving delegation, not reimplementation.

---

## Part 10 — Gate Status

| Gate | Status |
|---|---|
| Futures observation structure audit | **Complete** — Part 1.1 |
| Reusable infrastructure audit | **Complete** — Part 1.2, including one new load-bearing reuse (`epistemics.uncertainty`) |
| Bitemporal gap found | **Complete, flagged** — Part 1.3, not silently worked around |
| Scope lock (allowed/forbidden) | **Complete** — Part 2 |
| Data contract | **Complete** — Part 3 |
| Schema proposal | **Complete** — Part 4 |
| Transformation definitions | **Complete** — Part 5 |
| Lineage design | **Complete** — Part 6 |
| Storage design | **Complete** — Part 7 |
| Test plan | **Complete** — Part 9 |
| Unresolved questions | **All 5 decided** — Part 8, see PHASE_17F1_2_Q1_Q2_Q3_Q5_DECISIONS.md |
| Implementation | **COMPLETE.** `futures_stats_models.py`, `futures_stats_store.py` (separate `futures_stats.db`, per Q3), `futures_stats_materializer.py`; `Candle.transformation_history` retrofit landed (Q2). 20 new tests (`test_futures_stats_materializer.py`), safety-scan coverage extended, full regression: 5,332 passed, 0 failed. |
| Review | **Design and implementation both complete.** |
| Any implementation | **Not started. Not authorized.** |
