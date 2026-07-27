# Market Observation Contract (MOC v1)
## Engineering Series 73A — Implementation

**Status:** Implemented. This document describes `bujji/market_observation/`, the first real code built against `docs/MOF_V1_FOUNDATION.md` (Series 72, architecture-only) and referenced by `docs/MSI_V1_FOUNDATION.md` (Series 71). No changes were made to `mic_v2`, `bujji.mic_replay`, `bujji.production_runtime`, `bujji.trading_brain`, Runtime, or Qualification — MOC v1 is a new, isolated package.

---

## 1. Philosophy

MOF v1 (Series 72) established a four-layer discipline that MSI v1 (Series 71) and this project's Trading Brain both depend on:

```
Observation  →  Derived Evidence  →  Intelligence  →  Decision
   (MOC)          (MSI / MIC v2)      (MSI / MIC v2)    (Trading Brain)
```

An **Observation** is a timestamped, source-attributed record of something the market did or currently is — captured with zero interpretation. A candle's OHLC values are an Observation. An option strike's open interest at a snapshot time is an Observation. India VIX's closing value is an Observation. The dividing line from Derived Evidence: if producing the value requires looking at more than one raw data point, or requires any rule beyond "record what the source reported," it is no longer an Observation. A swing high is not an Observation — it requires comparing candles and applying a confirmation rule.

MOC v1 implements **only** the Observation layer. It never computes a swing, never classifies a regime, never produces a trading signal, and never executes anything. Every model, function, and test in this package exists to answer exactly one question: "what was observed, by which source, at what time, with what quality, and can it be replayed identically." Nothing here answers "what does it mean" — that is MSI's and MIC v2's job, one layer up.

This mirrors the discipline that made MIC v2 auditable (Series 66–70): no layer may be skipped, every object carries provenance, nothing depends on wall-clock time or unseeded randomness, and missing data must surface as `None`/absent, never fabricated.

---

## 2. The Contract — models

All models live in `bujji/market_observation/models.py` as `@dataclass(frozen=True)` records with no logic. Construction is exclusively the job of `engine.py`.

### `ObservationIdentity`
*What was observed, by whom, at what time.* Fields: `observation_id`, `observation_type` (one of 17 domains, `taxonomy.ALL_OBSERVATION_TYPES`), `instrument`, `exchange`, `segment`, `timestamp`, `resolution`, `source`, `schema_version`.

`observation_id` is a deterministic `hashlib.md5` hash over the identity fields (excluding the id itself) plus the `ObservationValue` — never `uuid4()`. Two Observations with identical identity-seed fields and identical value always produce the identical `observation_id`.

### `ObservationQualityMetadata`
*A disclosed, evidenced read of how trustworthy this Observation is — metadata only, never read by Identity/Value construction or equality logic.* Fields: `completeness` (0.0–1.0), `freshness` (seconds, ≥0), `confidence` (`Optional[float]` — `None` when the source publishes no confidence signal; never fabricated as 0.0 or 1.0), `missing_fields` (tuple), `validation_status`, `source_quality`.

### `ObservationProvenance`
*Where this Observation came from and how it got here*, mirroring MIC v2's existing `provenance` field shape rather than inventing a new one. Fields: `originating_source`, `acquisition_timestamp`, `normalization_timestamp`, `origin` (`LIVE` / `REPLAY` / `HISTORICAL_RECONSTRUCTION`), `version`, `transformation_history` (tuple, always present — empty tuple, never `None`, when no transformation occurred).

### `ObservationValue`
*Domain-neutral.* A closed set of variants (`taxonomy.ALL_VALUE_KINDS`: `SCALAR`, `OHLC`, `MAPPING`, `TEXT`), discriminated by `value_kind`, carrying a generic `payload`. MOC v1 deliberately does **not** define `PriceValue`/`OIValue`/`VIXValue` per-domain subclasses — every one of the 17 `ObservationType`s rides inside this one shape, so Identity/Series/Engine logic never branches on domain.

### `Observation`
*The canonical unit* — MOF's `ObservationSnapshot`, realized as one frozen record composing `identity` + `quality` + `provenance` + `value`.

### `SeriesGap`
*An explicit marker of a missing or disordered interval.* Fields: `after_timestamp`, `before_timestamp`, `reason` (`MISSING_INTERVAL` or `OUT_OF_ORDER_SKIPPED`). Per this project's "no silent data gaps" discipline, a gap is always a first-class recorded object, never a silent absence in the underlying tuple.

### `ObservationSeries`
*MOF's `ObservationSeries`* — an ordered, immutable, tuple-backed sequence of `Observation`s sharing `observation_type` + `instrument`, at a defined `resolution`, spanning an explicit `[window_start, window_end]`. Storage only — no analytics, no derived metrics (that is MSI's job).

### `ValidationResult`
The outcome of structural validation (`engine.validate_observation` / `engine.validate_series_ordering`). Fields: `is_valid`, `status`, `reasons` (always complete — every failing check contributes, never short-circuited on the first failure).

---

## 3. Design decision: does quality metadata affect `observation_id`?

**No.** `observation_id` is derived only from `ObservationIdentity`'s own fields plus `ObservationValue` — the "fact": what was observed, by whom, at what time, and what value it carried. It is never derived from `ObservationQualityMetadata`.

Two Observations that agree on identity and value but differ only in how their quality was later assessed (e.g. a completeness re-check, a freshness re-read at consumption time) are the *same* observation for identity/equality-of-identity purposes. Quality is a read *about* the fact, not part of the fact itself — this mirrors MOF Deliverable 3's own distinction between `ObservationQuality` ("MOF's own disclosed, evidenced assessment") and the Observation being assessed.

This keeps `observation_id` stable across a quality re-assessment, which downstream Derived Evidence consumers depend on for deduplication and provenance tracing. It is verified by `tests/test_market_observation_contract.py::TestQualityMetadataIndependence`.

---

## 4. Lifecycle (generic, no domain code)

```
Raw Feed
   ↓  (already normalized by the caller — MOC never parses a raw feed)
engine.build_observation(...)
   ↓
Observation  (source-attributed, timestamped, domain-tagged, uninterpreted)
   ↓
engine.validate_observation(...)  — structural checks only:
   identity fields present/well-formed, timestamp parseable,
   source non-empty, schema_version compatible, quality metadata
   internally consistent (confidence ∈ [0,1] if present, completeness
   ∈ [0,1]). NEVER validates the market value itself.
   ↓
engine.append_observation(series, observation)
   ↓
ObservationSeries  (ordered, gap-aware, replayable)
   ↓
serialization.series_fingerprint(series)  — deterministic content hash
   ↓
journal.MarketObservationJournal.record_series(series)  — append-only,
   replay-safe audit trail
   ↓
(MSI's Derived Evidence layer begins here — out of MOC's scope)
```

Two stages deserve emphasis, per MOF Deliverable 4:

- **Validation never silently passes through incomplete data.** Every structural check runs unconditionally; `ValidationResult.reasons` is always the complete set of failures, never truncated at the first one.
- **Out-of-order append is never silently absorbed.** `engine.append_observation` still appends the Observation (a series is a record of what was received, in receipt order — not a value-sorted index) but immediately records an `OUT_OF_ORDER_SKIPPED` gap marker, making the disorder explicit and queryable. Callers that require strict monotonic order call `engine.validate_series_ordering` afterward and handle the result themselves.

---

## 5. Extension rules

Future observation domains (Option OI series, Futures series, IV term structure, Market Breadth, etc., per MOF Deliverable 2's remaining gaps) must:

1. **Wrap or reference `Observation`/`ObservationSeries`, never subclass or mutate them.** A new domain adds a new `observation_type` constant to `taxonomy.py` and a new `value_kind` payload shape if genuinely needed — it does not add a new dataclass hierarchy.
2. **Never introduce a per-domain Identity/Value type.** `ObservationValue`'s `value_kind` + `payload` discriminated-union shape is deliberately the single shape every domain uses. If a domain's payload doesn't fit `SCALAR`/`OHLC`/`MAPPING`/`TEXT`, that is a signal to add one more closed `value_kind`, reviewed deliberately — never an ad hoc subclass.
3. **Never let Derived Evidence/Intelligence code construct an `Observation` directly.** Only `engine.build_observation` (via `runner.build_observation`) may mint an `observation_id`. Everything upstream of MOC (MSI, MIC v2) consumes already-built `Observation`/`ObservationSeries` objects, never raw feeds.
4. **Never skip validation.** Any new ingestion path must call `runner.validate_observation` before the Observation is considered part of a series a consumer can rely on.
5. **Quality and Provenance are metadata, not identity.** New domains must not fold quality/provenance fields into whatever makes two Observations "the same fact" — see Section 3.

---

## 6. Illustrative usage (pseudo-code, non-runnable, no real domain wiring)

```python
from bujji.market_observation import runner, engine, taxonomy, serialization

# A caller (a future connector, e.g. a Bhavcopy OI extractor) has already
# parsed and normalized one raw feed row into plain values:
observation = runner.build_observation(
    observation_type=taxonomy.TYPE_OPTION_OPEN_INTEREST,
    instrument="NIFTY-24000-CE-2026-07-30",
    exchange="NSE",
    segment="FO",
    timestamp="2026-07-24T15:30:00+00:00",
    resolution=taxonomy.RESOLUTION_DAILY,
    source="NSE_BHAVCOPY_FO",
    value_kind=taxonomy.VALUE_KIND_SCALAR,
    payload=1_234_500,                     # open interest, already normalized
    completeness=1.0,
    freshness=0.0,
    confidence=None,                       # Bhavcopy publishes no confidence signal
    missing_fields=(),
    validation_status=taxonomy.VALIDATION_VALID,
    source_quality=taxonomy.SOURCE_QUALITY_HIGH,
    originating_source="NSE_BHAVCOPY_FO",
    acquisition_timestamp="2026-07-24T18:05:00+00:00",
    normalization_timestamp="2026-07-24T18:05:02+00:00",
    origin=taxonomy.ORIGIN_HISTORICAL_RECONSTRUCTION,
)

result = runner.validate_observation(observation)
assert result.is_valid

oi_series = engine.new_series(taxonomy.TYPE_OPTION_OPEN_INTEREST, observation.identity.instrument, taxonomy.RESOLUTION_DAILY)
oi_series = runner.append_to_series(oi_series, observation)

fingerprint = serialization.series_fingerprint(oi_series)
# fingerprint is stable across replay runs given the same inputs --
# this is what MSI Brain 4 (Options Market Structure) will eventually
# consume as its ObservationSeries input for OI Migration reasoning.
```

---

## 7. Relationship to MOF and MSI

| MOF v1 (Series 72) concept | MOC v1 realization |
|---|---|
| `ObservationSnapshot` | `models.Observation` |
| `ObservationSeries` | `models.ObservationSeries` |
| `ObservationSource` | `ObservationIdentity.source` / `ObservationProvenance.originating_source` |
| `ObservationQuality` | `ObservationQualityMetadata.source_quality` / `.validation_status` |
| `ObservationFreshness` | `ObservationQualityMetadata.freshness` |
| `ObservationConfidence` | `ObservationQualityMetadata.confidence` (Optional, never fabricated) |
| `ObservationCompleteness` | `ObservationQualityMetadata.completeness` + `.missing_fields` |
| `ObservationWindow` | `ObservationSeries.window_start` / `.window_end` |
| `ObservationResolution` | `ObservationIdentity.resolution` / `ObservationSeries.resolution` (`taxonomy.ALL_RESOLUTIONS`) |
| `ObservationVersion` | `ObservationIdentity.schema_version` / `ObservationProvenance.version` |
| Deliverable 4's Validation stage | `engine.validate_observation` / `engine.validate_series_ordering` |
| Deliverable 4's Normalization stage | The caller's responsibility, upstream of `engine.build_observation` — MOC never parses a raw feed |
| Deliverable 6's "reason from change, not snapshots" | `ObservationSeries` + `engine.append_observation` + `engine.detect_gaps`, gap-aware by construction |
| Deliverable 8's Replay/Determinism requirements | `serialization.series_fingerprint` (deterministic `hashlib.md5` over sorted-key JSON), no `uuid4()`/`datetime.now()` anywhere in the package (verified by `TestIsolation::test_no_uuid4_used_anywhere_in_package`) |

| MSI v1 (Series 71) concept | Relationship |
|---|---|
| Deliverable 4's Evidence Graph, "Raw Observations" layer | This is exactly `ObservationSeries` as produced by MOC — MSI's Derived Evidence layer is required to cite specific Observations from a MOC-produced series, never skip to Reasoning Objects directly. |
| Deliverable 6's Replay requirement | MOC's `journal.MarketObservationJournal` and `serialization.series_fingerprint` are the concrete mechanisms MSI's replay-parity requirement depends on at the layer below it. |
| Deliverable 6's Qualification integration | Not yet wired — MOC's fields are structured to be consumable as domain-prefixed fields in the existing `QualificationRecord`/`sessions[]` schema per MOF Deliverable 8, but that wiring is explicitly out of scope for Series 73A. |

MOC's scope ends exactly where MOF says it should: at `ObservationSnapshot`/`ObservationSeries`. It does not decide what a swing is, what OI migration means, or what strategy to pick — those remain MSI's and Trading Brain's responsibility, one and two layers up.
