# Market Data Fabric — Architecture Audit

**Audit only. No code written, nothing modified.**

**Scope discipline:** the Architecture Lock froze TickStore/Fabric *decisions* behind Gate 1. Contract **shape** is auditable now precisely because it is backend-agnostic — but **backend, watermark value, ingestion topology and retention remain open** and are marked as such throughout.

---

## 0. CORRECTION — the fifth, and the most consequential

I have repeatedly reported these as **MISSING** across the 16A/16B/16C audits:

- multi-timeframe state
- conflict-preserving cross-TF representation
- `as_of` / decision-cutoff discipline
- data-quality context
- event calendar
- canonical content hashing

**All six exist, built and tested, in `bujji/mil_next/`.**

```
bujji/mil_next/
  timeframe_fold.py    multi-TF folding with as_of cutoff + ACTIVE no-look-ahead
  taxonomy.py          TIMEFRAME_1M/5M/15M/SESSION + TIMEFRAME_AGREEMENT
  models.py            15 models incl. ComposedRegimeView, DataQualityContext
  data_quality.py      freshness/completeness/outlier/feed-disagreement
  event_calendar.py    EventCalendarEntry / EventCalendarView
  canonical_hash.py    content hashing
  snapshot_builder.py  owns the decision cutoff watermark
  snapshot_journal.py  SQLite + WAL
```

Status: **0 internal importers, 8 test files** — built, tested, entirely disconnected.

`timeframe_fold.py`'s own docstring:

> *"No-look-ahead is enforced ACTIVELY here (filter + defensive assert) and is additionally proven BEHAVIORALLY by the test suite: a fold over an input set including entries with event_time > cutoff must produce a byte-identical RegimeAssessment to the same fold over that input set with those entries physically removed."*

That is a **stronger** no-look-ahead guarantee than the one I built in Phase 16C, and it predates it.

`DataQualityContext` is more sophisticated than my proposed model — it already separates audit-only fields from content-hashed ones:

```python
feed_disagreement_bps  # raw, audit-only, EXCLUDED from content_hash
skew_ms                # raw, audit-only, EXCLUDED from content_hash
arrival_age_ms         # raw, audit-only, EXCLUDED from content_hash
transport_latency_ms   # raw, audit-only, EXCLUDED from content_hash
clock_skew_detected    # boolean → INCLUDED
event_time_synthetic   # boolean → INCLUDED
```

Excluding jittery raw measurements from the hash while keeping their boolean interpretations is exactly right — it makes the hash stable across runs without discarding the diagnostic.

**Why I kept missing it:** `mil_next` has zero internal importers, so every import-graph query returned nothing. Absence in an import graph is not absence in a repository. This is the fifth time that has bitten this audit.

---

## 1. Two watermarks — a distinction that matters

The Lock froze "watermark." There are **two different things** under that word:

| | Purpose | Status |
|---|---|---|
| **Decision cutoff** (`as_of`) | "do not use data after T" | ✅ **EXISTS** — `mil_next.snapshot_builder`, actively enforced, behaviourally proven |
| **Tick lateness bound** | "how long to wait for late ticks before sealing a bar" | ⛔ **GATE 1** — needs `LATENESS_MAGNITUDE_ms` |

**The Lock applies only to the second.** The first is already solved and should be reused, not rebuilt. Conflating them would have led me to rebuild a proven mechanism.

---

## 2. What exists — reuse inventory

| Fabric layer | Existing | Status |
|---|---|---|
| Feed adapter | `broker/fyers_ws` | ⚠️ discards 21 of 23 fields |
| Canonical observation | `market_observation` (31 importers) | ✅ canonical |
| Tick aggregation | `live_observation` (late ticks, no fabrication) | ✅ reuse |
| **Raw tick persistence** | — | ❌ **the genuine gap** |
| Candle store | `market_timeseries` (15Q) | ✅ rework behind protocol |
| **Multi-TF fold** | **`mil_next.timeframe_fold`** | ✅ **reuse** |
| **Multi-TF state** | **`mil_next.ComposedRegimeView`** | ✅ **reuse** |
| **Cross-TF agreement** | **`TIMEFRAME_AGREEMENT_{UNANIMOUS,MAJORITY,SPLIT,INSUFFICIENT_EVIDENCE}`** | ✅ **reuse** |
| **Data quality** | **`mil_next.data_quality`** + `execution_reality` | ✅ **reuse** |
| **Event calendar** | **`mil_next.event_calendar`** | ✅ reuse |
| Trading calendar | `market_calendar.py` | ⚠️ unverified template, 0 prod consumers |
| Content hashing | `replay_engine.fingerprint_state` + `mil_next.canonical_hash` | ✅ reuse (6 impls exist) |
| Indicators | `market_timeseries.indicators` (6) + `signal/indicators.py` (VWAP, opening range, premium VWAP — Stack A) | ⚠️ partial |
| VWAP | `market/brain.py` (58 importers), `signal/vwap_audit.py` | ✅ reuse |
| Uncertainty / lineage / identity | `epistemics` (16C/16D) | ✅ reuse |
| Replay | `replay_engine` (canonical) | ✅ reuse |

**Genuinely missing: raw tick persistence, the full timeframe ladder (30m/1H/D/W/M), ~34 indicators, IV surface, OI series.**

---

## 3. TickStore / CandleStore contracts

**Backend-agnostic by construction.** Naming a backend is a Gate-1 decision and is *not* made here.

```
TickStore  (Protocol)
    append(ticks: Iterable[Tick]) -> AppendResult
        # AppendResult(written, deduped, dropped)
        # dropped > 0 MUST raise or alarm — never silent

    range(instrument_id, t0, t1, *, as_of) -> Iterable[Tick]
    latest(instrument_id, *, as_of) -> Optional[Tick]
    gaps(instrument_id, t0, t1) -> Iterable[Gap]

CandleStore  (Protocol)
    write(candle) / write_many(candles) -> int
    recent(instrument_id, timeframe, count, *, as_of) -> List[Candle]
    range(instrument_id, timeframe, t0, t1, *, as_of) -> List[Candle]
    forming(instrument_id, timeframe) -> Optional[FormingCandle]

SnapshotStore · UniverseStore · MarketEventStore   — analogous, all as_of-scoped
```

### Four binding rules

1. **`as_of` is a required keyword-only argument on every read.** Omitting it is a `TypeError`, not a review miss. This is the single most important design decision in the contract, and it is only enforceable if it exists from the first line.
2. **Intelligence imports protocols only** — never `sqlite3`, `duckdb`, `pyarrow`. A safety test enforces it.
3. **Every row carries `source ∈ {LIVE_TICK, BROKER_HISTORICAL, EXCHANGE_HISTORICAL, DERIVED, RECONSTRUCTED, SYNTHETIC_TEST}`**, with a test asserting `SYNTHETIC_TEST` never reaches a production store.
4. **Drops are never silent.** `AppendResult.dropped` is a first-class return value.

### Deliberately NOT decided (Gate 1)

Backend choice · partitioning · retention · archival tier · index strategy · batch size · single vs multi-process.

---

## 4. Multi-timeframe model

**Extend `mil_next`'s ladder rather than replacing it.**

Existing: `1m · 5m · 15m · SESSION`
Target adds: `30m · 1H · Daily · Weekly · Monthly`

| TF | Derived from | Alignment |
|---|---|---|
| 1m | **ticks** (authoritative) | clock |
| 5m / 15m / 30m | 1m | clock; divides evenly |
| 1H | 1m | session-anchored 09:15–10:15, **not** 09:00 |
| Daily | 1m | NSE session 09:15–15:30 IST |
| Weekly / Monthly | Daily | ISO week / calendar month |
| **4H** | — | **excluded** — 6h15m session cannot yield equal 4H bars |

**Derive everything from 1m, never chain.** Chaining accumulates rounding and makes `derived_from` ambiguous.

`mil_next`'s `SESSION` timeframe is a genuine insight worth keeping: a session bar is not "Daily" — it is the exchange's own trading period, which is the correct unit for gap/holiday reasoning.

**Cross-TF state:** reuse `ComposedRegimeView` + `TIMEFRAME_AGREEMENT`. Conflict is represented, never averaged — `SPLIT` and `INSUFFICIENT_EVIDENCE` are distinct, and that distinction is exactly what a naive scalar would destroy.

**Prerequisite:** `market_calendar.py` must be verified against the published NSE calendar before Daily/Weekly/Monthly are trustworthy. It currently self-declares as an unverified template with zero production consumers.

---

## 5. Indicator pipeline

```
Candle[]  +  DecisionContext(as_of)
    │
    ▼
FeatureSpec        name · definition · params · min_history
    │              · criticality map · timeframe · range_dependent?
    ▼
FeatureEngine      pure functions only
    │
    ▼
Feature            value | None
                   + Lineage      (calc_version, source_candle_range, as_of)
                   + Uncertainty  (state, confidence, limiting_factor)
```

### Rules

- **Insufficient history → no value.** Not a low-confidence value. `INSUFFICIENT_HISTORY`, which self-heals.
- **`range_dependent=True`** (ATR, Bollinger, realised vol) → a GAP in the window **breaks the definition**, so the output is `UNKNOWN`, not merely weakened. Already implemented in `epistemics.compose`.
- **`calc_version` = content hash** of (definition, params) — `epistemics.identity.resolve_calculation_identity`.
- **Criticality declared per feature.** Everything-critical collapses to UNKNOWN constantly; nothing-critical launders uncertainty.

### Inventory

| | Count |
|---|--:|
| Exist (`market_timeseries`) | 6 — SMA, EMA, RSI, ATR, Bollinger, realised vol |
| Exist (`signal`, Stack A) | VWAP (with equal-weight fallback + `is_real`), opening range, premium VWAP |
| Exist (`market/brain.py`) | VWAP relations (above/below/at) |
| Missing | ~34 — ADX/DI, Supertrend, MACD, ROC, momentum, volume-profile, swing structure, IV surface, skew, term structure, OI analytics, PCR, gamma concentration |

`signal/indicators.py`'s VWAP already carries `is_real` / `using_fallback` / `fallback_reason` — the same anti-fabrication discipline the epistemics model formalises. **Harvest the semantics; do not migrate Stack A wholesale.**

---

## 6. Integration boundaries

```
FABRIC (L0–L5)                        │  EXISTING (L6–L8) — unchanged
──────────────────────────────────────┼───────────────────────────────
Feed adapter → Observation            │
TickStore  ← the one real gap         │
CandleStore                           │
FeatureEngine ──┐                     │
DerivativesEngine ─┤                  │
mil_next fold ─────┴─► ComposedRegimeView
                          │
                          ▼
                   ═══ THE SEAM ═══
                          │
                          ▼           │  msi_consensus
                                      │  msi_decision_synthesis   ← UNCHANGED (15P-verified)
                                      │  msi_strategy_eligibility
                                      │  msi_trade_intent
                                      │  position_lifecycle · P&L · portfolio
                                      │  attribution · memory
```

**The seam is exactly one substitution:** `msi_decision_synthesis.synthesize()` today receives 5 single-TF `DomainSignal`s. It should receive multi-TF state instead. **`synthesize()` itself does not change** — Phase 15P proved every documented state is reachable and the engine correct.

**Preserved:** no outcome→decision feedback (AST-enforced) · PaperBroker as the only execution surface · `pnl.py` as sole economic authority · `replay_engine` as canonical replay.

**Still absent downstream:** risk/capital (L10) exists only in Stack A.

---

## 7. Revised gap list

| Gap | Severity | Blocked by |
|---|---|---|
| Raw tick persistence | **critical** | Gate 1 |
| Full-fidelity feed adapter | **critical** | — (buildable now) |
| Backpressure / drop accounting | **critical** | Gate 1 (sizing) |
| Timeframe ladder 30m→Monthly | high | calendar verification |
| **`mil_next` disconnected** | **high** | — (integration, not construction) |
| Calendar verification | high | — (buildable now) |
| ~34 indicators | medium | fabric |
| IV surface / OI series | medium | fabric |
| Risk layer (L10) | high | Stack A harvest |

**The Fabric is materially smaller than previously assessed.** Roughly half its upper structure already exists in `mil_next`, disconnected.

---

## 8. Recommended sequence

```
GATE 1 (blocked: token + non-expiry session)
   ↓
16F  mil_next integration audit — can it consume real candles?   ← NO Gate 1 dependency
     calendar verification against published NSE calendar         ← NO Gate 1 dependency
   ↓
16G  full-fidelity feed adapter (23 fields → Observation)
   ↓
GATE 2 capture integrity
   ↓
16H  TickStore behind protocol (backend chosen FROM Gate 1 evidence)
   ↓
16I  timeframe ladder extension, reusing mil_next fold
   ↓
16J  feature engine (lineage + uncertainty attached from line one)
   ↓
16K  the seam: multi-TF state → msi_decision_synthesis
```

**Two items need no Gate 1 evidence and should go first:** `mil_next` integration assessment, and calendar verification.

---

## 9. Verdict

**The Fabric needs less construction than assumed.** The honest summary:

- **Genuinely missing:** raw tick persistence, full-fidelity ingestion, the upper timeframe ladder, ~34 indicators, derivatives analytics.
- **Exists but disconnected:** `mil_next`'s entire multi-TF + data-quality + calendar + no-look-ahead layer.
- **Exists and connected:** observation model, tick aggregation, replay, lifecycle→memory, epistemics.

The recurring pattern holds one more time: **the dominant problem is integration debt, not missing capability** — and I under-detected it five times because zero-import packages are invisible to import-graph queries.

**Methodological correction for future phases: a capability search must inspect the filesystem, not only the import graph.** Every one of my five absence errors would have been caught by listing package contents before concluding.

---

**No code written. Frozen items untouched: TickStore · Fabric · watermark value · storage backend · ingestion topology · Stack A. Working tree uncommitted at `b148e39`. Regression unchanged at 5,167.**
