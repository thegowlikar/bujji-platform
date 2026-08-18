# Bujji — Architecture Lock Audit

**Final audit before implementation. No code written, deleted, merged, or refactored.**

## Locked decisions (accepted)

1. **Universe** — NIFTY ATM±25 across 4 expiries + spot + futures + VIX = **411 instruments**. Gate 1 measures operational sustainability. Universe is never silently reduced for storage convenience.
2. **Architecture** — incremental harvest Stack A → Stack B behind stable interfaces. No rewrites for purity. No duplicate live paths. Retirement only after parity + replay + shadow + operational proof.
3. **Canonical replay** — `replay_engine` (15H). Others reclassified, not deleted.
4. **Gate 1** — full-mode FYERS measurement. Token is the only external blocker. Raw wire payloads preserved.
5. **Watermark** — data-driven only. No constant chosen before measurement.

---

## A. Dependency DAG

```
RAW MARKET EVENTS
   │ ✗ DISCONNECTED ── FyersTickFeed keeps 2 of 23 fields; persists nothing
   ▼
AUTHORITATIVE STORAGE
   │ ✗ DISCONNECTED ── no TickStore exists
   ▼
MULTI-TF DATA
   │ ⚠ PARTIAL ── 5m only, synthetic ticks, tick-driven closure flaw
   ▼
FEATURES
   │ ⚠ PARTIAL ── 6 of ~40, synthetic; no lineage/versioning
   ▼
PHENOMENA
   │ ⚠ PARTIAL ── msi_market_phenomena consumes MSI fields, not the feature surface
   ▼
STATE
   │ ⚠ PARTIAL ── single-timeframe; no MultiTimeframeMarketState
   ▼
OPPORTUNITY
   │ ✓ CONNECTED ── 15P: 684/708 real cycles, all states reachable
   ▼
STRATEGY
   │ ✓ CONNECTED ── taxonomy bridge verified; disagreement disclosed
   ▼
TRADE INTENT
   │ ✗ DISCONNECTED (Stack B) ── no risk layer downstream
   ▼
RISK / CAPITAL / MARGIN
   │ ⛔ LEGACY ── exists richly in Stack A (24 consumers, 28 tests); ZERO in Stack B
   ▼
EXECUTION
   │ ⚠ PARTIAL ── PaperBroker only (correct); live gated
   ▼
POSITION LIFECYCLE
   │ ✓ CONNECTED (fixture) ── 15G/15L bridge proven
   ▼
PORTFOLIO
   │ ✓ CONNECTED (fixture) ── 15M
   ▼
OUTCOME
   │ ✓ CONNECTED (fixture) ── 15J
   ▼
MEMORY
   │ ✓ CONNECTED (fixture) ── 15N; population empty
   ▼
RESEARCH / KNOWLEDGE
     ✗ DISCONNECTED ── no promotion path; no experiment tracking
```

**Edge summary:** 5 CONNECTED · 5 PARTIAL · 4 DISCONNECTED · 1 LEGACY · 0 UNKNOWN.

**Two structural breaks:** the substrate (RAW→STORAGE→MULTI-TF) and the risk gap (INTENT→RISK, stranded in Stack A).

---

## B. Stack A harvest register

| Capability | Canonical impl | Consumers | Tests | Stack B equiv | Migration target | Interface boundary | Retirement condition |
|---|---|--:|--:|---|---|---|---|
| **Capital / margin** | `capital/` (engine, policy, providers, broker_adapter) | 24 | 28 | **NONE** | New `L10` service in Stack B | `CapitalPolicy` / `MarginProvider` protocols | Stack B path passes shadow + parity vs Stack A on same inputs |
| **Risk governor** | `trading_brain/risk_governor/` (adaptive governor, whole-book margin, capital_check, defined_risk) | 4 | — | **NONE** | `L10` risk service | `RiskDecision` protocol | Parity on recorded decisions + replay determinism |
| **Position sizing** | `trading_brain/position_sizing/` | — | — | **NONE** | `L10` | Sizing protocol | Parity + margin correctness |
| **Execution** | `execution/` | 23 | **59** | `broker/paper` + 15L bridge | Keep Stack A for legacy; Stack B uses 15L | `Broker` contract (already shared) | Only when legacy app retires |
| **Broker integration** | `broker/` (fyers, paper, factory, instrument_master, token mgr) | 19 | — | **SHARED — already canonical** | No migration needed | `Broker` ABC | N/A — keep |
| **Feed handling** | `broker/fyers_ws` (`FyersTickFeed` + watchdog) | 2 | — | **INSUFFICIENT** (drops 21/23 fields) | **New full-fidelity adapter**; harvest reconnect/watchdog *patterns*, not the class | `FeedAdapter` protocol | Gate 2 proves new adapter ≥ old reliability |
| **Operational safety** | `runtime_safety/` | 5 | 2 | **NONE** | `L17` in Stack B | Health/kill-switch protocol | Kill switch exists + tested |
| **Journal / audit** | `journal/` (25 files, SQLite) | 24 | **42** | `state_persistence` EventStore | Coexist; different purposes | Store protocols | No retirement — distinct roles |
| **Signal infra** | `signal/` | 4 | 9 | `msi_*` chain | **Do not harvest** — ORB-VWAP-specific | — | Retire with legacy app |
| **Tick engine** | `tick/` (position-exit monitor) | 1 | 3 | 15I management | **Do not harvest** — different purpose | — | Retire with legacy app |
| **Trade manager** | `trade/` | 5 | 11 | `position_lifecycle` (15G) | **Do not harvest** — superseded | — | Retire with legacy app |
| **Authentication** | `authentication/` | 7 | 5 | **SHARED** | Keep | — | N/A |
| **Ops / alerts** | `ops/` (alerts, health_monitor, incident_log) | 3 | 1 | **NONE in Stack B** | `L17` | Health protocol | Stack B health wired |

**Harvest priority: capital + risk governor + position sizing (L10), operational safety (L17), feed reliability patterns.** Everything else is either shared already or legacy-specific.

**Explicit non-goal:** `signal/`, `tick/`, `trade/` are ORB-VWAP-specific. They are superseded, not harvested. They stay untouched until the legacy app retires as a unit.

---

## C. Canonical ownership (one declared destination each)

| Concept | Canonical owner | Status |
|---|---|---|
| Instrument identity | `broker/instrument_master` | ✅ exists |
| Market event | `market_observation` | ✅ exists (31 imp.) |
| **Tick** | **`TickStore` protocol** | ❌ **to build** |
| Candle | `market_timeseries` (reworked, behind protocol) | ⚠️ rework |
| Option-chain snapshot | `SnapshotStore` protocol; `options_observation` as adapter | ❌ to build |
| Feature | Feature Engine (15Q seed) | ⚠️ extend |
| Phenomenon | `msi_market_phenomena` | ✅ exists |
| Market state | `MultiTimeframeMarketState` | ❌ to build |
| Opportunity state | `msi_decision_synthesis` | ✅ exists (15P-verified) |
| Trade intent | `msi_trade_intent` | ✅ exists |
| **Risk decision** | **new L10 service (harvest from Stack A)** | ❌ **to build** |
| Order intent | `broker.OrderRequest` | ✅ exists |
| Execution result | `broker.OrderResult` / `ExecutionReport` | ✅ exists |
| Position lifecycle | `position_lifecycle` (15G) | ✅ exists |
| Portfolio state | `portfolio_intelligence` (15M) | ✅ exists |
| Outcome | `outcome_attribution` (15J) | ✅ exists |
| Memory | `outcome_memory` (15N) | ✅ exists |
| Replay | `replay_engine` (15H) | ✅ exists |

**Four to build. Everything else has a declared owner today.**

---

## D. Authoritative vs derived vs ephemeral

| Dataset | Class | Note |
|---|---|---|
| Raw FYERS payload | **AUTHORITATIVE** | Verbatim, immutable, never reconstructable |
| Normalized tick | **AUTHORITATIVE** | Derived *shape*, authoritative *content* — raw retained alongside |
| REST option-chain snapshot | **AUTHORITATIVE** | Own timeline; not derivable from ticks |
| Instrument master snapshot | **AUTHORITATIVE** | Point-in-time contract reality |
| Universe change event | **AUTHORITATIVE** | Decision about what to observe |
| Feed/session events | **AUTHORITATIVE** | Connect/stale/gap/reconnect |
| Calendar/expiry events | **AUTHORITATIVE** | Once verified |
| Closed candle (any TF) | **DERIVED** | Materialized cache; re-derivable from ticks |
| **Forming candle** | **EPHEMERAL** | Never persisted; always reconstructable |
| Feature / indicator | **DERIVED** | + `calc_version` |
| Greeks / IV | **DERIVED** | + inputs stored with result |
| Phenomenon | **DERIVED** | |
| Market state / multi-TF state | **DERIVED** | |
| Opportunity / selection / intent | **DERIVED** | Recomputable from state + config |
| **Trade decision (acted upon)** | **AUTHORITATIVE EVENT** | A real thing happened |
| Order / execution / fill | **AUTHORITATIVE** | Broker reality |
| Position lifecycle events | **AUTHORITATIVE** | Real state transitions |
| Portfolio snapshot | **DERIVED** | From lifecycle + broker state |
| Outcome attribution | **DERIVED** | Analytical |
| Memory record | **AUTHORITATIVE (immutable)** | Historical fact once written |

**Ambiguity resolved:** a *proposed* intent is DERIVED (recomputable); an *acted-upon* decision is an AUTHORITATIVE EVENT. The boundary is action, not computation.

---

## E. Lineage requirements — and what currently fails them

Every derived artifact must carry: `source_event_ids` · `instrument_id` · `event_timestamps` · `as_of` · `timeframe/window` · `calc_version` · `feature_version` · `state_version` · `config_version` · `code_version`.

| Requirement | Status |
|---|---|
| source event IDs | ❌ **fails** — no tick IDs exist |
| instrument identity | ✅ available |
| event timestamps | ⚠️ available at feed, **not persisted** |
| `as_of` | ❌ **fails** — no `as_of` in any store API |
| timeframe/window | ✅ 15Q has it |
| `calc_version` | ❌ **fails** — 0 files in repo |
| feature/state version | ❌ **fails** |
| `config_version` | ⚠️ exists in 7 files, **not tied to decisions** |
| code/build version | ❌ **fails** |

**Seven of ten fail today.** All are cheap at construction and near-impossible to retrofit — they must be mandatory from the first line of 16C.

---

## F. Time semantics (locked)

| Concept | Definition | Authority |
|---|---|---|
| **Exchange time** | `exch_feed_time` — when the market event occurred | **Windowing, ordering, all analytics** |
| **Receive time** | Local arrival | Latency, staleness, health |
| **Processing time** | When derivation ran | Lineage only |
| **`as_of`** | The instant a query is answered *as if* | **Required kwarg on every read** |
| **Event time** | = exchange time | Canonical |
| **Session date** | NSE trading day (09:15–15:30 IST) | From calendar |
| **Expiry date** | Absolute contract expiry | **Identity** |
| **Calendar date** | Wall-clock date | Never used for windowing |

**Future-leakage prevention:**
- Ordering key `(exch_feed_time, ingest_seq)` — arrival order never trusted
- `as_of` a **required** kwarg → forgetting it is a `TypeError`, not a review miss
- Forming candles are a distinct type without a `close` field → cannot be consumed as settled history
- Watermark seals windows on a clock, not on tick arrival
- Replay reads only `window_end <= as_of`

**Identified leakage risk:** none structurally prevented **today**. All four mechanisms are new work in 16C/16E.

---

## G. Uncertainty semantics (locked)

```
EpistemicState ∈ {KNOWN, UNKNOWN, NOT_AVAILABLE, NOT_APPLICABLE,
                  INSUFFICIENT_HISTORY, STALE, GAP, DEGRADED}

Uncertainty = (state, confidence, limiting_factor, criticality,
               freshness, completeness, provenance[])
```

**The five required answers:**

| Question | Answer |
|---|---|
| Feature KNOWN but **stale**? | State → `STALE`. Value retained, confidence capped at `LOW`, `limiting_factor="freshness:<age>"`. Consumers may use it; risk/execution layers **must** reject it. |
| One **critical** input UNKNOWN? | Output → `UNKNOWN`. Absorption, not averaging. No value emitted. |
| One **non-critical** input UNKNOWN? | Output stays `KNOWN`; confidence drops one band; `limiting_factor` names it; `completeness` reduced. |
| Timeframe has a **gap**? | Output carries `GAP`, confidence capped `LOW`. Range-dependent features (ATR, Bollinger, realised vol) return **no value** — a gap breaks their definition, not just their quality. |
| Higher timeframe **not yet formed**? | `INSUFFICIENT_HISTORY` — **not** `UNKNOWN`. Distinct because it self-heals with time, and because "not yet" must never be learned from as "unknowable". |

**Criticality is declared per feature.** Everything-critical collapses to UNKNOWN constantly; nothing-critical launders uncertainty. This declaration is part of each feature's definition.

**Propagation:** `tick(quality) → candle(gap, tick_count) → feature(sufficiency) → phenomenon → state → opportunity → strategy → decision`, with `limiting_factor` preserved end-to-end. That chain is what answers *"why does Bujji believe this, and what is the weakest evidence?"*

---

## H. The MONITOR problem — resolved architecturally (no thresholds changed)

Today `MONITOR` conflates at least three distinct conditions. Locked separation:

| State | Meaning | Learnable? | Action |
|---|---|:--:|---|
| `NO_EDGE` | Evidence **sufficient**; genuinely no opportunity | ✅ yes | Correct inaction |
| `INSUFFICIENT_EVIDENCE` | Cannot determine — evidence missing | ❌ **no** | Observe |
| `DATA_DEGRADED` | Evidence present but untrustworthy (stale/gap) | ❌ no | Observe; alarm |
| `MONITOR` | Opportunity forming, not yet resolved | ✅ yes | Watch |
| `ELIGIBLE` | Opportunity exists; constraints unchecked | ✅ yes | Candidate |
| `TRADEABLE` | Eligible **and** risk/liquidity/health permit | ✅ yes | Actionable |
| `BLOCKED_BY_RISK` | Opportunity real; risk refused | ✅ yes | Not taken |
| `BLOCKED_BY_LIQUIDITY` | Opportunity real; liquidity refused | ✅ yes | Not taken |
| `BLOCKED_BY_OPERATIONAL_HEALTH` | Opportunity real; system unhealthy | ✅ yes | Not taken |

**Why this matters more than thresholds:** a learning system trained on a corpus where "there was nothing" and "I couldn't tell" share a label will learn nonsense. The `BLOCKED_BY_*` states are equally load-bearing — they are the *only* way to later measure opportunity cost of the risk policy.

`ELIGIBLE`→`TRADEABLE` requires L10, which Stack B lacks. **This state cannot exist until the risk harvest lands.**

---

## I. Autonomy boundary

| Stage | Bujji may | Bujji may NOT | Status |
|---|---|---|---|
| **OBSERVE** | Capture, persist, quality-tag | Derive conclusions | 16C |
| **ANALYZE** | Candles, features, phenomena, state | Rank or propose | 16E–16I |
| **RANK** | Opportunity states, ordering | Select a strategy | ✅ exists |
| **PROPOSE** | Trade intent | Size, execute | ✅ exists |
| **PAPER EXECUTE** | PaperBroker orders, lifecycle, P&L | Touch live broker | ✅ exists |
| **SHADOW MANAGE** | Recommend HOLD/ADJUST/HEDGE/ROLL/EXIT | Auto-execute | ✅ exists (advisory) |
| **LIVE EXECUTE** | — | **Everything — gated** | ⛔ separate audited gate |

**Five inviolable boundaries:**

| Boundary | Enforcement | Status |
|---|---|---|
| Memory ↛ decisions | AST safety test forbids `outcome_memory` importing decision paths | ✅ **enforced (15N)** |
| Research ↛ production | Separate stores; promotion requires explicit gate | ❌ **not built** |
| Strategy ↛ bypass risk | Intent must pass L10 before any order | ❌ **not built — L10 absent** |
| Risk ↛ bypass operational safety | Health state gates risk approval | ❌ **not built** |
| Execution ↛ bypass lifecycle reconciliation | 15L bridge is the only path | ✅ **enforced (fixture)** |

**Two of five enforced. Three depend on L10/L17 harvest.**

---

## J. Autonomous position management (design only)

```
                    ┌──────────── MarketState(t) ────────────┐
                    ▼                                        ▼
OPEN ─► MONITOR ─► THESIS VALIDATION ──intact──► HOLD ───────┘
                          │
                    invalidated / weakening
                          ▼
              ┌───────────┴───────────┐
              ▼                       ▼
      RISK-DRIVEN                THESIS-DRIVEN
   REDUCE · HEDGE            ADD · ROLL · PARTIAL EXIT
              └───────────┬───────────┘
                          ▼
                  CONSTRAINT GATE  (ALL must pass)
        risk · margin · capital · liquidity · expiry
        · portfolio exposure · operational health
                          ▼
                  FULL EXIT ─► RE-ENTRY (new position identity)
```

**Locked principles:**
- Management is **thesis-driven, constraint-bounded**. A thesis-valid action that fails any constraint is **not taken and recorded as blocked** — never silently skipped.
- **RE-ENTRY always mints a new `position_id`.** Never reuse identity across a flat gap — 15G's identity rule already guarantees this.
- Expiry is a **hard** constraint: an unmanageable position near expiry forces exit regardless of thesis.
- Every action produces an authoritative lifecycle event with its full constraint evaluation, so "why did you not hedge?" is answerable.

**Missing for this:** L10 (risk/margin/capital), liquidity state from microstructure, operational health gating, portfolio exposure feedback into single-position decisions. **All downstream of the substrate.**

---

## K. Broker + Quant Desk boundary

| Domain | Broker-authoritative | Bujji-derived | Reconciliation |
|---|---|---|---|
| Account | ✅ | — | Poll |
| Capital / funds | ✅ | Allocation plan | Poll + drift alarm |
| Margin | ✅ | Requirement estimate | **Estimate vs actual must be measured** |
| Orders | ✅ | Order intent | `client_order_id` (15L) |
| Executions / fills | ✅ | — | `ExecutionReport` |
| Positions | ✅ | `PositionLifecycle` | **Must reconcile — divergence is an incident** |
| Portfolio | — | ✅ 15M | Derived from lifecycle + broker |
| Market data | ✅ | Candles, features, state | Raw preserved |
| Risk | — | ✅ L10 | — |
| P&L realized | ✅ | ✅ 15K (must agree) | **Divergence = incident** |
| P&L unrealized | ✅ | UNKNOWN in pure snapshot | Needs live price |
| Health | Connection | Feed/data health | Bujji owns the composite |

**Three reconciliation loops are mandatory before live money:** positions, realized P&L, margin estimate vs actual. **None exist for Stack B today.**

---

## L. Production-trust gates (no "code exists" counts)

| # | Gate | Evidence required |
|---|---|---|
| 1 | Feed correctness | Gate 1 measured envelope vs `map.json` |
| 2 | Raw persistence | Full session captured; raw payload recoverable |
| 3 | No silent drops | Drop counter = 0 at full universe; queue depth bounded |
| 4 | Candle correctness | Independent recompute from raw ticks matches stored |
| 5 | Multi-TF correctness | 5m from 1m == 5m from ticks; session boundaries verified |
| 6 | Feature correctness | Known-answer tests + version pinning |
| 7 | Uncertainty propagation | Injected GAP/STALE/UNKNOWN degrade correctly end-to-end |
| 8 | Replay determinism | Byte-identical state reconstruction from raw |
| 9 | Opportunity correctness | All states reachable on real data (**15P: done**) |
| 10 | Strategy correctness | Selection↔eligibility consistency on real data |
| 11 | Risk correctness | Parity vs Stack A on identical inputs |
| 12 | Execution correctness | Fill reconciliation vs broker |
| 13 | Lifecycle correctness | Real open→close→attribution, live session |
| 14 | Portfolio correctness | Multi-position real book |
| 15 | Recovery correctness | Kill mid-session; reconstruct identically |
| 16 | Memory correctness | Real populated corpus; query correctness |
| 17 | Operational health | Silent-stall detection proven under fault injection |
| 18 | **Kill switch** | Exists (**0 files today**), tested under load |
| 19 | End-to-end shadow | Unattended session, full chain, fabric data |
| 20 | Live-market validation | Multi-session stability |

**Passed today: 1 of 20** (#9, via 15P).

---

## M. Must NOT be built yet

| Item | Why it waits |
|---|---|
| More strategies | Selection can't be evaluated without outcome history |
| ML / predictive models | No training corpus; would learn from synthetic data |
| Strategy optimization | Optimizing on ~0 real outcomes = curve-fitting noise |
| Memory-driven live adaptation | Deliberate boundary; needs research→promotion gate |
| Portfolio optimization | Needs multi-position real book |
| New execution features | PaperBroker sufficient; live gated |
| UI beyond existing dashboard | No new information to display yet |
| Speculative indicators | Each needs definition, versioning, tests, uncertainty semantics |
| Parquet/DuckDB tier | Gate 1 must justify it |
| 4H bars | Session can't divide evenly |
| Watermark constant | Must be measured |
| Full-chain capture (1,851) | Gate 1 tests viability first |

**Common thread: every one of these consumes evidence that does not exist yet.**

---

## N. Final answer

# ARCHITECTURE LOCK: **READY**

All five decisions are locked. Canonical ownership is declared for every concept (four to build, rest assigned). Authoritative/derived/ephemeral classification is unambiguous. Time and uncertainty semantics are specified with composition rules, not just vocabulary. The MONITOR conflation is resolved architecturally without touching a threshold. Autonomy boundaries and their enforcement status are explicit. No unresolved architectural decision blocks implementation.

**Two conditions remain — operational, not architectural:**
- FYERS token refresh (external, yours)
- A non-expiry trading session for Gate 1

### Next implementation phase

**GATE 1 — Feed Capability Measurement** *(harness already built, safety-verified, not run)*

**In scope:**
- Rebuild universe at **±25 = 411 instruments** with real spot at run time
- Ramp 10 → 25 → 50 → 100 → 200 → 411 in lite and full mode
- Capture **raw wire payloads verbatim**, append-only
- Measure: field envelope (present + null), tick rate per instrument class, `exch_feed_time`→`receive_ts` distribution, **clock skew including negative latencies (recorded, never clamped)**, out-of-order arrival, queue depth, drops, silent symbols, reconnect/resubscribe, depth cost, bytes/message
- Fault injection: forced disconnect; 30-min hold for silent-stall detection

**Out of scope:** any 16C implementation, storage-tier selection, watermark choice, universe reduction.

**Exit criteria:** the measured evidence needed to fix the watermark bound, the storage tier, and full-chain viability.

---

**No production code written, deleted, merged, or refactored. Working tree uncommitted at `b148e39`.**

**Auditing stops here. Awaiting the next implementation instruction.**
