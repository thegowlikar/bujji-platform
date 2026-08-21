# Phase 19.7 — Market Phenomena Recognition Foundation

## Objective

Convert "market measurements" (the already-validated `MarketIntelligenceSnapshot`, Phase 19.3) into
"market situation understanding" — what kind of event/process is currently unfolding. Not strategy
selection, not prediction, not execution.

Audit performed first, per this phase's own explicit instruction — see
[docs/PHASE_19_7_MARKET_PHENOMENA_RECOGNITION_AUDIT.md](PHASE_19_7_MARKET_PHENOMENA_RECOGNITION_AUDIT.md).
**A real, mature, already-shipped phenomena classifier was found**: `bujji/msi_market_phenomena/`
(Series 103, "Market Phenomena Classifier"), operating on a structurally different input pipeline (the MSI
Production Intelligence family — PSI/MSSI/VSB/MDI — not `bujji.intelligence`'s six brains). Its code was
not imported (different, incompatible input pipeline — see audit doc), but its **vocabulary** was reused
exactly as the user instructed: `VOLATILITY_EXPANSION`/`VOLATILITY_COMPRESSION` are the identical string
values MPC already uses for the same real-world concept. Its design discipline (evidence-tuple shape,
NONE/LOW/MODERATE/HIGH confidence scale, honestly-disclosed-not-classifiable list, the
`_PHENOMENON_RULES` dict + consistency-assert technique) was reused directly.

## New package: `bujji/market_phenomena/`

- `models.py` — `MarketPhenomenonAssessment`, `MarketPhenomenaAssessment`, `PhenomenonEvidenceItem`,
  the 5-phenomenon taxonomy, the confidence scale
- `evidence.py` — verbatim evidence-item builders (`regime_evidence()`, `volatility_evidence()`,
  `liquidity_evidence()`, `event_evidence()`)
- `detectors.py` — one `_detect_<phenomenon>()` per phenomenon + `PHENOMENON_DETECTORS` dict, consistency-
  asserted against the taxonomy (mirrors MPC's own `assert set(_PHENOMENON_RULES) == set(taxonomy.ALL...)`)
- `engine.py` — `build_market_phenomena_assessment()`, the one entry point

## Phenomenon Taxonomy — only what real evidence supports

| Phenomenon | Primary evidence | Reading |
|---|---|---|
| `VOLATILITY_COMPRESSION` | `RegimeType.COMPRESSED` (RegimeBrain's own "range visibly narrowing" classification) + realized-vol trend vs `previous`, when available | `RegimeReading` |
| `VOLATILITY_EXPANSION` | `RegimeType.VOLATILE`, or `TRANSITIONING` with `compression_ratio >= 1.0` (RegimeBrain's own expansion branch) | `RegimeReading` |
| `LIQUIDITY_STRESS` | `SpreadTightness.WIDE` (LiquidityBrain's own "spread widened" classification) | `LiquidityReading` |
| `EVENT_RISK` | `ExpiryProximity` in `{EXPIRY_DAY, EXPIRY_EVE}` or `VixRegime.ELEVATED` — directly reuses EventBrain's own output, no new evidence computed | `EventReading` |
| `REGIME_TRANSITION` | `current.regime.regime != previous.regime.regime` — the entire rule, per this phase's own spec | `RegimeReading` (current + previous) |

Every detector returns `None` (never a fabricated low-confidence guess) when the snapshot genuinely lacks
the evidence — verified live: a flat, calm, non-expiry snapshot with no `previous` correctly reports 4 of 5
phenomena in `not_detected`, each with a real, non-empty `not_detected_reason`.

## Design rule honored — phenomena describe reality, never a strategy

No `BUY_BREAKOUT`/`SELL_PREMIUM`/`BUY_DIP`-shaped concept exists anywhere in this package — verified
structurally (AST identifier scan across all 5 files, plus a direct check that neither
`MarketPhenomenonAssessment` nor `MarketPhenomenaAssessment` carries an action/order/quantity/direction/
strike-shaped field).

## Evidence Discipline — every conclusion answers WHY

Matches the phase's own worked example exactly: every `PhenomenonEvidenceItem` carries `metric`, `value`
(copied verbatim from the real Reading — either its `.evidence` dict or, when the metric is a plain field
rather than a dict key, the Reading's own attribute directly, e.g. `LiquidityReading.combined_spread`),
and `source` (`"RegimeReading"`, `"VolatilityReading"`, `"LiquidityReading"`, `"EventReading"`). Verified
live: `LIQUIDITY_STRESS`'s `combined_spread` evidence item's value is byte-identical to the real
`LiquidityReading.combined_spread` field that produced it — no re-derivation, no rounding drift.

## Integration

```
DecisionIntelligenceSnapshot (Phase 19.6, optional reference — decision_intelligence_id)
        |
        v
MarketPhenomenaAssessment (this phase)
        |
        v
Future Strategy Intelligence
```

`build_market_phenomena_assessment()` never calls a brain's `.analyze(`, never instantiates a broker,
never queries a datastore itself — every input (`snapshot`, `previous_snapshot`, `decision_intelligence_id`)
is handed in by the caller, the same discipline every phase since 19.3 has followed. Verified structurally
by AST-level checks (no `.analyze` attribute access, no brain class instantiation, no import from an
`EventStore`/broker/`fyers`/sqlite/Reality-tier-shaped module).

## Historical Memory Integration — identity shared, not reinvented

`MarketPhenomenaAssessment.intelligence_snapshot_id` is the **same real value**
`MarketMemoryEntry.intelligence_snapshot_id` (Phase 19.5) already carries — verified live:
`build_market_memory_entry()` and `build_market_phenomena_assessment()`, called against the same
`MarketIntelligenceSnapshot`, produce entries keyed by the identical id. This is the join key a future
phase needs to store "market state + phenomena + outcome" together, without inventing a second identity
scheme. No such combined store was built this phase — recording phenomena into `MarketMemoryEntry` itself,
or building a query surface that returns "past phenomena + what happened after," is real, separate future
work once this foundation is accepted, not attempted here without a concrete calling need.

## Contradiction Handling

Two real, documented tensions are surfaced when they occur:

- `REGIME_TRANSITION` with `current.regime.confidence < 0.5` — a real transition (the label genuinely
  changed) reported at reduced confidence (`CONFIDENCE_LOW`) with the low-confidence reading itself as
  `contradicting_evidence`, rather than silently reporting `HIGH` confidence on a shaky signal.
- `VOLATILITY_COMPRESSION`/`VOLATILITY_EXPANSION` against a `previous` snapshot whose realized-vol trend
  runs the opposite direction of the current cycle's classification — surfaced as contradicting evidence,
  confidence never silently inflated to hide the tension.

`contradicting_evidence` is always a real, present tuple field on every `MarketPhenomenonAssessment` —
empty when nothing contradicts, populated when something genuinely does, never absent or `None`.

## Testing

`tests/test_market_phenomena.py`, 13 tests, proving all 8 required properties:

1. **Deterministic fingerprint** — same inputs → same `assessment_id`; `.fingerprint()` self-consistent
   on both the report and each individual `MarketPhenomenonAssessment`.
2. **Replay equality** — LIVE vs HISTORICAL_REPLAY produce the identical `assessment_id` and the identical
   set of detected phenomenon types.
3. **No strategy vocabulary** — 2 tests: AST identifier scan across all 5 files; no action/order/direction/
   strike-shaped field on either model.
4. **Evidence traceability** — 2 tests: every detected phenomenon carries ≥1 real, named, sourced evidence
   item; a spot-checked value is byte-identical to the real `Reading` field it was copied from.
5. **Contradiction handling** — 2 tests: `contradicting_evidence` is always a real tuple field (present,
   not absent) on `REGIME_TRANSITION`; the volatility-compression detector's contradiction path is
   exercised without asserting it always fires (the rule is conditional on real trend data being
   available — the test verifies the *shape* is honored either way, not that a specific one always fires).
6. **Historical memory compatibility** — `MarketMemoryEntry` and `MarketPhenomenaAssessment` built from
   the same real snapshot share the identical `intelligence_snapshot_id`.
7. **Insufficient evidence handling** — 2 tests: a flat/calm/non-expiry snapshot honestly reports 4 of 5
   phenomena as `not_detected` with a real reason string; `REGIME_TRANSITION` is structurally impossible
   (and correctly always absent) without a real `previous_snapshot`.
8. **No direct data-store access** — 2 tests: AST-level import scan (no `EventStore`/broker/`fyers`/
   sqlite/Reality-tier import); AST-level check confirming no `.analyze` call and no brain class
   instantiation anywhere in the package.

One test bug caught and fixed during this phase: an initial assertion compared a `combined_spread`
evidence value against `LiquidityReading.evidence["combined_spread"]` — but `combined_spread` is a real
FIELD on `LiquidityReading`, not a key in its `.evidence` dict (confirmed by re-reading `liquidity_brain.py`
from Phase 19.2.2). The evidence-item helper's own documented fallback (dict key first, then the Reading's
own attribute) was working correctly; the test's assertion was checking the wrong source. Fixed to compare
against the real field.

## Full regression

Baseline before this phase: 5,830 passed (post Phase 19.6). After Phase 19.7's additions: **5,843 passed,
0 failed** — exactly the 13 new tests. No existing file was modified, only five new self-contained modules
in `bujji/market_phenomena/` + one new test file.

## Explicitly NOT built this phase (per the phase's own scope)

- ❌ Entry signals, trade setups, options structures, direction prediction, execution rules.
- ❌ A combined memory store recording phenomena + outcome together (identity link proven, storage
  deferred to real future need).
- ❌ Any of the 11 phenomena MPC itself already honestly declared unclassifiable (`FALSE_BREAKOUT`,
  `PREMIUM_EXPANSION`, `DELTA_MIGRATION`, etc.) — this phase's own scope was explicitly narrower (5
  phenomena, only what `bujji.intelligence`'s six brains actually support).

## Final verdict

Bujji can now say, from real, cited evidence: *"Current environment shows volatility expansion after
compression, with elevated event uncertainty and reduced liquidity confidence"* — verified live against
exactly this combination of real constructed inputs (the phase's own worked smoke test detected
`VOLATILITY_EXPANSION`, `LIQUIDITY_STRESS`, `EVENT_RISK`, and `REGIME_TRANSITION` simultaneously, each with
real supporting evidence). That sentence is not a trade. It is market understanding — per the user's own
framing, market awareness, before Bujji ever asks which strategy deserves consideration.
