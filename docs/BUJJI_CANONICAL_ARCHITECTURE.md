# Bujji — Canonical Target Architecture & Implementation Contract

**Architecture audit only. No production code written or modified.**

---

## 0. A meta-finding about this audit

This is my **third** correction to my own claims in four turns:

| # | I claimed | Reality |
|---|---|---|
| 1 | `tick/` is dead (0 importers) | Imported by `app.py` via relative import; 45 test files |
| 2 | Websocket symbol cap ~200 | `symbol_limit = 5000` in the SDK |
| 3 | No NSE holiday calendar exists | `bujji/market_calendar.py` exists — but is an **unverified template with 0 production consumers** |

**The pattern matters more than the individual errors.** In a 94-package / 856-file repository, absence is very hard to prove and easy to assert. Every future phase must therefore begin with *discovery*, and "missing" must be a verified conclusion, never a default from a grep that returned nothing.

This document marks unverifiable items **UNKNOWN** rather than assuming.

---

## A. Canonical architecture

```
                              LIVE MARKET (NSE)
                                     │
     ┌───────────────────────────────┼───────────────────────────────┐
     ▼                               ▼                               ▼
  WS SymbolUpdate (full)      WS DepthUpdate              REST chain / history
     │                               │                               │
     └───────────────────────────────┴───────────────────────────────┘
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L0  FEED ADAPTER          ── NEW.  raw payload preserved verbatim ║
   ║     (FyersTickFeed is INSUFFICIENT — discards 21 of 23 fields)    ║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L1  CANONICAL OBSERVATION ── market_observation (EXISTS, 31 imp.) ║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼
   ┌──────────────┬──────────────┬──────────────┬──────────────┐
   ▼              ▼              ▼              ▼              ▼
 TickStore   SnapshotStore  UniverseStore  MarketEventStore  (all NEW protocols)
   │              │              │              │
   │  ═══════ AUTHORITATIVE — never reconstructable ═══════
   ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L2  CANDLE ENGINE   ── live_observation (EXISTS) + 15Q semantics  ║
   ║     1m from ticks; 5/15/30/1H/D from 1m; W/M from D               ║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼  CandleStore (DERIVED cache)
   ┌─────────────────────────────────┴─────────────────────────────────┐
   ▼                                                                   ▼
 ╔═══════════════════════╗                          ╔══════════════════════════╗
 ║ L3a FEATURE ENGINE    ║                          ║ L3b DERIVATIVES ENGINE   ║
 ║  (15Q seed: 6 of ~40) ║                          ║  IV surface · OI · skew  ║
 ╚═══════════════════════╝                          ╚══════════════════════════╝
   └─────────────────────────────────┬─────────────────────────────────┘
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L4  PHENOMENA       ── msi_market_phenomena (EXISTS, 10 types)    ║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L5  MULTI-TIMEFRAME MARKET STATE  ── NEW. conflict-preserving     ║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L6  MSI INTELLIGENCE (EXISTS, 25 pkgs, UNCHANGED)                 ║
   ║   consensus → decision_synthesis → eligibility → selection → intent║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L7  RISK / CAPITAL   ── capital/ (EXISTS, 8 files, DISCONNECTED)  ║
   ╚══════════════════════════════════════════════════════════════════╝
                                     ▼
   ╔══════════════════════════════════════════════════════════════════╗
   ║ L8  LIFECYCLE → P&L → PORTFOLIO → ATTRIBUTION → MEMORY            ║
   ║     15G·15K·15L·15M·15J·15N — ALL EXIST, PROVEN, UNCHANGED        ║
   ╚══════════════════════════════════════════════════════════════════╝
```

**The fabric is L0–L5. L6–L8 already exist and must not be rebuilt.**

---

## B. Capability matrix

| Capability | State | Owner / note |
|---|---|---|
| Websocket feed + reconnect + watchdog | **EXISTS, DISCONNECTED** | `broker/fyers_ws` — wired only to legacy Stack A |
| Full-fidelity ingestion | **UNSAFE** | `FyersTickFeed` discards 21/23 fields |
| Canonical Observation model | **EXISTS** | `market_observation` (31 importers) |
| Raw tick persistence | **MISSING** | Nothing anywhere |
| Backpressure / drop accounting | **MISSING** | Silent loss possible |
| Tick aggregation | **EXISTS** | `live_observation` — late-tick correct |
| 1m / 15m / 30m / 1H / D / W / M | **MISSING** | Only 5m (15Q) |
| Candle semantics (OHLC, gap, forming) | **EXISTS (partial)** | 15Q — ~70% reusable |
| Instrument master | **EXISTS** | `broker/instrument_master`, public CSV |
| Universe manager | **MISSING** | No ATM/expiry rollover |
| **NSE calendar** | **EXISTS, UNVERIFIED, DISCONNECTED** | `market_calendar.py` — self-declares template; 0 prod consumers |
| Feed health states | **PARTIAL** | `ops/health_monitor`, `runtime_status` — not tick-aware |
| Data-quality vocabulary | **EXISTS** | `execution_reality` |
| Features / indicators | **PARTIAL** | 6 of ~40 (15Q) |
| Greeks | **PARTIAL** | `msi_greeks` — ATM only, per-cycle |
| IV surface / skew / term structure | **MISSING** | — |
| OI history / analytics | **PARTIAL** | REST OI verified exact; never stored as series |
| Phenomena | **EXISTS** | `msi_market_phenomena` (10 types) |
| Multi-timeframe state | **MISSING** | — |
| MSI intelligence chain | **EXISTS** | 25 packages, 15P-verified |
| Capital / margin | **EXISTS, DISCONNECTED** | `capital/` (8 files) |
| Lifecycle / P&L / portfolio / attribution / memory | **EXISTS, PROVEN** | 15G–15N |
| Replay | **DUPLICATED ×5** | `replay`, `mic_replay`, `replay_engine`, `msi_counterfactual_replay`, `qualification` |
| Uncertainty algebra | **MISSING** | 20 packages, 6+ scales, no composition |
| `as_of` query discipline | **MISSING** | Look-ahead currently possible |
| Feature / model / calc versioning | **MISSING** | 0 files reference `calc_version` |
| Sequence numbers / dup-tick handling | **MISSING / UNKNOWN** | 0 files; SDK exposure UNKNOWN |
| Corporate actions | **MISSING** | 0 files |
| Expiry settlement / rollover | **PARTIAL** | `settlement` 19 files, `rollover` 1 — coverage UNKNOWN |
| Auction / opening data | **PARTIAL** | 4 files — relevance UNKNOWN |
| Observability | **EXISTS** | `ops/` (alerts, health_monitor, incident_log), `dashboard/` |
| Research / backtest separation | **MISSING** | — |
| DR / corruption recovery | **PARTIAL** | EventStore torn-line tolerance; no DB-level plan |

---

## C. Data lineage — every current break

```
TICK ──✗ BREAK 1 ──► STORAGE ──✗ BREAK 2 ──► CANDLE ──► FEATURE ──✗ BREAK 3 ──►
PHENOMENON ──✗ BREAK 4 ──► STATE ──► OPPORTUNITY ──► DECISION ✓
```

| # | Break | Consequence |
|---|---|---|
| **1** | Feed → storage | Ticks reach `FyersTickFeed`, which keeps only `symbol`+`ltp` and persists nothing. **Authoritative evidence is destroyed at the ingestion boundary.** |
| **2** | Storage → candle | No `TickStore` exists; 15Q builds candles from synthetic ticks and discards them |
| **3** | Feature → phenomenon | `msi_market_phenomena` consumes intelligence-layer fields, not the 15Q feature surface |
| **4** | Phenomenon → state | No multi-timeframe state object; MSI receives 5 single-TF domain signals |
| ✓ | State → decision → lifecycle → memory | **Intact and proven** (15G–15P) |

**The chain is broken at its base, not its top.** Everything from L6 up works; L0–L5 is where the evidence is lost.

---

## D. Storage model (proposed, not implemented)

```
Tick                              AUTHORITATIVE
  instrument_id, exch_feed_time, receive_ts, ingest_seq
  ltp, bid_price, ask_price, bid_size, ask_size
  vol_traded_today, OI, last_traded_qty, last_traded_time
  tot_buy_qty, tot_sell_qty, avg_trade_price
  open_price, high_price, low_price, prev_close_price
  lower_ckt, upper_ckt, Yhigh, Ylow
  feed_mode, connection_id, source, quality_flags
  raw_payload                      ← verbatim, before normalization
  PK (instrument_id, exch_feed_time, ingest_seq)

Instrument                        AUTHORITATIVE
  instrument_id, symbol, underlying, kind
  strike, option_type, expiry_date        ← ABSOLUTE expiry is identity
  lot_size, tick_size, first_seen, last_seen, expired_at, settlement_price
  # expiry BUCKET is a query-time projection, never stored

UniverseChangeEvent               AUTHORITATIVE
  ts, instrument_id, action, reason
  atm_at_time, atm_distance_at_subscription   ← FROZEN at write
  spot_at_time, valid_from, valid_to

OptionSnapshot                    AUTHORITATIVE (own timeline)
  underlying, snapshot_ts, expiry_date, strike
  ce_oi, pe_oi, ce_prev_oi, pe_prev_oi, ce_oich, pe_oich, source

MarketEvent                       AUTHORITATIVE
  ts, kind (FEED_CONNECT|FEED_STALE|GAP|HOLIDAY|EXPIRY|SESSION_OPEN|...)
  payload, severity

Candle                            DERIVED
  instrument_id, timeframe, window_start, window_end
  o,h,l,c, volume?, oi?, tick_count
  source_tick_range, derived_from, is_closed, revision
  data_quality, calc_version

Feature                           DERIVED
  instrument_id, timeframe, ts, name, value?
  window, source_candle_range, sufficiency
  epistemic_state, confidence, limiting_factor, calc_version

Phenomenon / MarketState          DERIVED
  ... + evidence_refs[], epistemic_state, calc_version

Decision                          AUTHORITATIVE (a real event occurred)
  ts, market_state_ref, opportunity, selection, intent
  evidence_graph_ref, config_version, calc_versions{}
```

---

## E. Time model

**Four distinct timestamps. Never conflated.**

| Name | Meaning | Used for |
|---|---|---|
| `exch_feed_time` | Exchange event time | **Windowing, ordering, all analytics** |
| `broker_ts` | Broker server time, if supplied | Diagnostics — **UNKNOWN whether FYERS supplies one** |
| `receive_ts` | Local arrival (wall clock) | Latency, staleness, feed health |
| `persist_ts` | Durable write time | DR, throughput |
| `process_ts` | Derivation time | Feature lineage |

**Rules:**
- Candles are built on **event time**, never arrival time
- Latency = `receive_ts − exch_feed_time`; never assumed non-negative (clock skew is real)
- Ordering key = `(exch_feed_time, ingest_seq)`; arrival order is never trusted
- All queries are `as_of`-scoped on **event time**
- Session boundaries from `market_calendar` — **once verified** (§J.1)

**Watermark: DEFERRED.** The bound must come from Gate 1's measured p99 latency. Choosing it now would be a guess with permanent consequences.

---

## F. Uncertainty model

**Audit result: 20 packages define confidence constants across 6+ incompatible scales** (`CONFIDENCE_*`, `STATUS_*`, `PNL_*`, `QUALITY_*`, `STRENGTH_*`, epistemic 15N). **No composition semantics exist anywhere** — uncertainty resets at each layer boundary instead of propagating.

### Canonical epistemic state

```
EpistemicState ∈ { KNOWN, UNKNOWN, NOT_AVAILABLE, NOT_APPLICABLE, GAP, STALE }

Uncertainty = (
    state:           EpistemicState,
    confidence:      HIGH | MODERATE | LOW | NONE   (meaningful only when KNOWN)
    limiting_factor: str      ← WHICH input capped it
    provenance:      tuple[ref, ...]
)
```

### Composition semantics (the actual contract)

| Rule | Definition |
|---|---|
| **MIN-rule** | `confidence(out) ≤ min(confidence(critical inputs))`. Never stronger than its weakest critical input. |
| **UNKNOWN absorption** | Any critical input `UNKNOWN` → output `UNKNOWN`. Never averaged away. |
| **GAP taint** | Any `GAP` in the source window → output confidence capped at `LOW`, state carries `GAP`. |
| **STALE taint** | Input older than its timeframe's freshness bound → `STALE`; propagates. |
| **NOT_APPLICABLE isolation** | Does **not** degrade siblings (e.g. no management event ≠ missing evidence). |
| **Insufficiency ≠ low confidence** | Insufficient history produces **no value at all**, not a low-confidence value. |
| **limiting_factor mandatory** | Whenever output < max, it must name the capping input. This is what makes "why?" answerable. |

**Critical/non-critical must be declared per feature.** A feature that treats every input as critical will collapse to `UNKNOWN` constantly; one that treats none as critical launders uncertainty. This declaration is part of each feature's definition, not a global default.

**This is not an enum rename.** The existing vocabularies become adapters onto this model; none are deleted.

---

## G. Replay model

**Canonical owner: `replay_engine` (Phase 15H).** Proven, deterministic, already used by 15K/15L/15M/15N.

| Subsystem | Becomes |
|---|---|
| `replay_engine` (15H) | **CANONICAL** — the replay driver |
| `replay/` | **Corpus/session adapter** — feeds historical sessions into the canonical driver |
| `mic_replay` | **Publication-parity validator** — narrow, retained |
| `msi_counterfactual_replay` | **Research tool** on top of canonical replay |
| `qualification` | **Reporting/statistics** consumer |

**Nothing deleted.** Migration is by re-pointing, phase by phase.

### The parity contract

```
TickStore.range(instrument, t0, T, as_of=T)
   ↓ CandleEngine     ← same PURE function as live
   ↓ FeatureEngine    ← calc_version pinned
   ↓ Phenomena → State → MSI → Decision
```

**Honest boundary:** parity is claimable only across the **pure core**. The IO shell (timeouts, retries, reconnects, rate limits) cannot be identical. The pure/impure line must be drawn explicitly in code, and the parity test asserts only across the pure side — otherwise the guarantee is rhetorical.

---

## H. Gate plan

| Gate | Proves | Needs live market |
|---|---|---|
| **1** Feed capability | Field envelope, tick rate, latency p99, symbol cap, reconnect, depth cost | **YES** |
| **2** Capture integrity | One full session; **zero silent drops**; gaps explained; raw payload recoverable | **YES** |
| **3** Replay fidelity | Replay of a captured session reproduces it byte-identically | No |
| **4** Derivation trust | Candles/features deterministic; uncertainty composes; no look-ahead (asserted by API) | No |
| **5** Calendar trust | `market_calendar` verified against published NSE calendar; D/W/M bars correct | No |
| **6** End-to-end shadow | Full chain on fabric data, one unattended session | **YES** |

---

## I. Irreversible decisions

| Decision | Why irreversible | Recommendation |
|---|---|---|
| **Universe width** | Un-captured history can never be recovered. ATM±20 today forecloses ATM±25 research forever. | ±20 approved. Given `symbol_limit=5000`, consider **±25** — marginal cost, permanent option value. |
| **Raw tick retention** | Discarded ticks are gone | **Retain permanently.** Storage is the cheapest thing here. |
| **Timestamp policy** | Re-deriving event time later is impossible if not captured | Capture **all four**; use `exch_feed_time` for analytics. |
| **Contract identity** | Wrong identity corrupts all history | `(underlying, strike, option_type, absolute expiry_date)`. Bucket labels never stored. |
| **Raw payload preservation** | Cannot reconstruct fields never stored | **Store verbatim** alongside normalized. |
| **Storage granularity** | Aggregating on write is lossy | **Store every tick.** Candles are a cache. |
| **`as_of` in the API signature** | Retrofitting look-ahead safety across a built system is near-impossible | Make it a **required kwarg from the first line of code.** |
| **calc_version on every derived row** | Un-versioned history is permanently incomparable | Mandatory from day one. |

**Watermark is NOT yet irreversible** — deferred correctly until Gate 1.

---

## J. What we are still missing

### J.1 The calendar exists but is untrustworthy — **and nothing uses it**

`bujji/market_calendar.py` has `is_trading_day`, `is_half_day`, `next_trading_day`, `add_manual_closure`, and a `verification_warning()` that **self-declares the holiday table an unverified template**. Production consumers: **zero** (one test file).

Daily/Weekly/Monthly bars, expiry logic and session boundaries all depend on it. **Gate 5 must verify it against the published NSE calendar before any D/W/M bar is trusted.**

### J.2 Genuinely absent (0 files)

`trading_calendar` wiring · `corporate_action` · `sequence_number`/`seq_no` · `duplicate_tick` · `model_version` · `calc_version`/`feature_version` · research/backtest separation · IV surface · multi-timeframe state · uncertainty algebra · `TickStore`

### J.3 UNKNOWN — cannot be resolved from the repository

| Item | Why UNKNOWN |
|---|---|
| Does FYERS supply a **sequence number**? | Not in `map.json`'s 23 fields; wire protocol may carry one — **Gate 1 must inspect raw frames** |
| Does FYERS supply a **broker server timestamp** distinct from `exch_feed_time`? | Unknown |
| **Duplicate tick** frequency on reconnect | Requires Gate 1 fault injection |
| Are **corrections/amendments** ever published? | Unknown; would break immutability assumptions |
| **Auction / opening-call** data availability | `auction` appears in 4 files; relevance UNKNOWN |
| **Expiry settlement price** delivery | `settlement` in 19 files; coverage UNKNOWN |
| Server-side symbol cap vs SDK's 5,000 | Gate 1 |
| Does `DepthUpdate` share the symbol cap? | Gate 1 |

### J.4 Overlooked risks not on your list

1. **Clock skew is unbounded.** Latency uses `receive_ts − exch_feed_time` across two machines. Negative latencies are possible and must be recorded, not clamped — clamping would hide a real problem. NTP discipline on the VPS is a prerequisite, and its state should be an observation.
2. **The 15Q window-close flaw** (windows close only on the *next* tick — an illiquid strike can hold one open for hours) must be fixed when 15Q is folded in.
3. **Backpressure under 331 full-mode symbols is unmeasured.** The synchronous callback is the single most likely place to lose authoritative evidence.
4. **`capital/` is disconnected.** L7 risk/capital has no path from the new fabric. Autonomy is impossible without it.
5. **Two application stacks remain undecided.** Stack A owns the only working feed integration.
6. **No experiment tracking / research-live separation.** The moment features are versioned, you need to know which version produced which research conclusion.
7. **Config versioning exists in 7 files but is not tied to decisions.** A decision's reproducibility depends on the config that produced it.
8. **DB corruption recovery is unplanned** beyond EventStore's torn-line tolerance.

---

## Final answers

### 1. What Bujji already has
A proven intelligence and lifecycle stack (L6–L8): 25 MSI packages, phenomena, position lifecycle, canonical P&L, PaperBroker bridge, portfolio intelligence, outcome attribution, durable outcome memory — all replay- and recovery-tested. Plus a canonical observation model, correct tick aggregation, a working websocket with battle-tested reconnect handling, an instrument master, a verified REST option-chain, observability primitives, and a calendar skeleton.

### 2. What Bujji is missing
The entire authoritative substrate: **tick persistence, a full-fidelity feed adapter, storage protocols, universe management, the timeframe hierarchy, IV/OI series, multi-timeframe state, an uncertainty algebra, versioning, and `as_of` discipline.**

### 3. What is architecturally dangerous
- **`FyersTickFeed` destroys 21 of 23 fields at the ingestion boundary** — the single most dangerous item
- **Silent backpressure loss** — "connected" while losing ticks
- **Five replay subsystems** — parity is unverifiable
- **No uncertainty composition** — false precision propagates upward unchecked
- **Unverified calendar** that nothing consumes
- **Two application stacks** with undecided ownership

### 4. Irreversible decisions
Universe width · raw tick retention · timestamp capture · contract identity · raw-payload preservation · storage granularity · `as_of` in signatures · `calc_version` on every derived row. (§I)

### 5. What must happen before Gate 1
- `FYERS_PIN` added / token refreshed **(yours)**
- Non-expiry trading day confirmed, 10:00–13:00 IST window
- Universe rebuilt with the **real spot** at run time
- Confirm ±20 vs ±25 (§I) — must be decided *before* the run
- Gate 1 must additionally capture **raw wire frames** to answer the sequence-number question (§J.3)

### 6. Recommended execution sequence
```
GATE 1  feed measurement                          ← blocked on token + market
16C     full-fidelity adapter + TickStore
        + backpressure + feed health
GATE 2  capture integrity (zero silent drops)
16D     universe manager (rollover, lifecycle)
16E     multi-timeframe engine   ‖  16F backfill
GATE 5  calendar verification  ──┘
GATE 3  replay fidelity
16G     feature engine (versioned, uncertainty algebra)
16H     derivatives intelligence (IV surface, OI series)
GATE 4  derivation trust
16I     multi-timeframe state
16J     MSI integration (swap DomainSignal source)
GATE 6  end-to-end shadow
16K     research layer + tick replay
```

### 7. Is the 16A/16B plan still correct?
**Yes, with two refinements.** 16A's conclusion (consolidate, don't rewrite) survives every subsequent finding. 16B's scope was correct and has already returned value before running — the `FyersTickFeed` field-loss discovery alone would have invalidated 16C.

Refinements: **(a)** add raw-wire-frame capture to Gate 1 for the sequence-number question; **(b)** insert **Gate 5 (calendar verification)** — I previously called the calendar missing; it exists but is unverified, which is a different and more dangerous problem, because code that trusts it would be silently wrong.

### 8. What we have overlooked
Clock skew as a first-class observable · the 15Q window-close flaw · unmeasured backpressure at 331 symbols · `capital/` disconnection blocking autonomy · no experiment tracking or research/live separation · config versioning untied to decisions · no DB corruption plan · **and most importantly, the meta-finding in §0: in a repository this large, "missing" must be proven, not assumed.**

---

**No production code written or modified. Nothing deleted or merged. Working tree uncommitted at `b148e39`.**
