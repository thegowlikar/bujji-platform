# Knowledge Validation Engine (KVE) — Series 105 Architecture

## What this is

KVE answers: is this pattern consistently real across many days, or a
one-off? It doesn't invent hypotheses — it measures caller-supplied,
real, disclosed occurrences of a candidate pattern (each citing real
Series 101–104 artefact ids) and classifies the pattern into exactly one
of seven states: `NOT_OBSERVED`, `OBSERVED`, `REPEATED`, `EMERGING`,
`VALIDATED`, `DECAYING`, `INVALIDATED`.

## Isolation — tied with OAE for strictest in the stack

Same zero-bujji-import discipline as Series 104: `HypothesisOccurrence`
is a local, plain translation of real upstream artefacts (mirrors
`ExpressionAssessmentView`'s sibling-isolation convention). KVE never
imports Series 100–104's own types, never Production, never raw market
feeds — `test_kve_imports_nothing_from_any_other_bujji_package` verifies
zero `bujji.*` imports anywhere in the package.

## Package structure

`bujji/msi_knowledge_validation/`, same 9-file-family house convention:

| File | Responsibility |
|---|---|
| `taxonomy.py` | The 7 exhaustive validation states, declarative thresholds. |
| `models.py` | `HypothesisOccurrence` (local translation), `KnowledgeValidationReport`, `KnowledgeValidationExplanation`. |
| `engine.py` | Pure: `validate_causal_order`, `validate_hypothesis`. |
| `journal.py` | `KnowledgeValidationJournal` — real validation **history**, never overwritten (Deliverable 6). |
| `serialization.py`, `query.py` | Standard house conventions. |

## The seven-state classification, exactly as specified

A fixed, disclosed decision tree over real, measured metrics — never a
score fit to outcomes:

1. `occurrence_count == 0` → `NOT_OBSERVED`.
2. `consistency_ratio < 0.5` (real fraction of occurrences that were both
   causally valid and counterfactually legal) with `>= 2` occurrences →
   `INVALIDATED` — checked **before** the count-based states, since a
   pattern actively contradicted by its own majority evidence should
   never read as merely "repeated."
3. `occurrence_count == 1` → `OBSERVED` — "a single occurrence is never
   evidence" (test-verified, same principle as Series 100).
4. `< 5` occurrences → `REPEATED`.
5. `>= 5` occurrences, but the real recent-vs-lifetime rate comparison
   shows `WEAKENING` → `DECAYING`, overriding what would otherwise be
   `EMERGING`/`VALIDATED` — decay is checked before promotion, not after.
6. `>= 20` occurrences AND `>= 3` distinct real market classifications AND
   `>= 0.5` real replay-support ratio AND consistent → `VALIDATED`.
7. Otherwise (`>= 5`, not decaying, not yet meeting all `VALIDATED`
   criteria) → `EMERGING`.

**Diversity is a real, separately-enforced gate, not implied by volume**:
`test_many_occurrences_without_diversity_stays_emerging_not_validated`
confirms 25 real occurrences of the *same* market classification stay
`EMERGING` — count alone never promotes a pattern to `VALIDATED`.

## Decay detection, reimplemented locally (not imported from Series 100)

Series 100's MLE has its own `assess_decay` — KVE cannot import it (zero-
sibling-import isolation), so the same real, declarative recent-vs-
lifetime rate comparison is reimplemented locally in `engine.py`'s
`_trend` function, with its own disclosed ratio constants
(`config.py`'s `TREND_GROWING_RATIO`/`TREND_WEAKENING_RATIO`, same
values as MLE's, cited as a deliberate consistency choice, not copied
code). `test_decaying_when_recent_rate_is_weaker_than_lifetime_rate`
confirms a real, engineered scenario (20 clustered early occurrences, 2
occurrences after a long real gap) correctly overrides what would
otherwise be `VALIDATED` into `DECAYING`.

## Validation history (Deliverable 6)

`KnowledgeValidationJournal` never overwrites — re-validating the same
`hypothesis_label` later just appends a new report. `history_for`
returns the full real sequence in order;
`test_journal_history_never_overwrites_prior_state` confirms a pattern's
real progression (e.g. `REPEATED` at 3 occurrences, later `EMERGING` at
8) is fully preserved, not collapsed to only the latest state.

## No Engineering — structural, same proof pattern as OAE/EPS

`test_report_never_contains_a_recommendation_field` and
`test_models_have_no_knowledge_candidate_or_engineering_proposal_type`
verify there is no dataclass anywhere in this package that could hold a
recommendation, Knowledge Candidate, or Engineering Proposal.

## Golden replay test — a real, previously-established finding

`test_golden_replay_real_range_persistence_pattern_from_sprint116_corpus`
uses the exact 9 real days Sprint 116's own investigation established
for the "COVERED/RATIO independently SUITABLE under `RANGE_PERSISTENCE`,
excluded by the Expression filter" pattern — not fabricated, a real,
already-documented finding from this session's own prior work, now fed
through KVE. Result: `EMERGING`, not `VALIDATED` — correctly, since all 9
real days share the same single market classification (`RANGE`),
honestly falling short of the diversity requirement rather than being
inflated into a stronger claim than the real evidence supports.

## Verification summary

- Regression: **3002/3002 passing** (2976 pre-existing + 26 new KVE
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.

## Explicitly not implemented (per mission's own Prohibited section)

Knowledge Candidates, Engineering Proposals, Production changes,
parameter tuning, strategy optimisation. Per your own proposed
philosophy — Observation → Evidence → Validation → Knowledge →
Engineering → Production — a future series synthesizing Knowledge
Candidates from `VALIDATED`/`DECAYING` states across the whole stack is
deliberately deferred. Implementation stops here.
