# Phase 19.4 — Decision Context Foundation

## Objective

Build the layer between `MarketIntelligenceSnapshot` (Phase 19.3) and future strategy engines. Answers
"what is this environment suitable for," never "what trade should I place." No orders, no fills, no
quantities, no positions, no entry/exit signals, no strategy selection, no position sizing.

## New package: `bujji/decision_context/`

- `models.py` — `StrategyCompatibilityAssessment`, `MarketStateTransition`, `TransitionType`, `DecisionContext`
- `compatibility_engine.py` — `assess_strategy_compatibility()`
- `transition.py` — `detect_transition()`
- `builder.py` — `build_decision_context()`, the one entry point

Deliberately a **separate top-level package**, not nested inside `bujji/intelligence/` — this layer
answers a different question than the brains do, and the user's own framing ("Market Interpretation and
Decision Context," not yet "Decision Intelligence," since a decision implies action this phase does not
take) is reflected structurally.

## 1. Strategy Compatibility Engine

Reuses `msi_strategy_selection_foundation.taxonomy.ALL_STRATEGY_FAMILIES` verbatim — the same 13 real
family names already established in that package (`LONG_DIRECTIONAL`, `SHORT_DIRECTIONAL`,
`NEUTRAL_PREMIUM_SELLING`, `NEUTRAL_PREMIUM_BUYING`, `VOLATILITY_EXPANSION`, `VOLATILITY_COMPRESSION`,
`CALENDAR`, `RATIO`, `BUTTERFLY`, `IRON_CONDOR`, `IRON_FLY`, `COVERED`, `SYNTHETIC`), never invented new
ones.

### Every rule is traceable to a brain's own already-documented meaning

`assess_strategy_compatibility()` only assesses the families it has a genuine, pre-existing basis for:

- **Premium-selling-shaped families** (`NEUTRAL_PREMIUM_SELLING`, `IRON_CONDOR`, `IRON_FLY`): compatible
  when `VolatilityReading.richness == IV_RICH` and liquidity is not `WIDE`. This is not a new rule —
  `volatility_brain.py`'s own module docstring already states: *"Richness = IV / realized vol... how much
  more [is priced in] is exactly what determines whether selling premium has a statistical edge today."*
  This engine reuses that already-documented meaning; it did not invent it.
- **Premium-buying-shaped families** (`NEUTRAL_PREMIUM_BUYING`, `VOLATILITY_EXPANSION`): the inverse —
  compatible when `richness == IV_CHEAP`, same documented basis.
- **`VOLATILITY_COMPRESSION`**: compatible when `RegimeReading.regime == COMPRESSED`, directly reusing
  `RegimeBrain`'s own classification.
- **Liquidity as a universal gate**: `SpreadTightness.WIDE` blocks entry/exit feasibility regardless of
  otherwise-favorable volatility, added to `blocking_evidence`.

### Everything else is honestly `UNASSESSED`, never forced

`LONG_DIRECTIONAL`, `SHORT_DIRECTIONAL`, `RATIO`, `BUTTERFLY`, `CALENDAR`, `COVERED`, `SYNTHETIC` are
**always** reported in `unassessed_strategy_families`. A `MarketIntelligenceSnapshot` genuinely carries no
direction signal (none of the six in-scope brains produce one — confirmed already in Phase 19.3's own
report), no volatility term structure, no futures positioning, and no underlying-holdings data. Forcing a
SUITABLE/UNSUITABLE verdict on any of these would be fabrication. This matches
`msi_strategy_selection_foundation`'s own established `INSUFFICIENT_EVIDENCE` discipline — reused as a
precedent, not reinvented — where `CALENDAR` was already known to require `DOMAIN_VOLATILITY_TERM_STRUCTURE`,
"still genuinely unavailable everywhere."

`StrategyCompatibilityAssessment` therefore always partitions all 13 families into exactly three
disjoint sets: `compatible_strategy_families`, `incompatible_strategy_families`, `unassessed_strategy_families`.

## 2. Market State Transition Model

`detect_transition(previous, current)` compares two already-built `MarketIntelligenceSnapshot`s' own
`posture` field (Phase 19.3) via one documented, exhaustive mapping table:

```
COMPRESSION -> EXPANSION   : COMPRESSION_TO_EXPANSION
RANGING     -> TRENDING    : RANGE_TO_TREND
TRENDING    -> RANGING     : TREND_TO_RANGE
<anything>  -> EVENT_RISK  : NORMAL_TO_EVENT_RISK   (checked first, independent of the table above)
everything else            : UNKNOWN                 (including "no posture change")
```

`MarketStateTransition` holds the two actual `MarketIntelligenceSnapshot` objects (not copies), a
`transition_reason` string, and `evidence` drawn from each snapshot's own `thesis.primary_thesis` — no new
measurement, purely a comparison of two already-real conclusions.

## 3. DecisionContext

```python
intelligence_snapshot_reference: str     # the source snapshot's intelligence_snapshot_id -- a reference, not an embedded copy
compatible_strategy_families: Tuple[str, ...]
blocked_strategy_families: Tuple[str, ...]     # StrategyCompatibilityAssessment's incompatible set, renamed
confidence: float                        # reused verbatim from evidence_bundle.confidence (Phase 19.3's own min-across-brains) -- not a new score
reasoning: str
evidence: Tuple[str, ...]                 # supporting_evidence + blocking_evidence, concatenated
```

`build_decision_context()` is a pure function of a `MarketIntelligenceSnapshot`: it calls
`assess_strategy_compatibility()` internally and composes the result. No broker call, no state, no side
effect. `intelligence_snapshot_reference` follows the same "reference, don't duplicate identity" pattern
`IntelligenceContext.reality_snapshot_reference` already established in Phase 19.2.2, rather than
embedding the full snapshot object a second time.

`unassessed_strategy_families` deliberately does not appear as its own field on `DecisionContext` — a
future strategy engine only needs "what's allowed" and "what's blocked" to make progress; the full
three-way breakdown (including *why* something is unassessed) remains available via
`assess_strategy_compatibility()` directly for any caller that wants it.

## Testing

`tests/test_decision_context.py`, 7 tests, proving all 5 required properties:

1. **Determinism** — `test_same_snapshot_produces_identical_decision_context`: identical `DecisionContext`
   (full dataclass equality) from the same `MarketIntelligenceSnapshot`.
2. **Differentiation** — two tests: IV_RICH vs IV_CHEAP snapshots (constructed from genuinely different
   real premium inputs) produce different `StrategyCompatibilityAssessment`s; a flat-price vs
   large-swing candle sequence produces different `RegimeType` classifications (the upstream signal
   compatibility itself depends on).
3. **Replay equivalence** — `test_live_and_replay_produce_identical_decision_context`: LIVE vs
   HISTORICAL_REPLAY produce identical `compatible_strategy_families`, `blocked_strategy_families`,
   `reasoning`, `confidence`, and even the same `intelligence_snapshot_reference` (since Phase 19.3
   already excludes `execution_mode` from the fingerprint).
4. **No execution vocabulary** — two tests: an AST-level identifier scan (function/class/variable/field
   names only, not docstring prose — a naive whole-file substring search would trip on this package's own
   "no order, no fill..." disclaimers, caught and fixed during this phase) confirms no
   order/fill/quantity/position_size/qty identifier anywhere in the package; a direct check that none of
   `DecisionContext`/`MarketStateTransition`/`StrategyCompatibilityAssessment` carry an
   order/fill/quantity/side/price-shaped field.
5. **No modification to Reality / brains / MarketIntelligenceSnapshot** —
   `test_decision_context_package_only_reads_upstream_modules_never_imports_write_paths`: structural AST
   check confirming this package never calls `.analyze(` and never instantiates any of the six brain
   classes directly — it only composes already-built `Readings`/snapshots handed to it, exactly like
   `MarketIntelligenceSnapshot`'s own builder does one layer down.

## Full regression

Baseline before this phase: 5,798 passed (post Phase 19.3). After Phase 19.4's additions: **5,805 passed,
0 failed** — exactly the 7 new tests in `test_decision_context.py`. No existing file was modified, only a
new self-contained package + one new test file added.

## Constraints — verified honored

- ❌ No entry signals, no buy/sell decisions, no position sizing — `StrategyCompatibilityAssessment`
  classifies environmental fit only.
- ❌ No execution vocabulary — verified structurally (Testing, property 4).
- ❌ No modification to Reality layer, Intelligence brains, or `MarketIntelligenceSnapshot` — verified
  structurally (Testing, property 5); this package only imports and reads their already-built output types.

## Final verdict

**Foundation established: future strategy modules can now ask "what is the market environment suitable
for?" without asking "what trade should I place?"**

Per the user's own framing: before this phase, Bujji could answer *"what does this market state mean?"*
(Phase 19.3). Now it can additionally answer, from the same real evidence and without inventing a single
new signal: *"given that meaning, what does this environment allow, and what does it forbid?"* — still
zero orders, zero positions, zero execution. The next real decision (an actual trade recommendation) is
explicitly not this phase's to make.
