# Phase 19.6 — Decision Intelligence Engine Foundation

## Objective

Build the governed reasoning layer above `MarketRealitySnapshot` (Phase 18.x), `MarketIntelligenceSnapshot`
(Phase 19.3), `DecisionContext` (Phase 19.4), and Market Understanding Memory (Phase 19.5). Answers:
"given the current market state, what decisions are rational, what risks exist, what evidence supports
them, and what conditions would invalidate them?" — never trade generation.

## New package: `bujji/decision_intelligence/`

- `models.py` — `DecisionIntelligenceSnapshot`, `DecisionPosture`, `EnvironmentAssessment`,
  `StrategyFamilyAssessment`, `MemoryContext`, `RiskObservation`, `OpportunityObservation`,
  `UncertaintyObservation`, `ContradictionObservation`
- `evidence.py` — `DecisionEvidenceBundle`, `build_decision_evidence_bundle()`
- `reasoning.py` — the mechanical reasoning rules (environment state, contradictions, posture, memory context)
- `engine.py` — `build_decision_intelligence_snapshot()`, the one entry point

## The boundary — verified structurally, not just asserted

`DecisionIntelligenceSnapshot` cannot contain BUY/SELL/CE/PE/quantity/entry price/stop loss/order action —
confirmed by `test_decision_intelligence_snapshot_has_no_order_shaped_fields` (no field on the dataclass
matches an execution-shaped name) and `test_no_ce_pe_or_buy_sell_strings_in_a_real_built_snapshot` (a real,
fully-built snapshot's entire serialized content contains no `" BUY "`, `" SELL "`, `"CE"`, or `"PE"`
token). An AST-level identifier scan (`test_no_execution_vocabulary_in_identifiers`) additionally confirms
no function/class/variable name across all five package files is execution-shaped.

## 1. Reasoning pipeline — each layer consumes only the previous layer

```
MarketRealitySnapshot (referenced via reality_fingerprint)
    -> MarketIntelligenceSnapshot (Phase 19.3, already built)
    -> DecisionContext (Phase 19.4, already built)
    -> MarketMemoryEntry matches (Phase 19.5, already queried by the caller)
    -> DecisionIntelligenceSnapshot (this phase)
```

`build_decision_intelligence_snapshot()` never calls a brain's `.analyze(`, never instantiates a broker,
never queries an `EventStore`/database itself — every input (the snapshot, the decision context, the
memory matches) is handed in by the caller, exactly like `build_market_intelligence_snapshot()` never
calls a brain and `build_decision_context()` never builds a snapshot. Verified structurally by
`test_no_direct_brain_analyze_calls_in_decision_intelligence` (AST-level: no `.analyze` attribute access,
no brain class instantiation anywhere in the package) and `test_no_datastore_or_broker_imports` (no import
from anything `EventStore`/broker/`fyers`/sqlite/Reality-tier-shaped).

## 2. Evidence-based reasoning — every conclusion answers "why"

`derive_environment_state()` mechanically composes RegimeBrain's own regime label with VolatilityBrain's
own richness label (e.g. `"trending_with_iv_rich_volatility"`) — never a new classification, a rename/join
of two already-real labels — and cites the exact `evidence_bundle` metric names (`regime.efficiency_ratio`,
`volatility.richness_ratio`, ...) that produced it. `build_observations()` wraps
`StrategyCompatibilityAssessment`'s own already-real `supporting_evidence`/`blocking_evidence`
(Phase 19.4) into `OpportunityObservation`/`RiskObservation` — never inventing a reason beyond what that
assessment already stated. No free-form generated text anywhere in this pipeline.

## 3. Historical Memory Integration — reused, not reinvented

Callers pass in memory matches already computed via Phase 19.5's own `find_similar_memories_as_of()` — this
package never queries the memory store itself (property 5). `build_memory_context()` reports:

- `matched_count == 0` → `"no historical precedent found"`
- `0 < matched_count < 5` → `"limited -- only N matching event(s)"`
- `matched_count >= 5` → `"N matching events found"`
- `statistic` stays `None` unless **at least 5 matched entries carry a real, `KNOWN`
  `MarketOutcomeObservation`** — and even then it is a plain fraction ("3 of 5 matched states showed
  IV_RICH volatility afterward"), never a synthesized percentage. Verified by
  `test_memory_statistic_only_reported_with_enough_known_samples`: 2 known-outcome matches produce
  `statistic=None`; 5 produce a real fraction string. This is exactly the phase's own explicit instruction
  — never `"Probability = 72%"` without enough real samples.

## 4. Market Posture Reasoning — `DecisionPosture`, still environmental, never a trade

One documented priority order, entirely over already-real signals:

```
1. MarketPosture.EVENT_RISK (Phase 19.3)      -> REDUCE_EXPOSURE
2. LiquidityReading.tightness == WIDE          -> REDUCE_EXPOSURE
3. any ContradictionObservation present         -> WAIT_FOR_CONFIRMATION
4. nothing assessable at all                    -> INSUFFICIENT_INFORMATION
5. a directional family is compatible           -> FAVOR_DIRECTIONAL_ENVIRONMENT (never reachable in this
                                                    scope today -- no direction signal exists anywhere in
                                                    the six in-scope brains, documented honestly rather than
                                                    silently dead code)
6. a premium-shaped family is compatible        -> FAVOR_PREMIUM_ENVIRONMENT
7. otherwise                                    -> OBSERVE
```

## 5. Contradiction Engine — `mil_next` concept reused, code never imported

Two documented rules, each citing the exact two real readings that conflict:

- `VolatilityReading.richness == IV_RICH` **and** `LiquidityReading.tightness == WIDE` →
  *"Premium opportunity exists but liquidity conditions reduce confidence"* — the phase's own worked
  example, verified live by `test_contradiction_detected_when_rich_volatility_meets_poor_liquidity`
  (constructed with genuinely rich premiums and a genuinely wide spread; the resulting `DecisionIntelligenceSnapshot`
  really does report the contradiction and posture `REDUCE_EXPOSURE`).
- The symmetric case: `IV_CHEAP` + `WIDE` liquidity → *"Premium buying opportunity exists but liquidity
  conditions reduce confidence."*

No `mil_next` code was imported — `bujji/mil_next/` remains untouched, per the standing governance rule
(Phase 19.1.1, reaffirmed every phase since).

## 6. Determinism

`decision_intelligence_id` follows `MarketIntelligenceSnapshot`'s own precedent exactly (Phase 19.3):
a two-step provisional-then-hash construction, `fingerprint_state()` reused verbatim, `created_at` excluded
from the fingerprint payload. `decision_context_id` is a **content fingerprint** of the real
`DecisionContext` this snapshot reasoned from — `DecisionContext` itself (Phase 19.4) carries no stored id
of its own, so rather than retrofit a new field onto that already-shipped, frozen model, this phase computes
the reference the same way `MarketRealitySnapshot.fingerprint()` already computes its own identity: on
demand, from real content, never stored redundantly upstream.

Verified: `test_deterministic_output` (same inputs → same id, `.fingerprint()` always matches the stored
id); `test_live_and_replay_produce_identical_fingerprint` (LIVE vs HISTORICAL_REPLAY produce the identical
`decision_intelligence_id`, `recommended_posture`, and `environment_assessment` — since `execution_mode` is
already excluded from every upstream fingerprint since Phase 19.3's own design correction). No wall-clock
call exists anywhere in this package.

## Testing

`tests/test_decision_intelligence.py`, 13 tests, proving all 8 required properties (some properties covered
by more than one test for robustness):

1. **Deterministic output** — same inputs, same `decision_intelligence_id`, `.fingerprint()` self-consistent.
2. **Replay equality** — LIVE vs HISTORICAL_REPLAY byte-identical.
3. **No execution vocabulary** — 3 tests: AST identifier scan, no-order-shaped-fields check, and a real
   built snapshot's full serialized content contains no BUY/SELL/CE/PE token.
4. **No direct brain calls** — AST-level: no `.analyze` attribute access, no brain class instantiation.
5. **No datastore access** — AST-level import scan: no `EventStore`/broker/`fyers`/sqlite/Reality-tier import.
6. **Memory integration** — 2 tests: `MemoryContext` genuinely reflects supplied matches (count, ids,
   confidence note); the statistic threshold (5 known-outcome samples) is honored exactly.
7. **Contradiction detection** — 2 tests: the rich-volatility/poor-liquidity contradiction fires on real
   constructed inputs and correctly drives `REDUCE_EXPOSURE`; aligned conditions produce no contradiction.
8. **Insufficient evidence handling** — 2 tests: unassessable strategy families always surface as
   `UncertaintyObservation`s; a genuinely flat/unresolvable input (`UNKNOWN` richness, invalid liquidity
   quotes, no VIX) never gets forced into a false `FAVOR_*` posture — it lands on
   `INSUFFICIENT_INFORMATION`/`OBSERVE` instead.

Two test-authoring bugs caught and fixed during this phase (same class of mistake as Phase 19.4's own,
now avoided proactively where possible but still caught immediately when it recurred): a naive substring
scan for `"sell"` flagged the legitimate identifier `premium_selling_assessed` (a real strategy-family
concept name already used throughout `msi_strategy_selection_foundation`, not an order action) — narrowed
the term list; a naive substring scan for `".analyze("` flagged `engine.py`'s own docstring prose
explaining that it never calls `.analyze(` — switched to an AST-level `ast.Attribute(attr="analyze")` check.

## Full regression

Baseline before this phase: 5,817 passed (post Phase 19.5). After Phase 19.6's additions: **5,830 passed,
0 failed** — exactly the 13 new tests. No existing file was modified, only five new self-contained modules
in `bujji/decision_intelligence/` + one new test file.

## Explicitly NOT built this phase (per the phase's own scope)

- ❌ Strategy selection engine (no "best family" ranking anywhere — `StrategyFamilyAssessment` only carries
  Phase 19.4's own compatible/blocked sets forward, never picks one).
- ❌ Trade planner, options selector, position manager, execution engine, portfolio manager.

## Final verdict

Bujji can now answer, for any real market moment: *"What kind of market environment is this, what
opportunities are structurally compatible, what risks exist, what historical memory applies, and how
confident should we be?"* — entirely through composed, evidence-cited, deterministic reasoning over
already-validated layers, without placing a single trade. Per the user's own framing: this is the governed
intelligence layer, not the hand that acts.
