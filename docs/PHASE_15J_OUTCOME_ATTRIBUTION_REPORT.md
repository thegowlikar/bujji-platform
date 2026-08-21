# Phase 15J -- Outcome Attribution: Final Report

## 1. Forensic findings -- attribution input classification

| Input | Classification | Source |
|---|---|---|
| Entry snapshot (direction/regime/thesis/confidence, entry Greeks/premium) | `AVAILABLE_NOW` | `PositionLifecycle.entry` (15G/15F) |
| Thesis evaluation trajectory (direction/regime/volatility_trend checks over time) | `AVAILABLE_NOW` | `PositionLifecycle.thesis_evaluations` (15G, from 15F) |
| Management assessment trajectory (exposure/premium/liquidity/expiry evidence) | `AVAILABLE_NOW` | `PositionLifecycle.management_assessments` (15G, from 15I) |
| Exit reason + close timestamp | `AVAILABLE_NOW` | `PositionLifecycle.exit_reason`/`closed_at` (15G) |
| **Realized P&L** | **`MISSING`** | `PositionLifecycle.realized_pnl` -- defaulted `None`, and **confirmed by direct source inspection that no code path in this codebase ever sets it to anything else** (no PaperBroker linkage, per Phase 15B's own disclosed limitation, still true). |
| Exit price/premium per leg | `MISSING` | `PositionClosed`'s own event payload (15G) captures only `closed_at`/`exit_reason` -- no price field exists at all. |
| Real market-state observations at any given cycle | `REFERENCE_ONLY` | `intelligence_cycle.jsonl` -- same Phase 15H finding (VSB-downstream fields are referenced, not independently reconstructed). |
| Greeks/Premium Behaviour at entry and each evaluation | `DERIVABLE` (when real ATM bid/ask exists) / `MISSING` (this specific archived session) | `bujji.msi_greeks`/`bujji.premium_behaviour` (15E), sparse in Session B per Phase 15E's own finding. |
| Strategy-selection alternatives / rejection reasoning | `REFERENCE_ONLY` | `intelligence_cycle.jsonl`'s `strategy_selection` field -- not consumed this phase (kept out of scope, see Section 3). |

**Can the system distinguish selection fault vs. entry-timing fault vs.
management fault vs. market-move fault? Partially, and this is
disclosed rather than overstated**: it can distinguish these along the
dimensions where real trajectory evidence exists (thesis checks,
management evidence) -- e.g., "thesis stayed intact throughout" clears
selection even on a loss; "direction had already deviated at the first
post-entry evaluation" implicates entry timing specifically. It
CANNOT currently attribute a specific DOLLAR amount to any of these
causes, because realized P&L itself does not exist yet (see above).
This is the single most important, disclosed limitation of this phase.

## 2. Attribution taxonomy (Step 2) -- 12 dimensions, 4 roles

Dimensions: `SELECTION`, `ENTRY_TIMING`, `REGIME`, `DIRECTION`,
`VOLATILITY`, `PREMIUM_BEHAVIOUR`, `GREEKS_EXPOSURE`, `LIQUIDITY`,
`MANAGEMENT`, `EXIT_TIMING`, `STRUCTURAL_GEOMETRY`,
`INSUFFICIENT_EVIDENCE`. Roles: `PRIMARY_CAUSE`,
`CONTRIBUTING_FACTOR`, `PROTECTIVE_FACTOR`, `UNKNOWN`. Every dimension
maps to a REAL, already-available evidence source (Section 1's
`AVAILABLE_NOW` row) -- no dimension exists speculatively.

## 3. Evidence model (Step 3)

`AttributionEvidence(dimension, role, source, strength, observed_value,
expected_value, impact_direction, confidence, explanation)` --
`source` is always a literal path into the real lifecycle data (e.g.
`"thesis_evaluations[*].checks.direction"`), so every conclusion is
traceable back to exactly what was read. `impact_direction` has 4
values (`POSITIVE`/`NEGATIVE`/`NEUTRAL`/`UNKNOWN`) with `UNKNOWN`
STRUCTURALLY DIFFERENT from `NEUTRAL` -- `NEUTRAL` means evidence
resolved and showed no material effect either way; `UNKNOWN` means no
resolvable evidence existed at all. Verified by a dedicated test that
every `UNKNOWN`-role evidence item also carries `impact_direction=UNKNOWN`,
never silently defaulting to `NEUTRAL`.

## 4. Engine (Step 4)

`bujji/outcome_attribution/`: `attribute_position_outcome(lifecycle)`
-- pure, side-effect-free, consumes ONLY a real `PositionLifecycle`
(Phase 15G). No re-reading of `market_snapshots.jsonl`/
`intelligence_cycle.jsonl` directly -- the lifecycle itself is already
the replayable source of truth (inherits Phase 15H's determinism
guarantee for free by construction, not by re-deriving it). No wall-
clock read, no randomness -- verified by dedicated safety tests.

## 5. Outcome vs. causal attribution, explicitly separated (Step 5)

`outcome_direction` (`PROFIT`/`LOSS`/`BREAKEVEN`/`UNKNOWN`) is derived
**solely** from `realized_pnl`; causal evidence (thesis/entry-timing/
management/etc.) is derived **solely** from the lifecycle's accumulated
history -- the two are computed independently and never allowed to
influence each other. Proven by dedicated fixtures:
- "profitable trade, correct thesis" -> `PROFIT` + selection evidence `POSITIVE`.
- "losing trade despite correct thesis" -> `LOSS` + selection evidence **still `POSITIVE`** (the loss is never blamed on selection without real evidence).
- "bad selection + profitable outcome" -> `PROFIT` + selection evidence **`NEGATIVE`** (a good outcome never clears a thesis that was actually invalidated).

## 6. Lifecycle integration (Step 6)

One new event type, `OUTCOME_ATTRIBUTED` -- opposite guard direction
from `THESIS_EVALUATED`/`MANAGEMENT_ASSESSED`: only accepted while the
position is `CLOSED` (an open position returns `NOT_READY` immediately,
never a fabricated outcome). Stored as a **single, immutable** field
(`PositionLifecycle.outcome_attribution`) -- a second attempt with
identical content is idempotent; a second attempt with DIFFERENT
content is rejected outright ("immutable historical evidence can never
be silently overwritten"), a stricter guarantee than the tuple-based
accumulation pattern used for thesis/management history, deliberately
chosen because an outcome attribution is a one-time, deterministic
conclusion, not an ongoing observation stream. No second lifecycle
state machine was created.

## 7. Replay proof (Step 7)

Reuses Phase 15G's own `EventStore`-based `hydrate_position_lifecycles`
directly -- no new replay mechanism. Proven: deterministic double
replay (byte-identical `to_dict()`); event reordering within the file
(an `OUTCOME_ATTRIBUTED` event physically appended BEFORE
`POSITION_CLOSED`) is still correctly rejected at replay time -- file
order is never a substitute for the `CLOSED` guard; duplicate
`event_id` handled by `EventStore`'s existing dedup, never double-applied.

## 8. Fixture results (Step 8) -- all 15 required scenarios, 23 tests total

| # | Scenario | Result |
|---|---|---|
| 1 | Profitable, correct thesis | `PROFIT`, selection `POSITIVE` -- PASS |
| 2 | Losing despite correct thesis | `LOSS`, selection `POSITIVE` (not blamed) -- PASS |
| 3 | Bad selection + profit | `PROFIT`, selection `NEGATIVE` -- PASS |
| 4 | Good selection, bad entry timing | entry_timing `NEGATIVE` -- PASS |
| 5 | Management prevented larger loss | management `PROTECTIVE_FACTOR`/`POSITIVE` -- PASS |
| 6 | Management recommendation not acted on | management `UNKNOWN` impact, never fabricated as worse/better -- PASS |
| 7 | Volatility dominant factor | volatility `NEGATIVE` -- PASS |
| 8 | Premium behaviour dominant factor | premium `NEGATIVE` -- PASS |
| 9 | Exit timing (labeled vs. arbitrary reason) | labeled reason captured; `session_end` never `NEGATIVE` -- PASS |
| 10 | Liquidity materially affected outcome | liquidity `POSITIVE` when never adverse -- PASS |
| 11 | Insufficient evidence | multiple dimensions honestly `UNKNOWN` -- PASS |
| 12 | Conflicting evidence | both negative and positive signals preserved, neither discarded -- PASS |
| 13 | Multi-leg Iron Condor | attributed at position level, legs untouched -- PASS |
| 14 | Short-vol with IV expansion | volatility + exposure both `NEGATIVE` -- PASS |
| 15 | Directional reversal | direction `NEGATIVE` (genuine majority, not a coincidental tie) -- PASS |

Plus determinism, empty-lifecycle, and readiness-gating tests.
**23/23 passing** (`test_outcome_attribution.py`), plus **10/10**
lifecycle-integration/replay tests (`test_position_lifecycle_outcome_attribution.py`).

## 9. Real-data results (Step 9)

`SHADOW-OBSERVATORY-2026-08-06` never opened a real position (confirmed
again, consistent with every prior phase). Result reported explicitly:
**`NO_REAL_POSITION_OUTCOME_AVAILABLE`** -- no fabricated outcome was
produced. What WAS validated with real data: the raw evidence sources
this engine's dimensions would eventually consume (real direction/regime
readings) are present in 174/174 real cycles -- confirming attribution
READINESS, kept explicitly separate from an actual outcome result.

## 10. Safety (Step 10)

9/9 dedicated safety tests: no forbidden imports (explicitly including
`msi_strategy_selection_foundation`, `msi_trade_intent`,
`msi_decision_synthesis`, `position_management` -- the HARD BOUNDARY's
own no-feedback-path requirement, mechanically enforced by import
absence, not merely a comment), no forbidden order calls, no broker/
network/capital references, no wall-clock or randomness dependence, a
real regression proof that recording an attribution never mutates
`status`/`legs`, missing-evidence-never-fabricated across all
dimensions, cumulative lifecycle diff additive-only, and
`trading_brain`/`risk_governor`/broker guard files confirmed
byte-untouched. No new documented exceptions were needed.

## 11. Regression (Step 11 in the mission's own numbering, Step 10 here)

`4814 passed` (1 pre-existing, unrelated deprecation warning). Zero
regressions; all pre-existing safety tests preserved unmodified.

## 12. Consumer trust gate (Step 11)

**Verdict: `TRUSTED_ANALYTICAL_CONSUMER`** -- with one explicit, disclosed
caveat.

- Deterministic: YES (dedicated test, `to_dict()` equality across repeated calls).
- Replayable: YES (Section 7, reuses Phase 15H's pattern via Phase 15G's own hydration).
- Lifecycle-integrated: YES (`OUTCOME_ATTRIBUTED`, guarded, idempotent, conflict-rejecting).
- `UNKNOWN`-preserving: YES (dedicated test across all dimensions; `UNKNOWN` never conflated with `NEUTRAL`).
- Causality not overstated: YES (Section 5's explicit profit/loss vs. cause separation, proven by 3 dedicated fixtures).
- Real-data limitations disclosed: YES (Section 9, `NO_REAL_POSITION_OUTCOME_AVAILABLE` reported honestly).
- Safety boundary proven: YES (Section 10, including the mechanically-enforced no-feedback-path guarantee).
- Multi-leg behavior proven: YES (fixture 13).
- Incomplete evidence handled correctly: YES (fixtures 6, 11; dedicated safety test).
- Full regression green: YES (4814/4814).

**The caveat**: this engine is trustworthy as an ANALYTICAL consumer of
lifecycle history -- it correctly describes and traces causal evidence
without overclaiming. It is **NOT** yet capable of quantifying outcomes
in dollar terms, because `realized_pnl` and exit price/premium do not
exist anywhere in the system yet (Section 1's central finding). Per
the mission's own explicit instruction, it remains **disconnected from
live decision-making** regardless of this trust verdict -- analytical
trust does not authorize execution influence.

## 13. Files changed

New:
- `bujji/outcome_attribution/{__init__,models,engine}.py`
- `tests/test_outcome_attribution.py`
- `tests/test_position_lifecycle_outcome_attribution.py`
- `tests/test_outcome_attribution_safety.py`

Modified (additive only):
- `bujji/position_lifecycle/models.py` -- `EVENT_OUTCOME_ATTRIBUTED`, `outcome_attribution` field.
- `bujji/position_lifecycle/engine.py` -- `build_outcome_attributed_payload`, `_apply_outcome_attributed` reducer, dispatch wiring.

## 14. Newly discovered gaps

- **`realized_pnl`/exit price are `MISSING` system-wide** -- the single highest-leverage gap this phase surfaced. Without it, NO outcome-quantification is possible anywhere in Bujji, regardless of how good the causal-attribution logic becomes.
- **Strategy-selection alternatives/rejection reasoning** (`intelligence_cycle.jsonl`'s `strategy_selection.alternative_candidates`) is real and persisted but NOT consumed by this phase's `SELECTION` dimension -- a real, deeper "was a better family available" analysis remains future work.
- **Exit timing quality cannot be quantified** without an exit price -- currently only the LABEL of the exit reason is captured, not whether the timing itself was good or bad.

## 15. Recommended next phase

Two real, evidence-based candidates emerged, and they are not
independent:

**Phase 15K -- Structured Exit + P&L Linkage.** This phase's own central
finding (`realized_pnl` is `MISSING`, system-wide, not merely sparse)
is now the single highest-leverage gap in the entire architecture --
it blocks not just richer outcome attribution, but ANY future
PaperBroker integration, ANY action-plan generation for
ADJUST/HEDGE/ROLL (Phase 15I's own disclosed limitation), and ANY
learning/memory capability. Recommended over the mission's other
candidates (formal portfolio-level state, execution-quality attribution,
real multi-session validation) because those all implicitly assume P&L
already exists to reason about, and none of them can be genuinely
validated without it. This would extend `PositionClosed`'s event
payload additively (exit price/premium per leg) and give
`PositionLifecycle.realized_pnl` its first real, non-`None` value --
still strictly observational, still zero broker-write capability,
consistent with every phase's established discipline.
