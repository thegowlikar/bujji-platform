# Bujji Re-Baseline — Market Intelligence Operating System

**Architectural assessment. No production code written.**

---

## 0. Two findings that change the plan

### Finding 1 — the websocket does not deliver the data the new scope requires

From `bujji/broker/fyers_ws.py`, live-verified and documented in-repo:

```
on_message payloads are {"symbol": ..., "ltp": ..., "type": ...}
litemode defaults to True
```

**The feed as currently configured delivers LTP only.** No bid, no ask, no bid/ask quantity, no volume, no OI, no depth.

Requirement §4 of the new scope — persist bid/ask/qty/volume/OI per tick — is **not satisfiable from the websocket today**, and it is unproven whether full mode supplies them for option symbols.

Worse, from `docs/DAY1_LIVE_SESSION_FINDINGS.md` (Sprint 114):

> A live session on **2026-07-28 received exactly one real tick all day** for `NSE:NIFTY50-INDEX` in lite mode.

Lite mode fires the callback only when raw LTP *changes*, and for an INDEX that comparison is the only field checked.

### Finding 2 — tick ingestion has a demonstrated silent-failure mode

From `docs/TICK_SILENCE_INCIDENT_P1.md`:

> **2026-07-29's Live Shadow Session #2 received real ticks only twice all day**, both before market open, then went completely silent for the remaining ~7.5 trading hours — `is_connected` stayed True and `reconnect_count` stayed 0 the entire time.

Root-caused to the `fyers_apiv3` SDK's internal reconnect path. `force_reconnect()` and `TickSilenceWatchdog` exist as mitigation.

**Consequence:** "capture every tick" is not primarily a storage problem. It is a **feed-reliability problem that has already bitten this project twice in production.** Any architecture that assumes ticks simply arrive is unsound.

### What this does to my earlier estimate

My prior assessment projected ~320 GB/year from ~7.3M ticks/day. **That estimate assumed rich, high-frequency ticks — an assumption this repo's own live evidence contradicts.** I am withdrawing it. The real figure cannot be derived from the repository and must be measured. See §F.

---

## A. Current capability map

### Package inventory — 94 packages, 856 files

**Backbone (high fan-in, genuinely canonical):**

| Package | Files | Importers | Role |
|---|--:|--:|---|
| `market` | 2 | 58 | Thin re-export shim over `brain.py` |
| `core` | 24 | 39 | Models, enums, clock, event bus |
| `market_observation` | 9 | **31** | **Canonical `Observation` model** |
| `broker` | 18 | 19 | FYERS/Paper/instrument master |

**MSI analytical family (~25 packages, 9 files each, consistently connected):**
`msi_price_structure` (30), `msi_market_structure` (25), `msi_consensus` (25), `msi_trade_construction` (24), `msi_decision_synthesis` (17), `msi_market_direction` (16), `msi_volatility_structure` (14), `msi_trade_thesis` (14), `msi_strategy_eligibility` (12)… — these are real, connected, and production-reachable via `intelligence_cycle_recorder`.

**Observation/tick family — the duplication cluster:**

| Package | Files | Importers | Verdict |
|---|--:|--:|---|
| `market_observation` | 9 | 31 | **CANONICAL** — owns the schema |
| `live_market_events` | 9 | 18 | Event layer — likely merge target |
| `options_observation` | 9 | 7 | Bhavcopy-shaped; **bid/ask never populated** |
| `live_observation` | 9 | 5 | **Tick→window aggregation** (correct; 15Q reuses) |
| `futures_observation` | 9 | 3 | Low use |
| `execution_reality` | 7 | 3 | Quote quality/data-quality vocabulary |
| `tick` | 3 | **0** | **DEAD** |
| `market_timeseries` | 6 | **0** | New (15Q) — at risk of becoming #8 |

**Zero-internal-consumer packages (19):**
`msi_position_recomposition`, `mil_next`, `runtime_safety`, `msi_evidence_packet`, `msi_engineering_evidence_board`, `shadow_validation`, `shadow_observatory`, `market_timeseries`, `signal`, `replay_engine`, `tick`, `shadow_trade_construction`, `portfolio_intelligence`, `integration`, `context_window`, `trade`, `shadow_lifecycle`, `intelligence_report`, `execution_integration`.

Not all are dead — several are recent phase deliverables (`replay_engine` 15H, `portfolio_intelligence` 15M, `shadow_trade_construction` 14, `outcome_memory` 15N) consumed only by tests. But that is precisely the measurement of the recurring problem: **~20% of the codebase has no internal consumer.**

**Legacy cluster:** `trading_brain` (139 files, 26 importers) — largest single package, `position_group_id`-keyed, confirmed architecturally incompatible with the new core across Phases 13/15L/15M/15N.

### Domain capabilities

| Domain | Package(s) | Connected? |
|---|---|---|
| Ticks | `broker/fyers_ws` | ❌ never reaches intelligence |
| Tick aggregation | `live_observation` | ⚠️ only to 15Q |
| Candles | `market_timeseries` (15Q) | ❌ synthetic only |
| Storage | `state_persistence`, `journal`, `mil_next` | ✅ events; ❌ no tick/market store |
| Indicators | `market_timeseries.indicators` | ❌ 6 functions |
| Regime | `market_regime_memory`, `msi_*` | ✅ |
| Volatility | `msi_volatility_structure` | ✅ |
| OI | `broker.get_option_chain` | ⚠️ fetched, never stored as series |
| Greeks | `msi_greeks` | ⚠️ ATM-only, per-cycle |
| Premium behaviour | `premium_behaviour` | ⚠️ per-cycle, not a series |
| Market state | `market_state`, `market_state_builder` | ✅ single-timeframe |
| Replay | `replay_engine`, `mic_replay` | ✅ **event** replay; ❌ no tick replay |
| Memory | `outcome_memory`, `market_regime_memory` | ✅ |
| Decision | `msi_decision_synthesis` → `msi_trade_intent` | ✅ |
| Execution | `broker/paper`, `position_lifecycle` | ✅ paper-only |

---

## B. Gap analysis

| Requirement | Status |
|---|---|
| Canonical observation model | **EXISTS + DUPLICATED** (7 subsystems) |
| Websocket tick feed | **EXISTS + DISCONNECTED** |
| Rich tick fields (bid/ask/qty/OI) | **MISSING** — litemode delivers LTP only |
| Raw tick persistence | **MISSING** |
| Tick reliability/watchdog | **PARTIAL** — watchdog exists, two live failures |
| Tick aggregation | **EXISTS + CONNECTED** (to 15Q only) |
| 1m/15m/30m/1H/D/W/M candles | **MISSING** |
| 5m candles | **PARTIAL** — synthetic only |
| Dynamic universe manager | **MISSING** |
| Expiry-as-identity | **PARTIAL** — `instrument_master` has expiry rows |
| Historical backfill | **PARTIAL** — REST verified, used ad-hoc |
| Feature engine | **PARTIAL** — 6 of ~40 |
| Multi-timeframe state | **MISSING** |
| Option-chain history | **PARTIAL** — captured to JSONL, not queryable |
| OI/Greeks/IV series | **MISSING** |
| IV surface / skew / term structure | **MISSING** |
| Market state (unified, multi-TF) | **PARTIAL** — single-TF exists |
| Evidence/lineage on features | **MISSING** |
| Tick replay | **MISSING** (event replay exists) |
| No-look-ahead enforcement | **MISSING** — no `as_of` API |
| Data-quality engine | **PARTIAL** — `execution_reality` vocabulary only |
| Research query layer | **MISSING** |
| Storage abstraction | **MISSING** — 15Q returns SQLite types directly |

---

## C. Target architecture

```
FYERS FEED (ws + REST)
   ↓  [adapter: normalize, dedupe, quality-tag]
CANONICAL OBSERVATION MODEL   ← market_observation (existing, canonical)
   ↓
TickStore (protocol)          ← raw ticks = AUTHORITATIVE
   ↓                    ↘
CandleEngine (1m→…)      MicrostructureEngine
   ↓
CandleStore (protocol)        ← materialized VIEW, reproducible
   ↓                    ↘
FeatureEngine            DerivativesEngine (IV/OI/skew)
   ↓                    ↙
MarketStateEngine (multi-TF, conflict-preserving)
   ↓
MARKET INTELLIGENCE CORE      ← existing msi_* family plugs in here
   ↓
DECISION → POSITION → MANAGEMENT → OUTCOME → MEMORY → LEARNING
```

**Binding rules:**
1. Intelligence imports **protocols only**, never `sqlite3`/`duckdb`.
2. Ticks are truth; candles are a cache, always re-derivable.
3. Every read is `as_of`-scoped. No unscoped reads exist in the API.
4. One aggregation implementation, shared by live and replay (the Phase 15H precedent).
5. Every stored row carries `source ∈ {LIVE_TICK, HISTORICAL_API, RECONSTRUCTED, SYNTHETIC_TEST}`.

---

## D. Consolidation plan (no deletions without approval)

| Package | Action | Rationale |
|---|---|---|
| `market_observation` | **CANONICAL — keep, extend** | 31 importers; already owns the schema |
| `live_observation` | **Keep as aggregation engine** | Correct late-tick/no-fabrication semantics |
| `market_timeseries` | **Rework into the storage/candle layer** | Must not become subsystem #8 — see §I |
| `execution_reality` | **Promote to canonical data-quality vocabulary** | Already has `DATA_QUALITY_*` |
| `options_observation` | **Adapter** | Bhavcopy-shaped; bid/ask never populated — wrap, don't extend |
| `futures_observation` | **Adapter** | Low use |
| `live_market_events` | **Evaluate for merge into `market_observation`** | 18 importers — merge carefully |
| `tick` | **Propose deletion** (0 importers, 3 files) | ⚠️ needs your approval |
| `trading_brain` | **Freeze** | Legacy; incompatible identity scheme |
| 19 zero-consumer packages | **Triage, don't delete** | Several are pending-integration phase work |

**Schema ownership:** `market_observation` owns identity/value/quality/provenance. Everything else adapts to it. This is the single most important consolidation decision — without it, every new store invents its own row shape.

---

## E. Data model (canonical shapes)

```
Instrument
  instrument_id (surrogate), symbol, underlying, segment
  kind: SPOT|VIX|OPTION|FUTURE
  strike, option_type, expiry_date   ← expiry_date is IDENTITY
  lot_size, tick_size, first_seen, last_seen

  # expiry BUCKET (CURRENT_WEEK/...) is NEVER stored — derived at query time
  # from (expiry_date, as_of_timestamp)

Tick
  instrument_id, exchange_ts, receive_ts, seq
  ltp
  bid, ask, bid_qty, ask_qty        ← Optional; NULL = NOT_AVAILABLE
  volume, oi, prev_close             ← Optional
  source, feed_mode (LITE|FULL), quality_flags
  PK (instrument_id, exchange_ts, seq)

Candle
  instrument_id, timeframe, window_start, window_end
  o,h,l,c, volume(Optional), oi(Optional)
  tick_count, source_tick_range, derived_from (TICKS|1M|DAILY)
  is_closed, data_quality, calc_version

OptionChainSnapshot        ← REST-sourced, distinct from ticks
  underlying, snapshot_ts, expiry_date, strike
  ce_oi, pe_oi, ce_prev_oi, pe_prev_oi, ce_oich, pe_oich
  source=HISTORICAL_API|LIVE_REST

GreeksSnapshot
  instrument_id, ts, iv, delta, gamma, theta, vega
  input_spot, input_strike, input_expiry, input_rate, input_vol
  model, calc_version                ← inputs stored WITH the result

Feature
  instrument_id, timeframe, ts, name, value(Optional)
  window, source_candle_range, sufficiency, data_quality, calc_version

MarketState
  timeframe, ts, trend, volatility_regime, structure, momentum
  liquidity, options_regime, confidence, evidence_refs[], completeness

UniverseSnapshot / UniverseChangeEvent
  ts, instrument_id, action(SUBSCRIBE|UNSUBSCRIBE)
  reason, atm_at_time, atm_distance_at_subscription
  valid_from, valid_to
```

Two invariants worth stating explicitly:
- **`expiry_date` is identity; `CURRENT_WEEK` is a query-time projection.** Storing the label would silently mislabel every historical query after rollover.
- **`atm_distance_at_subscription` is frozen at write time**, never recomputed — otherwise history mutates as spot moves.

---

## F. Storage model — **cannot be estimated, must be measured**

| Input | Value | Confidence |
|---|---|---|
| Instruments | ~162 | Design choice |
| Session | 22,500 s | Known |
| **Ticks/sec/instrument** | **UNKNOWN** | ⚠️ Repo evidence shows **1 tick/day** for index in lite mode |
| Bytes/row (lite) | ~40–60 | Derivable |
| Bytes/row (full) | ~150–200 | If full mode delivers depth — **unverified** |

Two regimes, orders of magnitude apart:

- **Lite mode**, LTP-on-change: possibly < 1 M ticks/day → **< 20 GB/year**. SQLite alone is comfortably sufficient.
- **Full mode**, every broadcast: potentially 10–50 M ticks/day → **300 GB–1 TB/year**. Requires Parquet/DuckDB archival.

**Recommendation:** define the `TickStore` protocol now; back it with SQLite/WAL; **measure one real session**; then decide archival. Do not build the Parquet/DuckDB tier before the measurement justifies it.

**Query patterns to design for:** point-in-time as-of reads; instrument×timeframe range scans; cross-instrument time-aligned joins (spot vs ATM CE vs PE vs VIX); expiry-relative aggregation.

**Retention:** ticks permanent; candles materialized but re-derivable; state/features versioned by `calc_version`.

---

## G. Broker capability audit

| Capability | Status | Evidence |
|---|---|---|
| WS tick: `ltp` | ✅ **VERIFIED** | `{"symbol","ltp","type"}`, live 2026-07-28 |
| WS tick: bid/ask/qty | ❌ **NOT in lite mode** | Payload shape above |
| WS tick: volume/OI/depth | ❓ **NEEDS LIVE VERIFICATION** | Unknown whether full mode supplies them for options |
| WS full mode behaviour | ⚠️ Fires on nearly every broadcast | SDK source read, in-repo |
| **Symbol limit / connection limit** | ❓ **NEEDS LIVE VERIFICATION** | Not established anywhere in repo |
| Tick throughput / burst | ❓ **NEEDS LIVE VERIFICATION** | — |
| **Reconnect semantics** | ⚠️ **KNOWN DEFECTIVE** | SDK calls `on_connect` after flat `sleep(2)`; `on_close` never fires when `reconnect=True` |
| Silent-stall risk | ⚠️ **DEMONSTRATED TWICE** | 2026-07-29, 7.5 h silence, `is_connected=True` |
| REST option chain: OI | ✅ **VERIFIED** | `oi`/`prev_oi`/`oich`, `oich == oi - prev_oi` exact, 2026-07-20 |
| REST option chain: bid/ask/IV | ❓ **NEEDS LIVE VERIFICATION** | Only OI extraction is implemented |
| REST historical candles | ✅ **VERIFIED** | `[epoch,o,h,l,c,v]` under `candles` |
| REST historical **options** | ❓ **NEEDS LIVE VERIFICATION** | Likely unavailable — assume not until proven |
| Rate limits | ⚠️ Real incident | Double-fetch caused throttling 2026-08-06 |

**Six blocking unknowns**, all requiring one instrumented live session.

---

## H. Timeframe architecture

**One engine. Derive from 1m, not by chaining.**

| TF | Derived from | Alignment |
|---|---|---|
| 1m | **Ticks** (authoritative) | Clock |
| 5m/15m/30m | 1m | Clock; divides evenly |
| 1H | 1m | 09:15–10:15 (session-anchored, **not** 09:00) |
| Daily | 1m | Session 09:15–15:30 IST |
| Weekly | Daily | ISO week |
| Monthly | Daily | Calendar month |
| **4H** | — | **Excluded** — 6h15m session cannot yield equal 4H bars |

Chaining (1m→5m→15m→1H) accumulates error and makes provenance ambiguous. Single-source derivation keeps every bar traceable to one authority, and `derived_from` records it.

**Missing prerequisite: no NSE holiday calendar exists anywhere in the repo.** Daily/Weekly/Monthly are not trustworthy without it.

---

## I. Intelligence architecture

The existing `msi_*` family is the intelligence layer — it is real, connected, and should be **fed better evidence, not replaced**.

```
CandleStore + FeatureStore + DerivativesStore
        ↓  (as_of-scoped reads only)
MarketStateEngine — one state per timeframe
        ↓
MultiTimeframeState — preserves CONFLICT, never flattens
        ↓
msi_consensus → msi_decision_synthesis → msi_trade_intent   [EXISTING]
        ↓
position_lifecycle → management → outcome → memory          [EXISTING]
```

**No new decision engine.** `msi_decision_synthesis` remains the single opportunity resolver (Phase 15P proved it correct and fully state-reachable). The change is upstream: it currently receives 5 single-timeframe domain signals; it should receive multi-timeframe state.

**Multi-TF conflict must be represented, not averaged.** "Daily bullish, 15m bearish" is information. Collapsing it to a scalar destroys the most valuable signal in the system.

---

## J. Replay architecture

```
TickStore.range(instrument, t0, T)          ← only source of truth
     ↓ CandleEngine (same code as live)
     ↓ FeatureEngine (calc_version pinned)
     ↓ MarketStateEngine
     ↓ msi_* intelligence
     ↓ decision
```

Four guarantees:
1. **Single implementation** — live and replay call the same functions (Phase 15H/15K precedent, already proven).
2. **`as_of` on every read** — `window_end <= T`, enforced in the API signature so a caller cannot forget.
3. **`calc_version` pinned** — replaying with today's code but yesterday's version must be detectable.
4. **Forming candles excluded** — type-level, extending 15Q's `Candle`/`FormingCandle` split.

Testable claim: replay of a session must reproduce the live-recorded state transitions byte-identically. That is the acceptance criterion.

---

## K. Migration roadmap — **revised**

Your proposed order is close, with **one structural change**: feed measurement must come before universe/storage design, because the litemode finding shows the feed's actual capability is unknown and determines everything downstream.

| Phase | Name | Why here |
|---|---|---|
| **16A** | **Consolidation + Storage Protocols** | Resolve 7-subsystem duplication; define `TickStore`/`CandleStore`; no new capability |
| **16B** | **Feed Capability Measurement** ⟵ **NEW** | Measure lite vs full fields, tick rate, symbol cap, reconnect. **One instrumented session.** Blocks 16C–16E |
| **16C** | Tick Ingestion + Reliability | Persist ticks; watchdog; gap events; dedupe. Reliability is *part of* this phase, not later |
| **16D** | Dynamic Universe Manager | Needs 16B's symbol cap |
| **16E** | Multi-Timeframe Engine | Needs real ticks |
| **16F** | Historical Backfill | Independent of ticks — **can run parallel to 16C–16E** |
| **16G** | Feature Engine | Needs 16E + 16F |
| **16H** | Derivatives Intelligence | Needs 16D + REST chain |
| **16I** | Unified Multi-TF Market State | Needs 16G + 16H |
| **16J** | Intelligence Runtime Integration | Needs 16I |
| **16K** | Research + Tick Replay | Needs all |

**Two changes from your draft:** feed measurement inserted as 16B (blocking), and backfill (16F) moved to run in parallel since it depends only on the REST API.

---

## Risks

| Risk | Severity | Mitigation |
|---|:--:|---|
| Full mode still lacks bid/ask/OI | **High** | Measure in 16B; fall back to REST snapshot tiers |
| Silent tick stall (demonstrated ×2) | **High** | Watchdog + gap events mandatory in 16C |
| Symbol cap < 162 | **Medium** | Tiered universe; partition across connections |
| `market_timeseries` becomes subsystem #8 | **High** | 16A reworks it before extending |
| Consolidation breaks 31 importers | **Medium** | Adapters, not rewrites; additive only |
| Building 16E–16I on synthetic data | **High** | Phase 15P's exact failure mode — gate on real ticks |
| Storage over-engineering | Low | Measure before adding Parquet/DuckDB |

## Must be experimentally verified

1. Full-mode tick payload fields for **option** symbols (bid/ask/qty/volume/OI/depth)
2. Real tick rate per instrument, lite vs full
3. Websocket symbol cap and connection cap
4. Reconnect/resubscribe semantics under real disconnect
5. REST option-chain fields beyond OI (bid/ask/IV/volume)
6. Whether historical **options** data exists at all

All six from **one instrumented live session** — that is Phase 16B.

## What Phase 15Q becomes

**Keep the semantics; demote it from "the store" to "the candle layer."**

| Keep | Rework |
|---|---|
| OHLC exactness | Ticks become authoritative; candles a derived cache |
| Clock-aligned windows | 5m → full TF hierarchy |
| Gap honesty (no forward-fill) | Add `source`, `calc_version`, `derived_from`, `data_quality` |
| `Candle`/`FormingCandle` split | Extend to all timeframes |
| Insufficient-history → `None` | Add `as_of` scoping to all reads |
| Immutable store, conflict rejection | Hide SQLite behind protocols |
| `live_observation` reuse | Fold into the consolidated observation stack |

Roughly **70% of 15Q survives**. The store interface and the missing tick layer are what change.

---

## Final verdict

**Is the architecture strong enough to begin building the autonomous Quant Desk?**

**The intelligence layers: yes. The data foundation: no, not yet.**

- Decision/position/management/outcome/memory (15G–15P) are genuinely strong — replay-proven, recovery-proven, safety-tested.
- The observation layer is **fragmented (7 subsystems), disconnected (feed never reaches intelligence), and unmeasured (feed capability unknown).**

**Is the architecture fundamentally fragmented?** Yes — measurably. 94 packages, ~20% with zero internal consumers, 7 overlapping observation subsystems, and a production runtime that imports none of them. This is not a judgement call; it is what the import graph shows.

**But it is recoverable without a rewrite.** The canonical model (`market_observation`), the aggregation engine (`live_observation`), the intelligence family (`msi_*`), and the lifecycle/memory stack are all sound. What is missing is a **spine**: one storage abstraction, one tick path, one timeframe engine, connected end to end.

The correct next step is **16A (consolidation) followed immediately by 16B (feed measurement)** — and explicitly *not* more intelligence, until real ticks are flowing and the feed's true capability is known.
