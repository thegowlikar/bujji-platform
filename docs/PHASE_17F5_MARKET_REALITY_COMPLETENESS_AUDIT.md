# Phase 17F.5 — Market Reality Completeness Audit

**Status: AUDIT ONLY. No code. No schemas. No new fields. No strategies.**

**Question this document answers:** does Bujji have enough sensory input to
reconstruct a professional discretionary trader's view of NIFTY — and if
not, what exactly is missing, what is impossible with FYERS, and in what
order must the gaps be closed?

Every claim below was verified against the actual code on the VPS
(`/opt/bujji/app`) during this audit — imports traced, greps run, artifacts
read. Where a docstring claimed a capability, the claim was checked against
the code rather than accepted. Findings that contradict a prior document's
optimism are stated plainly.

---

## Part 0 — The headline finding

**Layer 0 and Layer 1 are a closed loop that nothing reads and nothing
feeds.**

Verified:

- **Non-test writers into Layer 0: one** — `scripts/run_futures_depth_poller.py`,
  which is gated OFF (`FIELD_MAPPING_VERIFIED = False`) and writes nothing.
  Every other `RawObservationStore(...)` / `build_raw_observation(...)`
  construction in the repo is inside `tests/`.
- **Non-test importers of `bujji.market_timeseries`: zero.** Nothing
  downstream consumes candles or futures statistics.
- **Non-test importers of `bujji.market_reality`: three**, all inside
  `market_timeseries` itself, plus the gated poller.
- **No Layer 0 store exists on disk.** No `layer0_data/` directory, no
  `raw_observations.jsonl`, no `candles.db`, no `futures_stats.db`.
- **`append_capture_event` has no live caller.** Capture events —
  the mechanism that makes blind spots visible — are exercised only by
  tests. No collector emits one.

**Consequence for this audit:** the honest answer to "can Bujji reconstruct
a trading session?" is **not yet, because Bujji has never observed one.**
Everything below distinguishes *the pipeline is capable of X once fed*
from *X has actually been demonstrated on real data* — a distinction the
green 5,359-test regression does **not** make, since those tests exercise
logic against synthetic fixtures.

---

## Part 1 — Full-day market reconstruction

### 1.1 What the pipeline can do, structurally

| Capability | Code path | Verified state |
|---|---|---|
| Immutable append-only raw observation log | `market_reality/store.py` → `state_persistence.EventStore` | Implemented, tested, **never written to in production** |
| Certification gate, fail-closed | `market_reality/certification.py` | Implemented; 3 artifacts on disk (see 1.3) |
| Validator (structural + forbidden-field + timestamp sanity) | `market_reality/validator.py` | Implemented, tested |
| Ordered replay, exact append order | `market_reality/replay.py` | Implemented, tested |
| **Dual bitemporal bounds** (`as_of_event_time` / `as_of_knowledge_time`) | `market_reality/replay.py` | Implemented this engagement; 8 dedicated tests |
| Capture events (disconnect/overflow/gap) in-stream | `market_reality/capture_events.py` | Implemented; **no live emitter** |
| Completeness measurement (expected vs received, missing intervals) | `market_reality/completeness.py::measure()` | Implemented; requires a caller supplying `interval_seconds` |
| Tick/Quote → Candle materialization with provenance | `market_timeseries/materializer.py` | Implemented, 18 tests |
| Futures statistics (OI/basis/book state/volatility) | `market_timeseries/futures_stats_materializer.py` | Implemented, 20 tests, **zero real rows** |

### 1.2 What is genuinely proven

The **bitemporal replay guarantee is real and tested**: an observation whose
`event_time` precedes a cutoff but whose `knowledge_time` follows it is
excluded. This is the single strongest property in the foundation, and it
is what makes "what did Bujji know at 10:30?" a well-posed question rather
than a hopeful one. It is proven against fixtures; it has never been
exercised against a real session's data.

### 1.3 Certification state — and a granularity gap found during this audit

Artifacts present in `data_certification/`:

| Artifact | Instrument key | Result | Dated |
|---|---|---|---|
| `fyers_nifty_spot_certification.json` | `NIFTY_SPOT` | CERTIFIED_AVAILABLE | 2026-08-12 |
| `fyers_nifty_future_certification.json` | `NIFTY_FUTURES` | CERTIFIED_AVAILABLE | 2026-08-12 |
| `fyers_option_chain_certification.json` | `NIFTY_OPTION_CE` | CERTIFIED_AVAILABLE | 2026-08-12 |
| *(none)* | `INDIA_VIX` | **CERTIFICATION_MISSING** (fail-closed) | — |

**NEW FINDING — certification granularity is coarser than the capability it
implies.** `CertificationGate.status_for(access_method, instrument_type)`
takes **no observation-kind parameter**. Certification is per
`(access_method, instrument_type)`, not per kind.

Concretely: the `NIFTY_FUTURES` artifact attests `quote_status: OK`,
`historical_status: OK`, and `oi_available: true` with the explicit
limitation *"oi obtained via depth(); not available via quotes/ltp or
optionchain."* It says **nothing whatsoever about the bid/ask ladder.**
Yet a `MARKET_DEPTH` observation carrying an unverified `bids`/`asks`
payload for a futures instrument would **pass the gate**, because the gate
only asks "is FUTURE certified for this access method?" — and it is.

This is not currently causing harm (no depth is being written, by the
`FIELD_MAPPING_VERIFIED` gate in the poller). But the poller's gate is a
*script-level constant*, whereas the certification gate is the *structural*
protection. The structural protection does not currently distinguish
"futures quotes are certified" from "the futures order-book ladder is
certified." **Recommendation:** treat this as a required design item before
any depth write is enabled — either per-kind certification keys, or a
per-field capability assertion inside the artifact. Named here so it is not
discovered later, after real rows exist.

### 1.4 Can a full trading day be reconstructed today?

**No.** Blocking, in order:

1. No collector writes to Layer 0 at all (tick, quote, depth, or chain).
2. Depth field mapping unverified (see Part 7).
3. No capture-event emitter — so a gap in a future capture run would be
   *invisible* rather than *recorded*, silently violating the one property
   the whole design exists to guarantee. **This is the most
   under-appreciated gap in the current foundation.**
4. VIX not certified — `INSTRUMENT_INDEX` writes fail closed.
5. Websocket not certified (`scripts/certify_websocket_access.py` prepared,
   never executed) — so tick-resolution capture has no certified path.

---

## Part 2 — Professional trader question capability matrix

Legend: **Yes** = answerable from stored reality today (pipeline + data).
**Pipeline-ready** = code path exists, needs data only. **Build** = needs a
component that does not exist. **Impossible** = FYERS does not provide the
input at all.

### Price structure

| Trader Question | Answerable Today? | Required Data / Component | Confidence |
|---|---|---|---|
| Where is NIFTY now? | Pipeline-ready | Spot tick/quote capture → Candle | High once fed |
| Where is NIFTY vs **previous day high/low**? | **Build** | Daily-resolution candles + a PDH/PDL primitive. `_INTERVAL_SECONDS` supports **only ONE_MINUTE and FIVE_MINUTE** — no daily bucket exists. Grep: `prev_day_high`, `previous_day` → **0 hits repo-wide** | — |
| Weekly / monthly levels? | **Build** | Same as above, plus weekly/monthly aggregation. Backfill decision (17G) allows 3y daily, but no daily interval exists in the aggregator | — |
| Is price trending or ranging? | **Build** | Regime primitive (Part 6). Legacy `msi_market_structure` classifies this but is **not fed by Layer 0/1** (Part 3) | — |
| Is a breakout occurring or failing? | **Build** | Structure primitive: swing points + break-of-structure. Grep `structure_break` → **0 hits**. Legacy `msi_market_structure/engine.py` has a self-declared *"minimal, disclosed, price-only swing-point proxy"* over event prices, not candles | — |
| Is price accepting or rejecting a level? | **Build** | Requires level memory + time-at-price. Neither exists | — |
| Where is price vs VWAP? | **Build** (legacy exists, unusable) | `vwap` has 129 hits — **all in the legacy pre-Layer-0 stack** (`signal/`, `core/orchestrator.py`, `broker/`). `market_reality/taxonomy.py:164` lists `"vwap"` as a **banned** Layer 0 payload field (correctly — it is derived). No Layer 1 VWAP materializer exists | — |
| Opening range? | **Build** (legacy exists, unusable) | Legacy ORB in `signal/engine.py`, `core/orchestrator.py` — not Layer-0-fed | — |

### Futures

| Trader Question | Answerable Today? | Required Data / Component | Confidence |
|---|---|---|---|
| Is OI increasing/decreasing? | **Pipeline-ready, data-blocked** | `FuturesStatistics.oi_open/oi_close/oi_change` implemented. Needs certified depth capture. **Zero real rows today** | High once fed |
| Is basis expanding/compressing? | **Pipeline-ready, data-blocked** | `basis`/`basis_percent` implemented with an explicit semantic contract (candle-close to candle-close, same window/`as_of`/`calc_version`). Needs both futures AND spot candles | High once fed |
| Is futures liquidity improving/deteriorating? | **Partially — measurement only** | `book_state` (3-state), `top_bid_size_last`, `top_ask_size_last` exist. Only top-of-book, only last-observation-in-window. **Depth ladder shape unverified** | Medium at best |
| Is positioning changing? | **Measurement yes, interpretation no — deliberately** | OI change + price change are stored **side by side, never fused**. "Long buildup / short covering" is an interpretation requiring assumptions and is explicitly out of scope | N/A |

### Options

| Trader Question | Answerable Today? | Required Data / Component | Confidence |
|---|---|---|---|
| Where is option interest concentrated? | **Build** | `KIND_OPTION_CHAIN` exists in taxonomy and `capture.py` maps it — but grep confirms **no materializer, no store, no consumer**. Chain is certified at the broker level; nothing captures or derives it | — |
| Are strikes being defended? | **Build** | Needs per-strike OI + ΔOI time series. Not backfillable (17G: chain history is capture-forward-only, permanently) | — |
| Is premium expansion happening? | **Build** | Needs per-strike LTP series | — |
| Is volatility expanding? | **Split — see Part 7** | *Realized* vol: pipeline-ready (`realised_volatility()` reused). *Implied* vol: **must be certified separately**; `iv` is a **banned** Layer 0 field. VIX: **not certified** | Mixed |

### Market internals

| Trader Question | Answerable Today? | Required Data / Component | Confidence |
|---|---|---|---|
| Is participation broad? | **Build — and needs new instruments entirely** | Grep `breadth` → **0 hits**. Advance/decline requires per-constituent data across 50 symbols, which is a **capture-scope expansion**, not a materializer | — |
| Are sectors confirming? | **Build** | Grep `sector` → 1 unrelated hit. Requires sector index capture (not in scope of any current certification) | — |
| Healthy rally or narrow move? | **Build** | Strictly downstream of breadth | — |
| Is BankNifty diverging? | **Build** | No BankNifty instrument is certified or captured | — |

### Risk / context

| Trader Question | Answerable Today? | Required Data / Component | Confidence |
|---|---|---|---|
| Is volatility elevated? | **Blocked at certification** | VIX cert script prepared, **never run**; `INSTRUMENT_INDEX` fails closed | — |
| Is liquidity abnormal? | **Build** | Needs a liquidity baseline over time; only point measurements exist | — |
| Is today different from normal? | **Build** | Requires regime + historical distribution. Neither exists | — |
| Was there a data gap, and where? | **Pipeline-ready, emitter-blocked** | `completeness.measure()` + `CaptureEvent` implemented; **no live emitter** | High once fed |
| What did Bujji know at 10:30? | **Yes — structurally proven** | Dual-bound `replay()`; `knowledge_boundary` stamped on every derived record | **High** |

**Summary count:** of 24 questions, **1** is answerable today (the
epistemic one), **6** are pipeline-ready and purely data-blocked, **15**
require components that do not exist, and **2** are impossible or
near-impossible (Part 7).

---

## Part 3 — Market State Snapshot design review

### 3.1 Does a unified snapshot already exist?

Six candidate packages were audited by tracing actual imports.
**Result: none of them import `bujji.market_reality` or
`bujji.market_timeseries`. Not one.**

| Package | Consumes | Bitemporal? | Layer | Wired live? |
|---|---|---|---|---|
| `market_state/` | dicts from `intelligence.runner` + `MarketStateAssessment` | No — single `timestamp` | Understanding | Yes (shadow runner, replay engine) |
| `msi_market_structure/` | `Episode` + `MarketEvent` objects | `provenance` is a hardcoded string | Understanding | Yes (widely) |
| `market_regime_memory/` | caller-supplied state | No | Understanding | Yes |
| `market_narrative/` | raw dicts | No | Understanding | Barely |
| `mil_next/` | caller-supplied `MarketDataInputs` | **Yes — strongest in repo** (event-time vs receipt-time provenance, content hashing, decision cutoff) | Understanding | **Dead — zero non-test importers** |
| `market_perception/` | broker adapters, `core.models.Candle` | No | Perception | Yes |

**Two findings worth stating plainly:**

1. **`mil_next` is a well-designed, bitemporally-aware snapshot builder that
   nothing uses.** Its `MarketIntelligenceSnapshot` already carries
   `decision_cutoff_event_time`, `event_time_provenance`,
   `receipt_time_provenance`, `content_hash`, `data_quality`. It predates
   Layer 0 and is fed by caller-supplied inputs rather than by replay.
   Before designing a new snapshot type, **this should be evaluated as a
   candidate to adapt** — the project's own reuse-over-rebuild rule applies.
   It is not a drop-in (it is interpretation-heavy: regime, thesis, posture,
   contradiction score), but its *temporal* design is closer to correct than
   anything else in the repo.

2. **The existing `market_state.MarketState` is not a Reality-layer
   object.** Its fields (`regime`, `market_direction`, `overall_confidence`)
   are classifications. Reusing it as "the" snapshot would collapse the
   Reality/Understanding boundary this phase exists to protect.

### 3.2 Where does a Market State Snapshot belong?

**Understanding Layer — not Memory, not Intelligence.**

Reasoning:

- It is **not Memory**: Memory's job is to store what was observed,
  immutably and traceably. A snapshot *composes* many observations into one
  view — that composition is a derivation with its own `calc_version`, not
  a stored fact.
- It is **not Intelligence**: it must contain no decision, no signal, no
  recommendation. "NIFTY is 40 points above PDH with futures OI rising and
  VIX falling" is a *state*; "therefore go long" is Intelligence.
- It **is Understanding**: it answers "what is the market doing right now,
  as best we can observe," which is exactly the Understanding layer's job.

**Design constraints it must inherit (non-negotiable, from Layers 0/1):**

- Every snapshot is a pure function of Layer 0 + Layer 1 at a given
  `as_of_event_time` / `as_of_knowledge_time` — replayable, byte-identical.
- Carries `calc_version`, `source_observation_ids` (or references to the
  materialized records that carry them), `knowledge_boundary`.
- Carries an `Uncertainty` (per `epistemics.uncertainty`) composed from its
  inputs — so `confidence = DEGRADED because VIX unavailable + depth stale +
  capture gap` is *derived*, never asserted.
- Contains **no** field whose name or meaning is a classification, unless
  and until a separate, explicitly-scoped Understanding phase authorizes it.

**Not implemented in this phase. Not authorized.**

---

## Part 4 — Multi-timeframe understanding readiness

### 4.1 Hard blocker found

`market_timeseries/aggregator.py::_INTERVAL_SECONDS` supports exactly:

```
ONE_MINUTE = 60
FIVE_MINUTE = 300
```

`window_bounds()` raises `ValueError` on any other interval. **There is no
15-minute, hourly, daily, weekly, or monthly bucket anywhere in Layer 1.**

`indicators.py::_INTERVAL_SECONDS` mirrors the same two, so
`series_is_contiguous()` returns `False` for any other interval — meaning
even if candles of another interval existed, the contiguity check would
silently mark them discontinuous.

### 4.2 Readiness by timeframe

| Timeframe | Aggregator support | Data source | State |
|---|---|---|---|
| 1-minute | **Yes** | Live capture only (17G: no historical backfill) | Pipeline-ready, data-blocked |
| 5-minute | **Yes** | Live + 6mo backfill permitted | Pipeline-ready, data-blocked |
| 15-minute | **No** | Backfill permitted (17G) but no bucket exists | **Build** |
| Hourly | **No** | — | **Build** |
| Daily | **No** | 3y backfill permitted (17G); broker `historical` currently called with `resolution=str(minutes)` only — daily resolution never exercised | **Build** |
| Weekly / Monthly | **No** | Derivable from daily once daily exists | **Build** |

### 4.3 Observed facts vs interpretations — the separation to preserve

| Observed fact (Reality/Memory) | Interpretation (Understanding) |
|---|---|
| OHLC of a window; `tick_count`; `first/last_event_time` | "trend" |
| A local high at time T with value V | "swing high" (requires a lookback rule = a definition = a `calc_version`) |
| Price crossed level L at time T | "break of structure" (requires "what is a structural level") |
| Range of the last N bars | "compression" / "expansion" (requires a threshold) |
| OI rose while price rose | "long buildup" |

**Everything in the right column requires a parameterized definition.** That
is precisely why `calc_version` exists: an interpretation with a
content-hashed definition is reproducible; one without is an opinion. Any
future structure work must carry `calc_version` for every threshold it uses.

---

## Part 5 — Supply/Demand readiness audit

**Explicitly not designing zones here.** Auditing prerequisites only.

Grep result: `demand_zone`, `supply_zone` → **0 hits repo-wide.** Nothing
retail or otherwise exists. Good starting position.

| Institutional-style question | Prerequisite | Available? |
|---|---|---|
| Can we identify accumulation areas? | Time-at-price + volume-at-price over a window | **No** — volume exists per candle, but no volume-at-price / profile primitive |
| Can we identify rejection areas? | Wick/rejection geometry + subsequent displacement | Partially — OHLC gives wicks; no displacement measure |
| Can we measure displacement? | Multi-bar range expansion relative to a baseline | **No** — requires a baseline definition (`calc_version`) that does not exist |
| Can we measure retest behaviour? | Level memory + revisit detection | **No** — no level store |
| Can we track zone freshness? | Zone lifecycle store (created/retested/broken) | **No** |
| Can we tell *who* was absorbing? | Aggressor side / trade prints | **Impossible** — see Part 7 |

**Verdict:** zones are **at least two layers away**. The honest prerequisite
chain is: daily/15m intervals → structure primitives (swings, displacement)
→ level memory → zone lifecycle. Attempting zones before level memory would
produce exactly the retail heuristic the operator ruled out.

**Critical caveat:** institutional zone logic conventionally leans on
absorption and aggressor-side inference. With FYERS snapshot depth and no
trade prints (Part 7), any zone Bujji builds will be a **price-and-OI
structure zone**, not a true order-flow zone. That limitation must be
carried in the zone's own `Uncertainty`, not hidden.

---

## Part 6 — Market regime readiness

Grep: no `MarketRegimeObservation` exists. `market_regime_memory/` operates
on caller-supplied regime state — it *remembers* regimes, it does not
*observe* them, and it is not Layer-0-fed.

### Raw observations required before regime classification can honestly exist

| Regime type | Minimum raw inputs | Missing today |
|---|---|---|
| Trending day | Multi-timeframe candles (15m+), directional range expansion | 15m interval; expansion measure |
| Range day | Session high/low, time-at-price distribution | Session-level aggregation; profile |
| Volatile / quiet day | Realized vol **vs its own history**; VIX level vs history | VIX certification; historical vol distribution |
| Expiry behaviour | Expiry calendar + option chain OI concentration | Both missing |
| Event-driven session | Event calendar | Missing entirely |

**Sequencing rule that must hold:** a regime *observation* is still a
derivation with a definition. "Today's realized volatility is in the 90th
percentile of the trailing 60 sessions" is an observation with a
`calc_version`. "Today is a volatile day" is a label. The first is
buildable once inputs exist; the second requires an explicit threshold
decision recorded as a parameter.

---

## Part 7 — Data limitations (permanent list)

### 7.1 Impossible with FYERS — no workaround, ever

| Limitation | Consequence |
|---|---|
| **No aggressor side** (buy-initiated vs sell-initiated) | Cannot answer "is this move buyer-initiated?" Order-flow analysis, delta, CVD, absorption-by-side are all **permanently out of reach** |
| **No individual trade prints** | No tape reading, no trade-size distribution, no large-print detection |
| **No true buyer/seller classification** | Every "who is doing this" question is unanswerable. Any future component implying it would be fabricating |
| **No historical option chain snapshots** | Per-strike OI history begins the moment polling begins — permanently. No backfill exists at any price (17G decision, confirmed) |
| **No historical futures OI** | Same: OI comes only from `depth()`, which has no historical form. OI history depth is bounded forever by collector uptime |
| **No dealer/market-maker positioning** | Dealer-gamma-style analysis is proxy-only at best, and must be labeled as proxy |

### 7.2 Possible, but not built

- Daily / weekly / monthly / 15-minute / hourly candle intervals
- VWAP as a Layer 1 derivation (legacy implementations exist but are not
  Layer-0-fed and must not be reused as-is)
- Previous-day / weekly / monthly levels
- Swing points, break-of-structure, compression/expansion, session high/low
- Option chain materializer + store (taxonomy exists; nothing else does)
- Market breadth / advance-decline / sector participation (also a
  **capture-scope expansion** — new instruments, new certifications)
- BankNifty and sector indices (no certification, no capture)
- Event calendar
- Volume-at-price / time-at-price profile
- **A capture-event emitter** — the mechanism exists, nothing emits

### 7.3 Possible after certification

| Item | Blocking artifact |
|---|---|
| India VIX capture | `scripts/certify_vix_access.py` — **prepared, never executed** |
| Tick-resolution capture | `scripts/certify_websocket_access.py` — **prepared, never executed** |
| Futures depth ladder → Layer 0 | `scripts/discover_depth_response_shape.py` — **prepared, never executed**; then a per-kind certification decision (Part 1.3) |
| Option depth | Same discovery run, option leg |
| Implied volatility | No certification exists; `iv` is a banned Layer 0 field, so IV can only ever enter as a certified, explicitly-derived Layer 1+ record |

---

## Part 8 — Required future collectors

| Collector | Status | Blocking |
|---|---|---|
| Futures `MARKET_DEPTH` poller (60s) | Written, gated OFF | Field-mapping discovery run |
| Spot tick/quote collector | **Does not exist** | Websocket certification (tick) or REST cadence decision (quote) |
| Futures tick/quote collector | **Does not exist** | Same |
| Option chain collector | **Does not exist** | Cadence decision; chain is certified at broker level |
| VIX collector | **Does not exist** | VIX certification |
| **Capture-event emitter** (inside every collector) | **Does not exist** | Nothing — this is pure build, and it is a **prerequisite for trusting any capture run**, not an add-on |
| Daily/historical backfill job | **Does not exist** | Daily interval support in the aggregator |
| Breadth / sector collector | **Does not exist** | Instrument scope decision + certification |

---

## Part 9 — Required future materializers

| Materializer | Depends on |
|---|---|
| Multi-interval candles (15m, hourly, daily, weekly) | Aggregator interval extension — **smallest, highest-leverage change in this list** |
| Session levels (PDH/PDL, session high/low, opening range) | Daily + intraday candles |
| VWAP (Layer 1, provenance-carrying) | Candles with volume |
| Option chain statistics (per-strike OI/ΔOI/LTP series) | Option chain collector |
| Volatility statistics (realized vs its own history; VIX series) | VIX capture + historical distribution |
| Market structure primitives (swings, displacement, BoS) | Multi-interval candles; each threshold as a `calc_version` parameter |
| Level memory | Structure primitives |
| Breadth statistics | Breadth collector |
| **Market State Snapshot (Understanding layer)** | Substantially all of the above |

---

## Part 10 — Recommended sequence toward Market Understanding

Ordered by *unblocking power per unit of risk*, not by interest.

**Gate A — make capture trustworthy (before any capture at all)**
1. **Build the capture-event emitter.** Nothing else on this list matters if
   gaps are invisible. Capturing data without recording blind spots produces
   a record that *looks* complete and is not — the single worst failure mode
   available to this project.
2. Resolve certification granularity (Part 1.3) — decide whether per-kind
   certification keys are required before depth writes.

**Gate B — establish broker reality (live runs, operator-driven)**
3. Run `discover_depth_response_shape.py` — futures **and** option legs.
   Answer the snapshot-vs-incremental question definitively.
4. Run `certify_vix_access.py`.
5. Run `certify_websocket_access.py`.

**Gate C — first real capture**
6. Enable depth writes (after 3 + 2).
7. Build spot + futures quote/tick collectors, with capture events wired in.
8. **Capture one complete real trading session**, then run a replay audit
   against it — the first genuine test of every property currently proven
   only against fixtures.

**Gate D — widen the sensory field**
9. Option chain collector (highest strategic value for an options-selling
   system; permanently capture-forward-only, so **every day not capturing is
   a day of history lost forever** — this argues for starting it early even
   at reduced polish).
10. VIX collector.
11. Multi-interval aggregator extension + daily backfill.

**Gate E — structure (only now)**
12. Session levels (PDH/PDL, opening range, session high/low).
13. VWAP as a Layer 1 derivation.
14. Structure primitives.
15. Level memory.

**Gate F — Understanding**
16. Market State Snapshot (evaluate adapting `mil_next` first).
17. Regime observations.
18. Zones — last, and only with their order-flow limitation explicit.

Breadth/sector capture can proceed in parallel with Gate D once the
instrument-scope question is decided; it is independent of the NIFTY chain.

---

## Part 11 — Verdict

**Does Bujji have enough sensory input to think like a professional trader?**

**No — and not close yet.** But the reason is encouraging rather than
alarming: what exists is *correct*, and what is missing is *known*.

- The **epistemic foundation is genuinely strong**: immutability,
  fail-closed certification, dual-bound bitemporal replay, content-hashed
  calc versions, absence-never-becomes-zero, forbidden-field enforcement.
  These are the properties that are expensive to retrofit, and they are
  in place before any data exists — which is the correct order.
- The **sensory field is nearly empty**: one gated collector, no live
  writes, no real rows, five of six certifications either missing or
  unexecuted.
- The **structural vocabulary is absent**: no multi-timeframe support, no
  levels, no swings, no breadth, no regime, no option chain reality.
- **Two findings from this audit are new and should not be lost**: the
  certification-granularity gap (Part 1.3), and the absence of any
  capture-event emitter (Part 0) — the latter being, in this auditor's
  view, the most important single item on the entire list.

The project is building eyes and ears before the brain. That sequencing is
right. The honest current position is: **the nervous system is well-designed
and not yet connected to anything.**

---

## Part 12 — Constraints honored by this document

- No schemas added. No new intelligence fields. No strategies.
- No market meaning inferred from incomplete data.
- Reality → Memory → Understanding → Intelligence hierarchy preserved
  throughout; every proposed future component is placed in exactly one layer.
- Every capability claim traced to a real code path and stated as
  *implemented*, *pipeline-ready*, *build*, or *impossible* — never as a
  hopeful "supported."
