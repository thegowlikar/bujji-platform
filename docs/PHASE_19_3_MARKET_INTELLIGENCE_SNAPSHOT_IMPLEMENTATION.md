# Phase 19.3 — MarketIntelligenceSnapshot Implementation

## Objective

Create the canonical intelligence composition layer: `MarketRealitySnapshot` reference +
`IntelligenceContext` + the six already-validated brain outputs (Phase 19.2.2/19.2.3) →
one deterministic `MarketIntelligenceSnapshot`. "What Bujji believed about the market at one point in
time, and exactly why."

This is a **composition layer, not a new intelligence layer**. No new indicator, no trading rule, no
buy/sell signal, no brain modification, no execution logic, no direct `mil_next` code reuse, no
persistence (deferred to a future phase per the user's own recommendation).

## New package: `bujji/intelligence/market_intelligence_snapshot/`

- `models.py` — `MarketIntelligenceSnapshot`, `MarketThesis`, `ContradictionScore`,
  `IntelligenceEvidenceBundle`, `MarketPosture`
- `builder.py` — `build_market_intelligence_snapshot()`, the one entry point

## 1. `MarketIntelligenceSnapshot` model

### Identity

```python
intelligence_snapshot_id: str          # content-hash, stored field (DatasetArtifact.artifact_id precedent)
created_at: datetime                   # audit metadata — excluded from the fingerprint
as_of_time: datetime
execution_mode: str                    # stored, but excluded from the fingerprint (see below)
reality_fingerprint: Optional[str]     # MarketRealitySnapshot.fingerprint(), when known
dataset_artifact_id: Optional[str]     # DatasetArtifact.artifact_id, when known
reconstruction_version: str            # this package's own composition-logic version: "19.3.0"
```

`intelligence_snapshot_id` deliberately avoids the bare word `snapshot_id`, per Phase 19.1.1's confirmed
3-way collision risk with `mic_adapter`/`mil_next`/`trading_brain.ontology` (re-verified this phase — see
Testing). Unlike `MarketRealitySnapshot.fingerprint()` (a computed method only, never stored — Phase
18.3's design), `intelligence_snapshot_id` **is** a stored field, set once at build time — the same
precedent as `DatasetArtifact.artifact_id` (Phase 18.12), since a built snapshot is treated as a finished
artifact even though it is not yet persisted. `.fingerprint()` independently recomputes the same value
from the object's current content, so a hand-constructed object with mismatched fields can never silently
disagree with its own stored id.

### Brain outputs — preserved verbatim

```python
regime: RegimeReading
structure: StructureReading
liquidity: LiquidityReading
volatility: VolatilityReading
greeks: GreeksReading
event: EventReading
```

`build_market_intelligence_snapshot()` never calls a brain itself — the caller (exactly like `runner.py`
already does today) builds the six Readings first and hands them in. The builder only composes; the object
holds the exact same Reading instances it was given (`is` identity, not a copy — confirmed by test).

### Evidence — `IntelligenceEvidenceBundle`, the first real consumer of `evidence_lineage`

Phase 19.2.3's own audit found `evidence_lineage` existed on every Reading but nothing read it yet. This
bundle is that first consumer: it flattens all six brains' `evidence_lineage` entries into one tuple, each
`metric_name` prefixed with its owning brain (`"regime.efficiency_ratio"`, `"volatility.richness_ratio"`,
...) so same-named metrics across brains never collide. `source_references` and `observation_ids` are the
deduplicated union across all items — real values only, never fabricated when a brain has none.
`confidence` is `min()` across the six brains' own confidence, not an average — a chain is only as
trustworthy as its weakest link; averaging would silently launder a genuinely low-confidence domain into
a falsely reassuring blended number.

### Interpretation

**`MarketThesis`** — explicitly an *interpretation*, not a fact. Every field is mechanically composed from
the six brains' own already-existing `.reason` strings and real numeric fields:

- `primary_thesis` — `f"{regime.regime.value} regime ({regime.reason})"`, plus expiry proximity when
  known. **Never a directional (bullish/bearish) claim** — none of the six in-scope brains (Regime,
  Structure, Liquidity, Volatility, Greeks, Event) produce a directional signal, so there is nothing
  honest to report there. The user's own worked example ("Bullish trend continuation") describes a
  capability that does not exist yet in this scope — a Market Direction brain is not one of the six.
- `supporting_factors` — each `SUFFICIENT`-quality brain's own `.reason` string, verbatim.
- `contradictions` — each `INSUFFICIENT`-quality brain, verbatim ("we do not have consistent evidence
  here" — a genuine evidentiary gap, not an invented tension between two SUFFICIENT readings; see
  "Scoped simplification" below for why nothing more elaborate was attempted).
- `invalidation_conditions` — real numeric levels already on the Reading objects: `structure.resistance_strike`,
  `structure.support_strike`, and expiry proximity — never an invented threshold.
- `derived_from` — which brains actually contributed (`SUFFICIENT` only).

**`ContradictionScore`** — deliberately narrower than `mil_next`'s own version (Phase 19.1.2's finding: it
also carries `evidence_freshness_penalty` and `regime_consistency_penalty`). Only `overall` is computed
here, from one documented formula: `contradicting_domain_count / (supporting + contradicting)`. The other
two sub-scores are **not fabricated** — `regime_consistency_penalty` would require comparing against a
prior snapshot, which does not exist in a single-snapshot build (no multi-cycle history is threaded
through this phase); `evidence_freshness_penalty` has no honest input yet either. Left for a real future
phase once multi-snapshot history exists, not silently faked with a placeholder value.

**`MarketPosture`** — `TRENDING | RANGING | EXPANSION | COMPRESSION | UNCERTAIN | EVENT_RISK`. A direct,
documented, one-to-one mapping (`_REGIME_TO_POSTURE` in `builder.py`) from `RegimeType` to `MarketPosture`,
with `EVENT_RISK` overriding whenever `EventReading.expiry_proximity` is `EXPIRY_DAY`/`EXPIRY_EVE` or
`vix_regime` is `ELEVATED` — event risk takes priority over price-action shape regardless of what the
session otherwise looks like. This is a renaming/aggregation layer over already-existing enums, not a new
classification rule.

## 2. Identity rules — verified, not just designed

- `snapshot_id` (bare) does not appear as a field name anywhere on `MarketIntelligenceSnapshot` —
  confirmed by `test_intelligence_snapshot_id_does_not_collide_with_other_identity_names`.
- `reality_fingerprint`/`dataset_artifact_id` are separate, honestly-`Optional` reference fields, never
  merged into or confused with this object's own identity.
- Fingerprinting **reuses** `bujji.replay_engine.engine.fingerprint_state()` verbatim — the same mechanism
  `MarketRealitySnapshot.fingerprint()` already uses (Phase 18.3), no new hashing scheme invented.

### `execution_mode` is excluded from the content fingerprint — a real design correction made during this phase

The initial implementation included `execution_mode` in the fingerprint payload. The first run of the
required replay-equivalence test (`test_live_and_historical_replay_produce_identical_fingerprint_thesis_posture`)
caught this immediately: LIVE and HISTORICAL_REPLAY produced *different* fingerprints for byte-identical
market understanding, purely because the mode label differed. Per the phase's own explicit requirement
("Confirm: same fingerprint... across LIVE and HISTORICAL_REPLAY"), `execution_mode` describes *how* a
snapshot was produced, not *what* Bujji understood — so it was moved out of `fingerprint_payload()`,
staying a real stored field on the object (same treatment `created_at` already got), and the test then
passed. This is documented here rather than silently fixed, since it is exactly the kind of design gap
this phase's own testing requirement exists to catch.

## 3. Evidence aggregation

`IntelligenceEvidenceBundle` consumes `evidence_lineage` exactly as it already exists on each Reading —
`wrap_evidence()`'s output from Phase 19.2.2 is never re-derived or altered, only re-keyed with a brain
prefix and flattened. The original `evidence: dict[str, Any]` field on each Reading is untouched, per
Phase 19.2.2's own "wrap, never replace" discipline, now carried one layer further up.

## 4. Thesis layer — concepts adopted, code not reused

`MarketThesis`'s shape (`primary_thesis`, `supporting_factors`/`contradictions`, `invalidation_conditions`,
plus a `derived_from` lineage field standing in for `mil_next`'s `TradeThesis.supporting_evidence` —
except here it is genuinely typed to real brain names, not `mil_next`'s untyped strings, per Phase
19.1.2's own flagged gap) follows `mil_next`'s already-designed concepts. Zero lines of `mil_next` code
were imported or copied; `bujji/mil_next/` remains untouched, per the standing governance rule
(Phase 19.1.1/19.2.2). `competing_thesis` (a `mil_next` concept) was **not** built — a genuine second,
independently-reasoned thesis would require a second interpretation pass this phase does not have a basis
for; a single-thesis-with-honestly-listed-contradictions is what this phase's real inputs support.

## 5. Determinism tests

`tests/test_market_intelligence_snapshot.py`, 13 tests:

1. Same inputs → identical `intelligence_snapshot_id` and `.fingerprint()`.
2. `.fingerprint()` recomputation always matches the stored id.
3. `created_at` excluded from the fingerprint (two builds, different wall-clock `created_at`, same id).
4. Different `reality_snapshot_reference` → different fingerprint (proves the reference is genuinely
   included, not silently dropped).

## 6. Replay test

Built two snapshots from byte-identical brain Readings at `as_of_time = 2026-08-14T10:30:00+05:30`, one
with `execution_mode=LIVE`, one with `execution_mode=HISTORICAL_REPLAY`:

```
intelligence_snapshot_id : IDENTICAL
thesis                   : IDENTICAL
posture                  : IDENTICAL
as_of_time                : IDENTICAL (2026-08-14T10:30:00+05:30)
execution_mode             : differs (LIVE vs HISTORICAL_REPLAY) -- the only thing that differs
```

Confirmed by `test_live_and_historical_replay_produce_identical_fingerprint_thesis_posture` (initially
failed, fixed per the design correction in section 2 above, now passes).

Also verified: `EVENT_RISK` posture correctly overrides regime shape on an expiry-day + elevated-VIX
session (`test_posture_is_a_documented_mapping_not_a_new_signal`); evidence bundle confidence is the true
minimum across all six brains, not an average
(`test_evidence_bundle_confidence_is_the_weakest_link_not_an_average`); invalidation conditions use the
real numeric strikes from the actual `StructureReading` fed in, never an invented threshold
(`test_invalidation_conditions_use_real_numeric_levels_never_invented`); and neither
`PremiumReading`/`BehaviourReading` are referenced anywhere on the object
(`test_out_of_scope_brains_are_not_referenced`).

## Full regression

Baseline before this phase: 5,785 passed (post Phase 19.2.2/19.2.3). After Phase 19.3's additions:
**5,798 passed, 0 failed** — exactly the 13 new tests in `test_market_intelligence_snapshot.py`, on top
of the unchanged baseline. No existing test was affected, since this phase adds a new, self-contained
package and modifies no existing file.

## Constraints — verified honored

- ❌ No new indicators — every number in the snapshot already existed on a Reading before this phase.
- ❌ No trading rules, no buy/sell signals — `MarketThesis`/`MarketPosture` describe environment and
  evidentiary state, never an action.
- ❌ No brain modification — `bujji/intelligence/{regime,structure,liquidity,volatility,greeks,event}_brain.py`
  untouched this phase.
- ❌ No Reality layer modification — `MarketRealitySnapshot` untouched; this package only reads its
  `.fingerprint()` value via `IntelligenceContext.reality_snapshot_reference`, never the snapshot itself.
- ❌ No persistence — `MarketIntelligenceSnapshot` is constructed, tested, and discarded within a test/call
  scope. No store, no artifact registry entry, no `.jsonl` append. Per the user's own recommendation,
  storage is a Phase 19.4 decision, made only after this phase proves construction is sound.
- ❌ No execution logic — nothing here touches a broker, an order, or capital.

## Final verdict

**Ready for Phase 19.4 — Decision Intelligence layer.**

Bujji can now produce, in one deterministic object: "At 2026-08-14 10:30 IST, based on NIFTY structure,
volatility, liquidity, options behaviour, and event context, this is what I believed the market state
was — and here is exactly why" — reproducible byte-for-byte under replay, with every evidence item
traceable to its source, and honest about where it does not yet have enough to say more (no fabricated
freshness/regime-consistency sub-scores, no invented directional thesis, no competing thesis without a
real second reasoning pass to back it).
