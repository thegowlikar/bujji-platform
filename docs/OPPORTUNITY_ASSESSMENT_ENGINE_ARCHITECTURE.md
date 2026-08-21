# Opportunity Assessment Engine (OAE) — Series 104 Architecture

## What this is

OAE answers: was Production's decision objectively justified, using only
causal evidence? It assesses **decision quality**, never profit — no
field anywhere in this package represents an outcome or a P&L number.
Every real trading day gets exactly one of five classifications:
`GOOD_TRADE_TAKEN`, `TRADE_SUBOPTIMAL`, `CORRECT_STAY_OUT`,
`OPPORTUNITY_IDENTIFIED`, `TRADE_SHOULD_NOT_HAVE_OCCURRED`.

## The cleanest isolation in the whole learning stack

Series 102 (CRE) needed one file that invokes real decision functions.
Series 103 (MPC) needed one file that reads real Intelligence-layer
types. **OAE needs neither.** It consumes existing Series 99–103
artefacts, and per the mission's own framing — "OAE must consume existing
artefacts. It must not recreate them" — the cleanest way to do that
without re-importing every upstream package's types is the sibling-
isolation convention already established since Sprint 120
(`ExpressionAssessmentView`): OAE defines its own local, plain `*View`
dataclasses (`DecisionView`, `PhenomenaView`, `CounterfactualView`), and
the *caller* is responsible for translating a real `DecisionRecord`/
`MarketPhenomenaReport`/`CounterfactualSession` into one.

Result: **`test_oae_imports_nothing_from_any_other_bujji_package`** — a
single test verifying zero `bujji.*` imports anywhere in the package,
stricter than any isolation test in Series 100–103. No isolated exception
file exists because none is needed.

## Package structure

`bujji/msi_opportunity_assessment/`, same 9-file-family house
convention:

| File | Responsibility |
|---|---|
| `taxonomy.py` | The 5 exhaustive classifications; real string values reused verbatim (not imported) from Series 99/102's own taxonomies. |
| `models.py` | `DecisionView`/`PhenomenaView`/`CounterfactualView` (local translations), `OpportunityAssessment`, `OpportunityAssessmentExplanation`. |
| `engine.py` | Pure: `validate_causality`, `assess_day`, the internal `_opportunity_criteria` AND-gate. |
| `serialization.py`, `journal.py`, `query.py` | Standard house conventions. |

## The classification logic, exactly as specified

**`TRADE_APPROVED` days** (burden of proof is neutral — decision
already happened, question is whether it was justified):
1. Real, contemporaneous `conflicting_domains` present at decision time → `TRADE_SHOULD_NOT_HAVE_OCCURRED` (`CONFIDENCE_HIGH`).
2. Else, a real legal counterfactual selected a *different* real family → `TRADE_SUBOPTIMAL` (`CONFIDENCE_MODERATE`) — disclosed as an evidentiary difference, explicitly never framed as a profit claim.
3. Else → `GOOD_TRADE_TAKEN` — the honest default absent disqualifying evidence.

**`NO_TRADE` days** (Hindsight Protection, mandatory ordering):
`CORRECT_STAY_OUT` is the real default. `OPPORTUNITY_IDENTIFIED` is only
reachable if **all 6** of the mission's own criteria pass:

1. Market Phenomena support it (real, non-empty `phenomenon_types`).
2. Counterfactual Replay demonstrates a legal path (real `LEGAL` + a real selected family).
3. Decision Auditor confirms Production stayed out.
4. Evidence Packet exists (non-empty real packet ids).
5. Earliest causal timestamp is known.
6. No future information was required (re-validated here, defense in depth on top of Series 102's own check).

Test-verified exhaustively: `test_any_single_missing_criterion_defaults_
to_correct_stay_out` — five parametrized cases, each missing exactly one
criterion, each correctly falling back to `CORRECT_STAY_OUT`. The burden
of proof genuinely sits on the opportunity, not just in prose.

## Causality (Deliverable 6)

One re-checked rule: a counterfactual's `earliest_causal_timestamp` must
never exceed the real decision's own `timestamp`. A violation doesn't
raise — it produces a real, disclosed, conservative assessment
(`CORRECT_STAY_OUT`/`GOOD_TRADE_TAKEN` depending on the real outcome,
`evidence_strength=NONE`, and the violation spelled out in
`supporting_reasoning[0]`), mirroring CRE's own "record it, don't hide
it" choice rather than raising and losing the record entirely.

## No Engineering — structurally, not just by policy

`test_models_have_no_knowledge_candidate_or_engineering_proposal_type`
and `test_assessment_never_contains_a_recommendation_field` verify there
is no dataclass anywhere in this package that could hold a
recommendation, a Knowledge Candidate, or an Engineering Proposal. This
mirrors Series 101's own "No Opinions" structural proof for
`EvidencePacket` — the constraint isn't a promise, it's the absence of a
field to violate it with.

## Golden replay tests

Built from the real corpus's one real `NO_TRADE` day (2026-07-13, the
Sprint 116/120 finding) and the real counterfactual result this session's
own Series 102 work actually observed for that day (mid-day cutoff →
real `TREND_REVERSAL`, no real family ever selected on that path either).
`test_golden_replay_real_no_trade_day_with_real_midday_counterfactual`
uses that exact real, previously-observed result (`alternative_selected_
family=None`) and confirms OAE correctly refuses to claim an opportunity
just because *something* different was observed — criterion 2 of the
AND-gate genuinely fails, and the assessment honestly stays
`CORRECT_STAY_OUT`.

## Verification summary

- Regression: **2976/2976 passing** (2949 pre-existing + 27 new OAE
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.
- Isolation: the strictest zero-bujji-import test in the learning stack.

## Explicitly not implemented (per mission's own Non-Goals)

Knowledge Candidate generation, Engineering Proposals, strategy
optimisation, Production changes, automatic learning. Per your own
proposed sequence, Series 105 (synthesizing Knowledge Candidates from
OAE + the rest of the stack) is deliberately deferred — implementation
stops here.
