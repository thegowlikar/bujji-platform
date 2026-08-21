# Phase 16F — mil_next Consumption Audit

**Audit only. No code written. Regression unchanged at 5,167 / 0 failed.**

---

## 0. THE HEADLINE — the bridge is far shorter than assumed

> **`mil_next` consumes TICKS, not candles.**

```python
@dataclass(frozen=True)
class MarketDataPoint:
    event_time: datetime
    price: float
    volume: Optional[float] = None
    symbol: str = "NIFTY"
    sequence_no: Optional[int] = None   # provider sequence number, when available
```

Five fields. Three optional. `mil_next.timeframe_fold` does its **own** bucketing from raw points.

**Consequence: the Market Reality Bridge does not require a CandleStore at all.**

```
PREVIOUSLY ASSUMED                    ACTUAL REQUIREMENT
tick → TickStore → CandleStore        tick → MarketDataPoint → mil_next
     → candles → mil_next                    (mil_next folds internally)
```

The candle layer (15Q) is valuable for indicators and research, but it is **not on the critical path** to reconnecting real market data to existing intelligence.

`sequence_no` is especially notable: `mil_next` already anticipates a provider sequence number. Gate 1's unknown #12 ("does FYERS supply one?") maps onto an existing optional field — if absent, it stays `None`, which the model already tolerates.

---

## 1. mil_next input contracts

### `MarketDataPoint` — the atomic input

| Field | Required | Notes |
|---|:--:|---|
| `event_time` | ✅ | **exchange event time**, not arrival |
| `price` | ✅ | the only mandatory value |
| `volume` | ❌ | `None` tolerated — **critical**, since FYERS INDEX supplies no volume |
| `symbol` | ❌ (defaults `"NIFTY"`) | |
| `sequence_no` | ❌ | already anticipated |

### `MarketDataInputs` — the assembled call

```python
session_id: str
points_by_symbol: Dict[str, Tuple[MarketDataPoint, ...]]
source_health: Dict[str, SourceHealth]
max_silence_ms: Dict[str, float]
```

Docstring: *"Callers assemble this from whatever real data source they have — this laboratory never fetches/ingests data itself."* The ingestion boundary is already drawn correctly.

### `SourceHealth`

```python
connected: bool
reconnected_since_last_observation: bool = False
```

`reconnected_since_last_observation` is a direct consumer of Gate 1's reconnect measurement.

### `build_snapshot()` — required arguments

```python
session_id, cadence_id, decision_cutoff_event_time,
market_data_inputs, timeframe_config, calendar_sources,
active_config_versions, has_open_positions=False, clock=_real_clock
```

Two hardcoded lookups the bridge must satisfy:
- `points_by_symbol["NIFTY"]`
- `source_health["underlying_tick"]` / `max_silence_ms["underlying_tick"]` (default `60_000.0`)

### `TimeframeConfig` — already parameterised

| Bucket | window | `max_lag_ms` |
|---|---|--:|
| `1m` | 60 s | 90,000 |
| `5m` | 300 s | 360,000 |
| `15m` | 900 s | 1,080,000 |
| `SESSION` | — | 3,600,000 |

`max_lag_ms` is a **per-timeframe staleness tolerance**, distinct from the tick lateness watermark Gate 1 must set. Both exist; only the latter is frozen.

### Failure behaviour — fails safe, does not raise

```python
failure_reason = is_mandatory_failure(dq)
if failure_reason is not None:
    return _minimal_snapshot(...)     # degraded snapshot, NOT an exception
```

Mandatory failures:
```
FRESHNESS_STALE                  → MANDATORY_SOURCE_STALE
COMPLETENESS_MAX_SILENCE_EXCEEDED
COMPLETENESS_GAP_DETECTED
FEED_DISAGREEMENT_BREACHED
```

**This is the behaviour a live system needs:** bad data yields a degraded, explicitly-labelled snapshot rather than a crash or a confident wrong answer.

---

## 2. Candle compatibility

**Direct feed: not applicable — and that is the finding.** `mil_next` never asks for OHLC.

| 15Q `Candle` field | mil_next need |
|---|---|
| `close` | → `price` (if candle-sourced points are ever used) |
| `open` / `high` / `low` | **unused** |
| `volume` | → `volume` (optional) |
| `window_start` / `window_end` | **unused** — mil_next computes its own windows |
| `tick_count` | **unused** (maps to `completeness_detail` conceptually) |
| `instrument` | → `symbol` |

**Adapter needed: none for the primary path.** A `Candle → MarketDataPoint` adapter would be *lossy and redundant* — it would discard H/L and then ask mil_next to re-bucket already-bucketed data.

**Correct sourcing:**

| Source | Path |
|---|---|
| Live ticks | tick → `MarketDataPoint` **directly** ✅ |
| Historical backfill | REST candle → `MarketDataPoint(event_time=window_end, price=close)`, flagged `RECONSTRUCTED` ⚠️ |

The historical path is a genuine downgrade (one point per bar instead of a real tick stream) and must be labelled as such, never mixed with `LIVE_TICK` provenance.

---

## 3. Data-quality integration

`DataQualityContext` already has a slot for nearly every Gate 1 measurement:

| Gate 1 measurement | Existing field | Status |
|---|---|---|
| `exch_feed_time` → `recv_ts` | `transport_latency_ms` + `transport_latency_status` | ✅ direct |
| clock skew | `clock_skew_detected` + `skew_ms` | ✅ direct |
| silent symbols | `completeness` via `max_silence_ms` | ✅ direct |
| gaps | `COMPLETENESS_GAP_DETECTED` | ✅ direct |
| staleness | `freshness` / `FRESHNESS_STALE` | ✅ direct |
| reconnect | `SourceHealth.reconnected_since_last_observation` | ✅ direct |
| synthetic timestamps | `event_time_synthetic` | ✅ direct |
| price outliers | `outlier_flag` | ✅ direct |
| multi-feed disagreement | `feed_disagreement_state` / `_bps` | ✅ (single feed today) |
| **dropped ticks (backpressure)** | — | ❌ **the one genuine gap** |
| **session boundaries** | via `calendar_sources` | ⚠️ needs verified calendar |

**Only one field is missing.** Backpressure drops are an ingestion-layer fact `mil_next` has no concept of — and per Gate 1's design, a drop is a first-class result. It would map naturally onto `completeness_detail`.

**Design quality worth noting:** raw jittery measurements (`skew_ms`, `transport_latency_ms`, `arrival_age_ms`, `feed_disagreement_bps`) are **excluded from the content hash**, while their boolean interpretations are included. That keeps snapshot hashes stable across runs without discarding the diagnostic — exactly the right call, and one I would not have made unprompted.

---

## 4. Indicator dependency audit

| Owner | Provides | Verdict |
|---|---|---|
| `market_timeseries.indicators` (15Q) | SMA, EMA, RSI, ATR, Bollinger, realised vol | **canonical for candle-based TA** |
| `signal/indicators.py` (Stack A) | `VwapTracker` (with `is_real`/`using_fallback`/`fallback_reason`), `OpeningRangeBuilder`, `PremiumVwapTracker` | **canonical for VWAP + opening range** — harvest semantics |
| `market/brain.py` (58 importers) | VWAP *relations* (above/below/at) | **consumer**, not calculator |
| `mil_next` | regime/direction folds — **not** classical indicators | **canonical for multi-TF regime** |
| `msi_*` | `compute_confidence` per domain — confidence, not price maths | **not indicators** |

### Duplication assessment

**Genuine duplication: only VWAP.** `signal.VwapTracker` computes it; `market/brain.py` consumes it; 15Q does not implement it at all. Two implementations do **not** exist — the apparent 26-file spread is one calculator plus many consumers.

**No duplicate SMA/EMA/RSI/ATR anywhere.** 15Q is uncontested.

`signal.VwapTracker` already carries the anti-fabrication discipline the epistemics model formalises (`is_real`, `using_fallback`, `fallback_reason` for zero-volume candles). **Harvest the semantics into the canonical feature engine; do not migrate the Stack A class.**

### Canonical owners going forward

| Concept | Owner |
|---|---|
| Candle-based TA (SMA/EMA/RSI/ATR/BB/vol) | `market_timeseries.indicators` |
| VWAP + opening range | new feature engine, semantics harvested from `signal` |
| Multi-TF regime / agreement | `mil_next` |
| Per-domain confidence | `msi_*` (unchanged) |
| Uncertainty composition | `epistemics` |

---

## 5. Minimum Viable Market Reality Bridge

```
        FYERS WebSocket (full mode, litemode=False)
                        │
                        ▼
        ┌───────────────────────────────────┐
        │ 1. FEED ADAPTER                   │  23 fields → normalized
        │    preserve raw payload verbatim  │  ← the only genuinely
        └───────────────┬───────────────────┘    new component
                        │
        ┌───────────────▼───────────────────┐
        │ 2. OBSERVATION STORE (append-only)│  authoritative evidence
        │    backend = GATE 1 DECISION      │
        └───────────────┬───────────────────┘
                        │
        ┌───────────────▼───────────────────┐
        │ 3. MarketDataPoint PROJECTION     │  5 fields — trivial
        │    + SourceHealth + max_silence   │
        └───────────────┬───────────────────┘
                        │
        ┌───────────────▼───────────────────┐
        │ 4. mil_next.build_snapshot()      │  ✅ EXISTS
        │    folds · DQ · calendar · no-look-ahead
        └───────────────┬───────────────────┘
                        │
        ┌───────────────▼───────────────────┐
        │ 5. THE SEAM  ComposedRegimeView   │  ⚠️ the one design question
        │              → DomainSignal[]     │
        └───────────────┬───────────────────┘
                        │
        ┌───────────────▼───────────────────┐
        │ 6. msi_decision_synthesis → …     │  ✅ EXISTS, UNCHANGED
        │    → lifecycle → P&L → memory     │
        └───────────────────────────────────┘
```

### Component ledger

| # | Component | Status | Gate 1? |
|---|---|---|:--:|
| 1 | Feed adapter | ❌ **build** | no |
| 2 | Observation store | ❌ **build** | ✅ backend only |
| 3 | `MarketDataPoint` projection | ❌ **build (trivial)** | no |
| 4 | `mil_next.build_snapshot` | ✅ **exists** | no |
| 5 | `ComposedRegimeView` → `DomainSignal` | ❌ **design** | no |
| 6 | MSI → memory | ✅ **exists** | no |

**Four components. Two are substantial (1, 2). One is trivial (3). One is a design question (5). Two already exist.**

### Prerequisites with no Gate 1 dependency

1. **Calendar verification** — `calendar_sources` feeds `load_calendar_view`; `market_calendar.py` self-declares as an unverified template with zero production consumers. Session boundaries and `SESSION` bucketing depend on it.
2. **The seam design (5)** — `ComposedRegimeView` gives `{timeframe: direction}` + `volatility_instability`; `synthesize()` expects `DomainSignal(domain_name, state, confidence, evidence_ids)`. The mapping is a genuine design decision, not mechanical, and it determines whether multi-TF conflict survives into the decision or is flattened. Phase 15P showed how consequential the `DomainSignal` vocabulary is.
3. **Backpressure → `completeness_detail`** — the one missing DQ field.

---

## 6. Verdict

**The bridge is roughly one third of what the Fabric plan assumed.**

| Previously planned | Actually required |
|---|---|
| TickStore + CandleStore + multi-TF engine + DQ engine + calendar + no-look-ahead | Feed adapter + observation store + a 5-field projection + one seam design |

`mil_next` supplies folding, cutoff discipline, no-look-ahead, data quality, calendar integration and cross-TF agreement — all already tested. The candle/indicator layer remains valuable for research and richer features, but it is **not on the critical path** to putting real market data in front of existing intelligence.

**Recommended next (no Gate 1 dependency):**
1. Verify `market_calendar.py` against the published NSE calendar
2. Design the `ComposedRegimeView` → `DomainSignal` seam
3. Build the full-fidelity feed adapter (23 fields, raw payload preserved)

Then Gate 1 → observation-store backend → projection → live snapshot.

---

**No code written. Frozen untouched: TickStore · storage backend · watermark value · ingestion topology · Stack A. `mil_next` diff: 0 lines. Regression 5,167 / 0 failed.**
