# Engineering Evidence Board (EEB) — Series 106 Architecture

## What this is

EEB is the final layer of the learning architecture — the one place
where accumulated, real, cross-series evidence gets an explicit,
auditable governance decision about whether it's ready for a human to
start designing an engineering proposal. It does not modify Production,
does not write code, and does not recommend implementation. Every real
hypothesis review receives exactly one of five decisions:
`INSUFFICIENT_EVIDENCE`, `CONTINUE_OBSERVING`,
`READY_FOR_ENGINEERING_REVIEW`, `SUPERSEDED`, `ARCHIVED`.

## The most important sentence in the whole package

`READY_FOR_ENGINEERING_REVIEW` does not mean "implement this." It means
only that sufficient evidence exists to justify *designing* a controlled
engineering proposal. This is enforced both structurally (no field
anywhere in `models.py` could hold code, a parameter value, or an
implementation instruction — `test_report_never_contains_a_code_or_
parameter_field`) and textually (the disclosed reasoning explicitly says
so — `test_ready_for_engineering_review_never_means_implement`).

## Isolation — same strictest tier as OAE/KVE

Zero imports from any other bujji package, anywhere in this package
(`test_eeb_imports_nothing_from_any_other_bujji_package`). All inputs
arrive via local `*View` translations (`KnowledgeValidationView`,
`OpportunityAssessmentRef`) — the same sibling-isolation convention
Series 104/105 established. Additionally, per the mission's explicit "No
code generation" prohibition, this package is the first to add a
structural check that no file anywhere in it can even call
`compile`/`exec`/`eval` (`test_no_file_in_eeb_can_generate_or_execute_
code`) — a defense-in-depth check no prior series needed, since none of
them were governance-adjacent enough for code generation to even be a
plausible risk.

## Three real decision paths, five real outcomes

`review_evidence()` — the pure, automatic path — can only ever produce
three of the five decisions:

1. `INSUFFICIENT_EVIDENCE` — `validation_state` is `NOT_OBSERVED`,
   `OBSERVED`, `REPEATED`, or `INVALIDATED` (too few real occurrences, or
   directly contradicted by its own majority evidence per Series 105).
2. `CONTINUE_OBSERVING` — any of: real, disclosed contradictory
   observations exist beyond what Series 105 measured; `DECAYING` (a
   real weakening trend, even with a high occurrence count —
   test-verified explicitly); `EMERGING` (real evidence still
   accumulating); or `VALIDATED` but with no real, *positive*
   Opportunity Assessment referenced (`OPPORTUNITY_IDENTIFIED`/
   `GOOD_TRADE_TAKEN`) — a validated market pattern alone isn't enough
   without real evidence of a genuine actionable opportunity.
3. `READY_FOR_ENGINEERING_REVIEW` — only when `VALIDATED`, zero
   contradictions, and at least one real positive Opportunity Assessment.

`ARCHIVED` and `SUPERSEDED` are **never** producible by
`review_evidence()` — verified structurally
(`test_review_evidence_never_assigns_archived_or_superseded`, sweeping
every real `validation_state`). They exist only via two explicit,
human-initiated functions:

- `archive(report, reason, timestamp)` — requires a real, non-empty
  reason (raises otherwise); produces a **new**, immutable report, never
  mutating the original (same correction-creates-a-new-record discipline
  as Series 101's `EvidencePacket`).
- `supersede(report, superseded_by_hypothesis_label, reason, timestamp)`
  — requires both a real reason and a real, named replacement hypothesis
  (raises otherwise); same immutability discipline.

## All eight real review criteria, disclosed every time

`evidence_strength`, `replay_reproducibility`,
`diversity_of_market_conditions`, `causal_validity`,
`opportunity_quality`, `evidence_trend`, `contradictory_evidence`,
`known_limitations` — every single one appears in
`explanation.criteria_evaluated` on every real report, verified by
`test_all_eight_review_criteria_are_disclosed_in_explanation`.
`known_limitations` is never silently empty: if the caller discloses
none, EEB adds a standard, honest scope note rather than presenting a
report that looks limitation-free.

## Governance history — never overwritten

`EngineeringEvidenceJournal` is append-only; re-reviewing, archiving, or
superseding a hypothesis just appends a new real report.
`test_journal_history_preserves_full_governance_trail` confirms a real
hypothesis's full lifecycle (`CONTINUE_OBSERVING` →
`READY_FOR_ENGINEERING_REVIEW` → `ARCHIVED`) survives intact in order —
governance requires the full auditable trail, not just a latest
snapshot.

## Golden replay test — carrying Series 105's own result one layer further

`test_golden_replay_real_range_persistence_pattern_stays_continue_
observing` feeds Series 105's own real, already-established result for
the Sprint 116 `RANGE_PERSISTENCE`/Expression-filter pattern (9 real
days, `EMERGING` state, single real market classification) into EEB.
Result: `CONTINUE_OBSERVING`, never `READY_FOR_ENGINEERING_REVIEW` — EEB
cannot promote evidence past what the layer beneath it already
established. This is the concrete proof that the whole stack's
information only ever flows upward honestly, never gets inflated at a
later stage.

## Verification summary

- Regression: **3032/3032 passing** (3002 pre-existing + 30 new EEB
  tests), zero pre-existing tests modified.
- Replay parity: **0 diffs** across all 41 real corpus days.

## The learning architecture is now complete, per this engagement's own stated intent

```
Market → Decision (99) → Evidence (101) → Counterfactual (102)
  → Market Classification (103) → Opportunity Assessment (104)
  → Knowledge Validation (105) → Engineering Evidence Board (106)
  → [human] Engineering Proposal → Replay Validation → Production
```

Every box above this line is real, tested, isolated, and read-only.
Nothing in it has ever touched Production. The one box below the line —
the first human Engineering Proposal that could ever influence
Production — is deliberately, permanently a separate, human decision.
Per the mission's own instruction and this engagement's own architectural
recommendation: implementation stops here.
