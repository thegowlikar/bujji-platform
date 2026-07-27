# Series 109 — Dynamic Rolling & Strategy Transition Intelligence

## 0. Scope

`bujji/msi_dynamic_management/` consumes, read-only: `msi_position_lifecycle`
(Series 96), `msi_strategy_optimization` (Series 108), and
`msi_volatility_structure` (Series 88) — all frozen, unmodified. It adds
six INDEPENDENT decision assessments per position per day (Deliverable
3), each with an evidence-computed priority (Deliverable 4), a
representable-only strategy transition engine (Deliverable 5), and a
five-facet explanation for every recommendation (Deliverable 7). No
frozen file was edited — confirmed by the full regression suite's
pre-sprint test count remaining unchanged. See Section 9 for what this
sprint can and cannot yet honestly answer.

## 1. Literature review (Deliverable 1)

| Practice | Classification |
|---|---|
| Delta roll (rolling a strike when its delta has moved materially from entry) | **Universal principle** — delta as a probability proxy for strike pressure is textbook. The EXACT trigger delta is discretionary (see below). |
| Gamma roll (rolling to reduce gamma exposure as expiry approaches) | **Universal principle** — gamma acceleration near expiry is a physical fact of option pricing (1/√t scaling). How aggressively to act on it is style-specific. |
| Theta roll (rolling to a new expiry to re-capture time decay) | **Universal concept**; the specific DTE cutoff (this package uses 21, reused from Series 108, itself disclosed as tastytrade-style) is discretionary. |
| DTE roll | Same as theta roll — the concept (avoid holding through the final days' gamma/pin-risk window) is broadly cited; the exact day count is not universal. |
| IV expansion roll (widening wings/reducing size when IV expands) | **Discretionary** — some desks widen, others reduce size, others do nothing until the position is actually tested. This package only DETECTS a regime change (Series 88's own real taxonomy) and flags it as evidence; it does not prescribe widen-vs-narrow, which is exactly why Wing Adjustment's priority is at most RECOMMENDED, never MANDATORY, on a regime change alone. |
| IV contraction roll (narrowing wings / adding size when IV contracts) | **Discretionary**, same reasoning as above — the regime-change signal is symmetric in this implementation; it does not distinguish "good" vs "bad" contraction, since that judgment is itself a style choice outside this sprint's scope. |
| Tested-side adjustment (react to the SIDE under pressure) | **Universal concept** (a tested strike is a real, observable event); this package's `assess_strike_roll` reacts to the tested SHORT leg's own delta — it does not yet distinguish "roll the tested side" from "roll the untested side toward it" (a specific, style-specific construction technique — see Section 9's disclosed gap). |
| Untested-side adjustment | **Style-specific** — rolling the untested side in toward the tested side to collect additional credit is one specific, well-documented premium-selling technique (not universal — some desks simply roll the tested side out). Not implemented as a distinct decision type in this sprint; `strike_roll`'s WHETHER answer does not yet extend to WHICH side, a disclosed gap (Section 9). |
| Rolling winners (taking profit and re-establishing a similar position) | **Style-specific** — this package's `full_exit` assessment reaches OPTIONAL on `PROFIT_HARVEST` (Series 96's own real state), reflecting that harvesting a winner is a genuine, evidenced option, not a universal mandate to always re-enter. |
| Rolling losers (extending a losing position rather than realizing the loss) | **Explicitly NOT encoded as a default here** — this package's `roll_whole_strategy`/`roll_strike`/`roll_expiry` all require `thesis_invalidation.compatible=True` (the thesis must still hold); a losing position with a BROKEN thesis routes to `full_exit`, never to a roll, avoiding the well-documented behavioral trap of "rolling a loser" to avoid realizing a loss when the underlying premise has actually failed. |
| Exit versus roll | **Universal principle, cited across all professional risk-management literature**: the thesis, not the P&L, should determine roll vs. exit. This is the CENTRAL organizing principle of this package's priority logic (Section 3) — every roll-type decision checks thesis compatibility FIRST and routes to AVOID (deferring to `full_exit`) when it is broken. |

## 2. Package (Deliverable 2)

```
bujji/msi_dynamic_management/
  __init__.py, taxonomy.py, config.py, models.py,
  engine.py, serialization.py, query.py, runner.py, journal.py
```

## 3. Six independent decisions (Deliverable 3) — verified never combined

`DecisionAssessment` for each of `STRIKE_ROLL`, `EXPIRY_ROLL`,
`DELTA_REBALANCE`, `WING_ADJUSTMENT`, `STRATEGY_CONVERSION`,
`FULL_EXIT` — each with its OWN `assessment_id` (a real content hash),
own `recommended` boolean, own `priority`, own five-facet `Explanation`.
Verified: `test_six_decision_types_are_produced_independently_never_combined`
confirms 6 distinct decision types with 6 distinct assessment_ids from
one board call — no merging.

Real example (position tested, thesis intact, plenty of DTE, stable
regime, portfolio Greeks within tolerance): only `STRIKE_ROLL` reaches
`RECOMMENDED`; all five other decision types independently reach
`AVOID` — each for its own, separately-evidenced reason, not because
one decision "won."

## 4. Priority — evidence-computed, never fixed (Deliverable 4)

**No lookup table maps decision-type → priority.** The SAME decision
type reaches different priorities on different real evidence — verified
directly: `test_priority_is_evidence_computed_not_fixed` shows
`STRIKE_ROLL` at `AVOID` (delta=0.10), `RECOMMENDED` (delta=0.40, at
Series 108's own tested threshold), and `MANDATORY` (delta=0.55, past
the "more likely ITM than OTM" line) — three different priorities for
the identical decision type, purely as a function of the real delta
value.

Priority bands, each disclosed and structural (never tuned against any
return series): `STRIKE_ROLL`/`EXPIRY_ROLL` use Series 108's own
RECOMMENDED-level thresholds (reused by identity) plus two new,
disclosed bands (MANDATORY, OPTIONAL) either side. `DELTA_REBALANCE`
reuses Series 96's own real `WATCH_ABS_PORTFOLIO_DELTA`/`VEGA` (RECOMMENDED)
and `adjustment_policy.fired` (MANDATORY) — no new numeric threshold
invented. `WING_ADJUSTMENT` uses a real regime-LABEL change (Series 88's
own taxonomy) as its only trigger — no numeric IV threshold invented
here at all. `STRATEGY_CONVERSION` is gated by Series 108's own
`roll_whole_strategy` signal, then RECOMMENDED only if a representable
target exists, OPTIONAL (not silently dropped) if the structural
mismatch is real but no representable transition exists. `FULL_EXIT`
reaches MANDATORY only when a broken thesis is COMPOUNDED by a fired
hard exposure trigger — a broken thesis alone is RECOMMENDED, not
MANDATORY, since exit timing within the same session still has
discretion the evidence doesn't remove.

## 5. Strategy Transition Engine (Deliverable 5)

Only recommends transitions representable in `ALL_STRATEGY_FAMILIES`
(frozen, Series 89). `TRANSITION_RULES` (this package) plus
`msi_strategy_optimization.CONVERSION_RULES` (Series 108, reused by
identity) together cover 6 real pairs: `IRON_CONDOR→BUTTERFLY`,
`RATIO→SHORT_DIRECTIONAL`, `CALENDAR→IRON_FLY` (Series 108) and
`LONG_DIRECTIONAL→CALENDAR`, `NEUTRAL_PREMIUM_SELLING→IRON_CONDOR`,
`NEUTRAL_PREMIUM_BUYING→RATIO` (this sprint, mapping the user's own
"Vertical→Calendar"/"Short Premium→Defined Risk"/"Long Premium→Ratio"
category-level examples to one concrete representative real pair each —
disclosed explicitly, not a full category mapping, since the frozen
taxonomy has no category-level grouping to represent one).

**Verified honest reporting for a non-representable request:**
`test_transition_reports_non_representable_honestly` confirms
`VOLATILITY_EXPANSION` (no rule targets it) returns
`representable=False, to_family=None`, with an explicit "no
representable transition rule exists" reason — never an approximated
guess.

## 6. Rolling simulation (Deliverable 6) — real, measured, no
profitability claim

Run across all 41 real Bhavcopy-covered days, extending the frozen
`SessionDriver`/`run_full_cadence` batch-replay loop, with REAL
per-position current strike delta computed by repricing the position's
actual entered `HeldLeg`s against each subsequent day's real chain
(reusing `_build_strike_evidence`, Series 90/108, directly — closing
this sprint's own predecessor's disclosed measurement gap from Series
108 Section 9).

```
Tracked position-days: 4

Roll/adjustment/exit OPPORTUNITY frequency (priority MANDATORY or RECOMMENDED):
  FULL_EXIT: 3
  DELTA_REBALANCE: 2
  EXPIRY_ROLL: 1
  WING_ADJUSTMENT: 1

Full priority distribution:
  DELTA_REBALANCE: AVOID=2, MANDATORY=2
  EXPIRY_ROLL: AVOID=3, MANDATORY=1
  FULL_EXIT: MANDATORY=2, OPTIONAL=1, RECOMMENDED=1
  STRATEGY_CONVERSION: AVOID=4
  STRIKE_ROLL: AVOID=4
  WING_ADJUSTMENT: AVOID=3, RECOMMENDED=1

Strategy transition representability: {representable: 3, not representable: 1}
Exit-recommended position-days: 3/4
```

No profitability was measured anywhere in this run, per Deliverable 6's
own explicit instruction. The small sample (4 position-days) directly
reflects this corpus's own already-disclosed low admission rate (Series
91/99/100: only 11/41 days historically approved a trade) — not a
limitation introduced by this sprint.

## 7. Explainability (Deliverable 7) — verified, not just claimed

Every `DecisionAssessment.explanation` carries five distinct, non-empty
fields: `why`, `why_now`, `why_not_later`, `why_not_another_roll`,
`why_not_exit` — verified across all six decision types simultaneously
by `test_every_decision_explains_all_five_required_facets`. A decision
that is `AVOID` still explains itself (e.g. `why_not_exit` for a healthy
strike roll: "thesis remains compatible; the STRIKE, not the thesis, is
under pressure") — explanation is never conditional on the decision
being actionable.

## 8. Stability (Deliverable 8) — real, measured

The full 41-day rolling simulation (Section 6) was run TWICE,
independently, end-to-end. Result: **all 24 real decision assessment_ids
were byte-identical across both runs; the full priority distribution
was identical.** Confirmed at the unit level too:
`test_rerun_is_byte_identical_ids_and_explanations` asserts
`assessment_id`, `priority`, AND the full `Explanation` object
(dataclass equality, all five facets) are identical across two isolated
calls with identical inputs.

## 9. Operational readiness (Deliverable 9)

| Question | Can BUJJI answer it now, with evidence? |
|---|---|
| Why did we roll? | **Yes** — the firing decision's `explanation.why` cites the specific real evidence (e.g. `|delta|=0.55 >= STRIKE_ROLL_MANDATORY_DELTA=0.5`). |
| Why didn't we roll? | **Yes** — every non-firing decision (AVOID/OPTIONAL) still carries a populated `why` (verified, Section 7) — e.g. "thesis is broken -- rolling a strike ... just re-tests the same failed idea." |
| Why this adjustment? | **Yes**, for the six decision types this sprint defines. |
| Why not another adjustment? | **Yes, at the type level** — every decision's `why_not_another_roll` states it was evaluated independently of the other five. **Not yet at the WITHIN-type level** — e.g. `strike_roll` cannot yet say "roll to strike X specifically, not strike Y" (see the disclosed gap below). |
| Why exit now? | **Yes** — `full_exit`'s five-facet explanation, including the MANDATORY-vs-RECOMMENDED distinction (broken thesis alone vs. broken thesis + fired hard trigger). |

**Two real, disclosed gaps, not papered over:**
1. **Tested-side vs. untested-side adjustment (Deliverable 1's own
   distinction) is not yet implemented.** `assess_strike_roll` answers
   WHETHER to roll the tested strike; it does not yet decide WHICH leg
   (tested side out, or untested side in) to actually move — that is a
   specific construction technique requiring re-invocation of Trade
   Construction (Series 90, frozen), out of this sprint's scope.
2. **"What exactly should I roll TO" remains open**, same root cause as
   Series 108's own disclosed gap (Section 9 there): a firing
   `strike_roll`/`expiry_roll` says WHETHER, not the new strike/expiry
   itself, which requires a real re-optimisation call this sprint does
   not wire in (Series 108's `optimize_strikes`/`optimize_expiry` COULD
   supply this, but composing them here was out of this sprint's scope
   — a concrete, small follow-up, not a fundamental blocker).

## 10. Recommendation (Deliverable 10)

**Needs Additional Evidence** — not "Ready for Live Shadow Validation,"
supported directly by the Section 6/8 measurements, not opinion:

- The decision LOGIC is real, deterministic (Section 8: 24/24 identical
  across two full runs), evidence-driven (Section 4), and fully
  explainable (Section 7) — these are genuine, measured strengths.
- But Section 6's real corpus produced only **4 tracked position-days**
  across 41 real trading days — far too small a sample to say the
  roll/exit/conversion LOGIC has been exercised across a representative
  range of real market conditions (only 2 `DELTA_REBALANCE MANDATORY`
  events, 1 `EXPIRY_ROLL MANDATORY` event, 1 `WING_ADJUSTMENT
  RECOMMENDED` event were observed in total — each decision type fired
  at most once or twice). This mirrors the same small-sample caveat this
  project has honestly disclosed since Series 101's own performance
  analytics (`MIN_RELIABLE_SAMPLE_SIZE=30`, not met here either).
- The two disclosed gaps in Section 9 (tested-vs-untested-side
  adjustment, "roll to what new strike/expiry") mean 2 of the 5 named
  Deliverable 9 questions are not yet fully answerable — real, honest
  gaps, not fabricated as closed.

**What "Needs Additional Evidence" concretely means here:** not more
decision logic (the six-decision/priority/explanation architecture
itself is sound and tested) — more REAL trading days with REAL open
positions to exercise it against, exactly the evidence-accumulation
phase Sprint 102 already scoped (Gate A: ≥100 trades) and which this
project's own next phase (live shadow operation, Sprint 107) is
positioned to produce. This sprint's own code is not the blocker;
volume of real, tracked position-days is.
