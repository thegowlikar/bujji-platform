# Phase 19.2.2 — Intelligence Determinism Hardening Implementation

## Goal

Make the existing Intelligence Core brains (`bujji/intelligence/*_brain.py`) historically reproducible
before building `MarketIntelligenceSnapshot` (Phase 19.3). Per Phase 19.2.1's finding, the brains were
never mathematically wrong — every classification/pricing method is already a pure function of its real
inputs. The gap was epistemic: no brain could prove *when*, from *what evidence*, and under *which reality
state* it reached a reading, because every brain stamped its own `as_of` field by calling `now_ist()`
directly, and evidence dictionaries carried no lineage back to their Reality-tier source.

This phase closes exactly that gap, and nothing else. No `MarketIntelligenceSnapshot`, no strategy
decisions, no execution logic, no `mil_next` merge, no `PremiumBrain`/`BehaviourBrain` changes.

## Part 1 — Clock injection

### `IntelligenceContext` (new: `bujji/intelligence/context.py`)

One shared context object, not six independent clocks (per explicit instruction). Modeled on
`mil_next.snapshot_builder`'s already-proven `Clock = Callable[[], datetime]` pattern — reused as a
design, not as code; `mil_next` itself remains untouched.

```python
@dataclass(frozen=True)
class IntelligenceContext:
    as_of_time: datetime                                    # required, no default
    reality_snapshot_reference: Optional[str] = None        # MarketRealitySnapshot.fingerprint(), when known
    dataset_artifact_reference: Optional[str] = None         # DatasetArtifact.artifact_id, when known
    execution_mode: str = "LIVE"                             # LIVE | PAPER | HISTORICAL_REPLAY
```

`as_of_time` has no default — a default of "now" would silently reintroduce the exact bug this phase
exists to close. `execution_mode` fails closed (`__post_init__` raises on an unrecognized value).

### Brains modified

`RegimeBrain`, `StructureBrain`, `LiquidityBrain`, `VolatilityBrain`, `GreeksBrain`, `EventBrain` — all
six now take a required `context: IntelligenceContext` argument. Every `now_ist()` call site (one-for-one
with Phase 19.2.1's confirmed six-for-six finding) was replaced with `context.as_of_time`. `now_ist`
imports were removed entirely from all six files (structurally verified — see Testing, Property 3).

**Zero classification/pricing logic changed.** Every private method (`_classify`, `_efficiency_ratio`,
`_scaled_confidence`, `_annualized_realized_vol`, `_compression_ratio`, `_bs_price`, `_bs_delta`,
`_bs_gamma`, `_bs_theta`, `_bs_vega`, `solve_implied_volatility`, `_classify_proximity`,
`_classify_tightness`, `_classify_richness`, `_classify_vix`, `_classify_exposure`) is byte-identical to
before this phase — confirmed via `test_intelligence_runner_and_brains_classification_logic_untouched`
(no removed `def` line for any of these methods).

### Production callers updated

Making `context` required broke every real call site, discovered by grepping the whole app for
`.analyze(` rather than assuming only the brain files themselves needed to change:

- `bujji/intelligence/runner.py` — `run_intelligence()`, the one production entry point into the MIC —
  now builds one `IntelligenceContext(as_of_time=now, execution_mode=EXECUTION_MODE_LIVE)` per cycle from
  its own already-real `now: datetime` parameter, and threads it through all six in-scope brain calls.
- `bujji/market_state/intelligence_cycle_recorder.py` — calls `LiquidityBrain.analyze()` directly (not
  through `runner.py`). Builds its context from `snapshot.timestamp`, parsed via `datetime.fromisoformat()`
  since `MarketSnapshot.timestamp` is a real ISO string field, not a `datetime` object (caught by a full
  regression run — see Errors below).
- `bujji/shadow_runtime/shadow_session_runner.py` — also calls `LiquidityBrain.analyze()` directly per
  monitoring cycle. Builds its context from `self._clock()`, the file's own already-injected clock
  (no new clock source introduced).

## Part 2 — Evidence lineage

### `IntelligenceEvidence` + `wrap_evidence()` (new: `bujji/intelligence/evidence.py`)

Wraps, never replaces, each brain's existing `evidence: dict[str, Any]`. Per explicit instruction, the
original dict is left untouched on every Reading class; a new `evidence_lineage: Dict[str,
IntelligenceEvidence]` field is added additively alongside it.

```python
@dataclass(frozen=True)
class IntelligenceEvidence:
    metric_name: str
    value: Any                              # copied verbatim from the raw evidence dict, never recomputed
    source_reference: Optional[str]         # context.reality_snapshot_reference
    observation_references: Tuple[str, ...] # real HistoricalObservation.observation_id values, when known
    observed_at: datetime                   # context.as_of_time -- never wall-clock
```

`wrap_evidence(raw_evidence, *, context, observation_references=())` is a pure, mechanical wrap: one
`IntelligenceEvidence` per key/value pair, value copied verbatim. The function has no access to anything
it could use to fabricate a number, and `source_reference`/`observation_references` are honestly `None`/
`()` when the caller has no real reference to supply (never guessed).

### `models.py`

`evidence_lineage: Dict[str, IntelligenceEvidence] = field(default_factory=dict)` added to the six
in-scope Reading classes only: `RegimeReading`, `StructureReading`, `LiquidityReading`,
`VolatilityReading`, `GreeksReading`, `EventReading`. `PremiumReading` and `BehaviourReading` were
deliberately left untouched (out of scope, per Phase 19.0.1/19.1's established exclusion).

Each of the six brains now calls `evidence_lineage=wrap_evidence(evidence, context=context)` at every
return site that already builds an `evidence` dict.

## Part 3 — Volatility reference policy

### `VolatilityReferencePolicy` (new: `bujji/intelligence/volatility_policy.py`)

Formalizes, rather than reinvents, the ATM-selection convention already live in
`bujji/market_perception/option_chain_adapter.py:122`:

```python
atm_strike = min((c.strike for c in contracts), key=lambda s: abs(s - spot))
```

`select_atm_strike(strikes, spot)` extracts that exact expression as a pure, reusable function (raises on
an empty `strikes` sequence rather than guessing). `select_atm_straddle(spot, expiry, contracts)` applies
it against one real options-chain snapshot to pick the same-expiry CE+PE pair at the nearest strike,
raising if either leg is missing rather than silently filling a gap. Initial and only supported policy:
`VOLATILITY_REFERENCE_ATM_STRADDLE = "ATM_STRADDLE"`.

The live `option_chain_adapter.py` itself is untouched — it is tightly coupled to an async broker quote
fetch, a different concern from this pure selection rule.

## Files changed

**New (`bujji/intelligence/`):**
- `context.py` — `IntelligenceContext`
- `evidence.py` — `IntelligenceEvidence`, `wrap_evidence()`
- `volatility_policy.py` — `select_atm_strike()`, `select_atm_straddle()`, `AtmStraddleSelection`

**Modified:**
- `bujji/intelligence/models.py` — additive `evidence_lineage` field on 6 Reading classes
- `bujji/intelligence/regime_brain.py`, `structure_brain.py`, `liquidity_brain.py`, `volatility_brain.py`,
  `greeks_brain.py`, `event_brain.py` — clock injection + evidence lineage wiring only
- `bujji/intelligence/runner.py` — threads one shared `IntelligenceContext` per cycle
- `bujji/market_state/intelligence_cycle_recorder.py` — builds context from `snapshot.timestamp`
- `bujji/shadow_runtime/shadow_session_runner.py` — builds context from its own injected `self._clock()`

**Untouched (explicitly out of scope):**
- `bujji/intelligence/premium_brain.py`, `behaviour_brain.py`
- `bujji/mil_next/` (entire package — architecture reference only, per standing governance rule)

## Testing

New file: `tests/test_intelligence_determinism.py` (10 tests), proving all five required properties:

1. **Historical replay determinism** — `test_replay_of_same_timestamp_produces_identical_output`: two
   independent `IntelligenceContext` instances with the same `as_of_time` produce byte-identical
   `RegimeReading` output (regime, confidence, evidence, `as_of`).
2. **Live/historical mode equivalence** — `test_live_and_historical_execution_modes_produce_identical_reading`:
   the same candles + same `as_of_time` produce the same reading regardless of `execution_mode`, confirming
   no brain branches on it.
3. **No brain calls the system clock internally** — `test_no_in_scope_brain_imports_or_calls_now_ist`: a
   structural, source-inspection test (grep + AST walk for clock imports) across all 6 in-scope brain
   files, not a runtime test — a runtime test could pass by accident if a stale cached value happened to
   match the real clock.
4. **Evidence traces back to source observations** — 3 tests: `wrap_evidence()` carries context provenance
   verbatim, `RegimeBrain`'s real `evidence_lineage` matches its own `evidence` dict exactly, and lineage
   is honestly `None`/`()` (never fabricated) when the caller supplies no real reference.
5. **ATM volatility selection is deterministic** — 4 tests: same input always produces the same strike,
   matches the live `option_chain_adapter.py` precedent expression exactly, straddle selection picks the
   same expiry's CE+PE at that strike, and both selection functions raise (never guess) on
   empty/incomplete input.

### Existing test files updated (signature change, not behavior change)

`test_regime_brain.py`, `test_structure_brain.py`, `test_liquidity_brain.py`, `test_volatility_brain.py`,
`test_greeks_brain.py`, `test_event_brain.py`, `test_execution_reality_phase1.py`,
`test_msi_strategy_selection_foundation.py` — every `brain.analyze(...)` call site updated to pass a
fixed `TEST_CONTEXT = IntelligenceContext(as_of_time=<fixed datetime>)`, mechanically inserted via a
paren-matching script rather than hand-edited line by line (8 files, ~140 call sites) to avoid transcription
errors. `test_premium_brain.py`/`test_behaviour_brain.py` needed no changes (out of scope).

### Pre-existing safety guards updated (not deleted)

Three test files (`test_greeks_and_premium_behaviour_safety.py`, `test_intelligence_cycle_recorder_safety.py`,
`test_intelligence_safety_phase2.py`) contained guards from earlier phases asserting the intelligence
brains/runner/`shadow_session_runner.py` were byte-identical or zero-diff against a prior commit
(`b148e39`) — a "these files are permanently read-only" invariant from before this phase existed. Per
explicit user decision, these were updated (not deleted) to assert the phase's actual invariant instead:
no classification/pricing method definition was removed, only clock/evidence wiring changed, and
`premium_brain.py`/`behaviour_brain.py` remain genuinely untouched.

### Full regression

Baseline before this phase: 5,775 passed. After this phase's changes (including the new
`test_intelligence_determinism.py` and the updated safety guards): **5,785 passed, 0 failed**. No test
outside the intelligence/shadow-runtime surface was affected.

## Errors and fixes during this phase

- **`snapshot.timestamp` is a `str`, not a `datetime`.** `intelligence_cycle_recorder.py`'s
  `IntelligenceContext` was initially built with `as_of_time=snapshot.timestamp` directly. This passed
  Python's dataclass construction (no runtime type enforcement) but broke at
  `LiquidityReading.to_dashboard()`'s `self.as_of.isoformat()` call, surfaced only by the full regression
  run (`test_shadow_runtime_recovery.py` and related — 9 tests failed with
  `AttributeError: 'str' object has no attribute 'isoformat'`, all silently swallowed into
  `ShadowSessionRunner`'s own `self._errors` list rather than raising, per that module's own "never hang
  the loop" discipline). Fixed by parsing with `datetime.fromisoformat(snapshot.timestamp)` before
  constructing the context.
- **Stale "never modify" safety guards.** Three pre-existing test files asserted byte/zero-diff identity
  against a pre-this-phase commit for exactly the files this phase's own mandate requires changing. Flagged
  to the user rather than silently resolved; user chose to update the guards to check the new invariant
  (no removed classification logic) instead of deleting them or leaving them permanently red.

## Explicit exclusions (per this phase's own scope)

- No `MarketIntelligenceSnapshot` implementation — that is Phase 19.3.
- No strategy decisions, no execution logic.
- No `mil_next` merge — it remains an architecture reference only.
- No changes to `PremiumBrain` or `BehaviourBrain` — still out of Market Intelligence Core scope
  (position/learning intelligence, per Phase 19.0.1/19.1).

## What this phase enables

Every in-scope brain reading now carries a real, injected `as_of` that is identical under replay, and an
`evidence_lineage` that honestly traces back to its Reality-tier source when one is known. Per the user's
own framing: this phase gives Bujji "a timestamped memory of its own thoughts." Phase 19.3 can now build
`MarketIntelligenceSnapshot` — "what Bujji believed about the market at that exact moment" — on top of a
genuinely reproducible Intelligence Core.
