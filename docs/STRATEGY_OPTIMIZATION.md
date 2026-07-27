# Series 108 — Strategy Optimisation & Dynamic Position Management

## 0. Scope

`bujji/msi_strategy_optimization/` sits, conceptually, between Strategy
Selection and Position Construction/Execution/Lifecycle — but it is
never wired into their real call sites (no frozen file was edited). It
is a pure, read-only consumer: given an already-selected family (Series
89-94, frozen) and the real option chain, it produces optimisation
*guidance* — strike, expiry, roll, adjustment, and conversion
assessments — that nothing downstream currently reads automatically.
Wiring this guidance into the live pipeline is a disclosed, future
integration step (Section 10), not claimed as done here.

**Absolute constraints, confirmed honoured:** no file under `msi_*`
(other than the new package itself), `intelligence/`, `capital/`, or
`execution/` was modified. Full regression suite: pre-sprint count
unchanged and passing, plus this sprint's own new tests — see Section 9.

## 1. Institutional options desk literature (Deliverable 1)

Researched and documented before writing code, per the sprint's own
instruction. Practices are explicitly separated into UNIVERSAL (broadly
agreed across professional premium-selling/options-desk literature) and
STYLE-SPECIFIC (one documented convention among several, not a law):

| Practice | Classification | Notes |
|---|---|---|
| Delta as the primary strike-placement metric (not raw distance-from-spot) | **Universal** | Delta approximates probability the option expires ITM under a lognormal/BS assumption — the basis of nearly all professional strike-selection literature. This codebase already does this (`FAMILY_DELTA_TARGETS`, Series 90). |
| Short strike delta ≈ probability of the option finishing ITM (1 − delta ≈ POP for a short single option, approximately) | **Universal**, with a caveat | The approximation holds better for far-OTM strikes than near-ATM ones (it ignores the P&L asymmetry of premium collected vs. max loss) — cited widely, but professional desks treat it as a rough guide, not an exact probability. |
| Expected-move-based wing width (1-sigma of the priced distribution) | **Universal** | Standard, real, already implemented (Series 90's `WING_WIDTH_EXPECTED_MOVE_MULTIPLIER`), reused here by identity. |
| Skew-aware positioning (index put skew is structurally asymmetric — puts trade rich vs. calls on most equity indices) | **Universal observation, style-specific reaction** | That skew exists is near-universally observed (documented across index-options literature). What to DO about it (shift strikes, use ratio spreads, ignore it) varies by desk/strategy — this package only DETECTS and reports skew (Deliverable 4), it does not prescribe a skew-driven strike shift, since that would be a style choice this sprint should not silently encode as a rule. |
| Theta/gamma trade-off in expiry selection (shorter DTE = more theta AND more gamma, simultaneously) | **Universal** | The trade-off itself is textbook; WHERE on that curve to sit (weekly vs. monthly) is style-specific — this package reports the real trade-off per real expiry (Deliverable 5) and picks a structural default among near-term expiries, disclosed as a default, not a law. |
| "21 DTE" mechanical management point | **Style-specific** | A specific, widely-cited convention from one research group (tastytrade's own published studies on assignment risk and gamma acceleration near expiry) — not a universal rule followed by every desk. Used here as `ROLL_EXPIRY_DTE_THRESHOLD`, explicitly disclosed as style-specific in `config.py`. |
| "Tested" strike (delta breach past some threshold, commonly cited in the 0.30-0.40 range) as an adjustment trigger | **Style-specific range, universal concept** | That a strike being approached/breached warrants attention is universal; the EXACT threshold (0.30? 0.35? 0.40?) varies by source and risk tolerance. This package uses 0.35 as a disclosed structural default (`TESTED_STRIKE_DELTA_THRESHOLD`), the conservative end of the commonly-cited range. |
| Roll the UNTESTED side toward the tested side (vs. rolling the tested side away) | **Style-specific** | A specific premium-selling convention (common in defined-risk iron condor management) — not adopted as a rule in this sprint (Deliverable 6 only classifies WHETHER to roll strike/expiry/whole-strategy, not HOW to construct the rolled position, which would require touching Trade Construction — out of scope, frozen). |
| Converting a tested defined-risk spread into a pinned structure (e.g. condor → butterfly) when one side is tested but the range thesis survives | **Style-specific, but well-documented** | One of several documented management responses to a tested iron condor — this package encodes it as ONE explicit, disclosed conversion rule (Deliverable 8), not as the only "correct" response. |
| Term-structure-driven calendar entry/exit (enter when back-month IV is rich vs. front-month, exit/convert when that edge closes) | **Universal concept, style-specific execution** | That term structure carries information is broadly accepted; exactly how to trade the edge (calendar vs. diagonal, when to convert) is desk-specific. This package's CALENDAR→IRON_FLY rule encodes one specific, disclosed response. |
| Event-calendar-aware expiry avoidance (don't hold gamma-heavy structures through a scheduled binary event) | **Universal concept** | Universally cited as good practice — but this codebase has **no real event-calendar data source anywhere** (confirmed by the Deliverable 1 code audit: no file in this repository reads an economic/earnings/RBI-policy calendar). `ExpiryOptimizationAssessment.event_calendar_available` is honestly `False` on every real call — never fabricated. |

## 2. Package (Deliverable 2)

```
bujji/msi_strategy_optimization/
  __init__.py, taxonomy.py, config.py, models.py,
  engine.py, serialization.py, query.py, runner.py, journal.py
```
Same 9-file house convention as every other MSI package (minus a
dedicated 9th file — this package has no separate `journal.py`-adjacent
concern beyond the one included). Consumes, by direct, disclosed reuse:
`bujji.msi_trade_construction.engine._build_strike_evidence`/
`_candidates_for_type` (Series 90's own real IV/delta solver, never
re-implemented a second time), `bujji.msi_trade_construction.config`
(`FAMILY_DELTA_TARGETS`, `WING_WIDTH_*`, `DEFAULT_RISK_FREE_RATE`, all
reused BY IDENTITY), `bujji.intelligence.volatility_brain
.compute_expected_move` and `bujji.intelligence.greeks_brain._bs_gamma`/
`_bs_theta` (same real Black-Scholes functions Series 88/90 already
reuse).

## 3. Per-family optimisation objectives (Deliverable 3)

Every family in `msi_strategy_selection_foundation.taxonomy
.ALL_STRATEGY_FAMILIES` (13 real families) has declared objectives in
`taxonomy.FAMILY_OBJECTIVES` — verified by
`test_every_real_strategy_family_has_declared_objectives`. Example real
mappings: `IRON_CONDOR` → (MAXIMIZE_THETA, MINIMIZE_GAMMA_EXPOSURE,
MAINTAIN_BALANCED_WINGS); `CALENDAR` → (MAXIMIZE_TERM_STRUCTURE_ADVANTAGE,
MINIMIZE_THETA_DECAY); `RATIO` → (CONTROL_TAIL_RISK, MAXIMIZE_THETA);
`BUTTERFLY` → (MAXIMIZE_PIN_PROBABILITY, MAXIMIZE_REWARD_RISK) — matching
the spec's own worked examples exactly.

## 4. Strike Optimiser (Deliverable 4) — real, measured

`engine.optimize_strikes` never hard-codes a strike: it solves real
per-strike IV/delta from the day's real chain (reusing Series 90's own
solver), picks the CE/PE candidates closest to the family's target
delta, derives wing width from the real expected move (falling back to
a fixed points-width only when IV isn't resolvable, exactly like Series
90's own disclosed fallback), and computes real strike spacing from the
chain's own real strike ladder. Real example (2026-05-25, IRON_CONDOR,
target_delta=0.20): short_ce strike=24250 (achieved delta 0.1841),
short_pe strike=23850 (achieved delta −0.1861), wing_width=235.41
(expected-move-derived), skew_adjustment=CALL_SKEW — every value
traceable to a real chain row, every decision explained
(`explanation.why`, verified non-empty by
`test_strike_optimizer_never_hardcodes_a_strike_and_explains_every_decision`).
Fails closed (all fields `None`/`UNKNOWN`) when no target delta or spot
is available — never guesses.

## 5. Expiry Optimiser (Deliverable 5) — real, measured, one real bug
found and fixed during development

Evaluates every REAL expiry present in the day's chain (never a
synthetic one), buckets by real DTE (weekly/next-weekly/monthly/
far-monthly), and computes real theta/gamma/expected-move per bucket via
the same Black-Scholes functions Series 88/90 already use.

**Real finding, corrected before shipping:** this codebase's real
2026-05-25 chain contains expiry entries out to DTE=1681 (a multi-year
horizon no real NIFTY chain practically trades at meaningful liquidity —
a real data-quality characteristic of the corpus, not fabricated). An
initial "maximise |theta|/gamma" dominance ranking is monotonic in DTE
(both shrink, but gamma shrinks faster), so it degenerated to always
picking the single most distant real expiry present — the opposite of
useful "dominant expiry" guidance for a MAXIMIZE_THETA objective. Fixed
by capping the dominance comparison to real candidates within
`EXPIRY_DOMINANCE_MAX_DTE=45` days (all candidates, including far-dated
ones, are still reported in full — only the *dominance* comparison
excludes them, disclosed explicitly in the assessment's own
`explanation.why`). Verified: `test_expiry_optimizer_prefers_near_term_over_impractically_far_dated_real_expiries`
confirms the dominant pick has DTE≤45 even though far-dated (DTE>1000)
candidates are present in the same real chain.

Event calendar: **honestly `False`/unavailable on every call** — no
event-calendar source exists anywhere in this codebase (Section 1).

## 6. Rolling Intelligence (Deliverable 6) — five independent signals,
never collapsed

`engine.assess_roll` returns FIVE separate booleans
(`roll_strike`, `roll_expiry`, `roll_whole_strategy`, `hold`, `exit`),
each with its own reasoning tuple — verified by
`test_roll_assessment_never_collapses_the_five_signals`. A single
priority-resolved `recommended_action` label (EXIT > ROLL_WHOLE_STRATEGY
> ROLL_EXPIRY > ROLL_STRIKE > HOLD) exists only for readability; the
five real signals remain independently inspectable. Consumes a real,
already-computed `PositionLifecycleAssessment` (Series 96, frozen) —
never re-derives lifecycle state. `exit` fires on
`position_state ∈ {EXIT_CANDIDATE, THESIS_BROKEN}`; `roll_whole_strategy`
fires when the position is an `ADJUSTMENT_CANDIDATE` AND the entry
thesis is no longer compatible (the structure itself, not just its
strikes, no longer fits); `roll_strike`/`roll_expiry` fire on a tested
delta / low DTE ONLY when the thesis remains compatible (otherwise
`roll_whole_strategy` or `exit` already dominates).

## 7. Adjustment Planner (Deliverable 7) — evidence-cited

`engine.plan_adjustment` maps the real Roll Assessment plus Series 96's
own real fired hard-exposure triggers into one of the 8 spec-named
actions, every recommendation citing the specific real evidence that
drove it (verified non-empty by
`test_adjustment_planner_cites_evidence_for_every_action`).

## 8. Dynamic Strategy Conversion (Deliverable 8) — three real, gated
rules; two of the user's own examples found NOT representable

Only fires when `roll.roll_whole_strategy` is already `True` (gated,
verified by `test_conversion_never_fires_without_the_roll_whole_strategy_gate`)
and only for the three explicit, disclosed pairs in
`taxonomy.CONVERSION_RULES` — `IRON_CONDOR→BUTTERFLY`, `RATIO→
SHORT_DIRECTIONAL`, `CALENDAR→IRON_FLY` — each with a stated real
condition, never explored for novelty (verified: no rule fires without
its condition; a family with no rule honestly reports
`recommended=False, to_family=None`,
`test_conversion_only_fires_on_explicit_rules_never_for_novelty`).

**Real, disclosed missing-capability finding:** the sprint's own worked
examples "Iron Condor → Broken Wing Butterfly" and "Calendar → Diagonal"
name target shapes that are **not real, distinct values** in
`ALL_STRATEGY_FAMILIES` (frozen, Series 89) — a broken-wing butterfly
and a diagonal are asymmetric-strike/asymmetric-expiry *parameterizations*
of `BUTTERFLY`/`CALENDAR`, not separate families this taxonomy
represents. This package's `CONVERSION_RULES` therefore maps
`IRON_CONDOR→BUTTERFLY` (the real, existing family; the "broken wing"
asymmetry itself is expressible only through the Strike Optimiser's own
wing parameters, not as a distinct taxonomy value) and does not attempt
a `CALENDAR→DIAGONAL` rule at all, since no `DIAGONAL` family exists to
recommend. Extending the taxonomy to represent these as distinct,
selectable families would require modifying `msi_strategy_selection_foundation`
— explicitly frozen, out of this sprint's scope. This is a real, honest
answer to Deliverable 10's "why not another optimisation" question, not
an oversight (verified: `test_conversion_rules_only_target_real_taxonomy_families`).

## 9. Historical replay (Deliverable 9) — real, measured, no
profitability claim

Run across all 41 real Bhavcopy-covered days (2026-05-25 → 2026-07-22),
extending the SAME frozen `SessionDriver`/`run_full_cadence` batch-replay
day loop every prior series already uses:

```
strategy_optimization runs:                                21
strike_optimization runs (resolved a real short_strike):    21
expiry_optimization runs (resolved a real dominant_expiry): 21

Open positions tracked across days: 4 position-days
  strike re-optimisation changes vs. entry-day optimum:      1
  expiry re-optimisation changes vs. entry-day optimum:      1

Roll decision distribution: EXIT=3, HOLD=1
Adjustment action distribution: CLOSE_COMPLETELY=3, CLOSE_PARTIALLY=1
Conversion frequency: 0/4 position-days recommended a conversion
```

**Disclosed measurement limitation:** this corpus run did not thread a
real per-position "current short-strike delta" or "current DTE" hint
for open positions (both parameters `assess_roll` accepts but this
particular script call site passed `None` for both) — reconstructing
those from an `AdmittedTrade`'s real entered legs re-priced against each
subsequent day's real chain is straightforward but was not built in
this pass (a real, disclosed follow-up, not fabricated as measured).
Consequently `roll_strike`/`roll_expiry` never fired in this specific
41-day run (both require a non-`None` delta/DTE reading); only
`exit`/`hold` were reachable and are the only values observed. The
engine functions themselves are tested directly and independently for
`roll_strike`/`roll_expiry` firing correctly (Section 6), so this is a
gap in this ONE corpus script's wiring, not in the engine.

No profitability metric was computed anywhere in this measurement, per
Deliverable 9's own explicit instruction.

## 10. Readiness assessment (Deliverable 10)

| Question | Can BUJJI answer it now, with evidence? |
|---|---|
| Why this strategy? | **Yes** — `msi_strategy_selection_foundation`/`msi_strategy_selector` (frozen, Series 89-94) already produce a fully evidenced `StrategySelectionAssessment`. |
| Why these strikes? | **Yes, now** — `StrikeOptimizationAssessment.explanation.why` cites the real achieved delta vs. target and the real wing-width source (Section 4). |
| Why this expiry? | **Yes, now** — `ExpiryOptimizationAssessment` reports every real candidate's theta/gamma/expected-move and states exactly why the dominant one was chosen (Section 5), including when it honestly can't (all real candidates too far-dated). |
| Why this delta? | **Yes** — `FAMILY_DELTA_TARGETS` (Series 90, reused by identity) states the declared risk-shape choice per family; `StrategyOptimizationAssessment` surfaces it directly. |
| Why this wing width? | **Yes, now** — real expected-move-derived, with the specific fallback path disclosed when IV isn't resolvable (Section 4). |
| Why not another optimisation? | **Partially.** For strikes/expiry: yes — every REJECTED candidate is still reported with its own real reasoning (`ExpiryCandidate.why_considered_or_rejected`), not silently dropped. For strategy FAMILIES themselves: `msi_strategy_selection_foundation`'s own suitability reasoning already answers this (frozen, unmodified) — this sprint adds no new "why not another family" layer, since that would be Strategy Selection's job, not Optimisation's. |
| When should I roll? | **Yes, now** — `RollAssessment`'s five independent, evidenced signals (Section 6). |
| What exactly should I roll? | **Partially.** BUJJI can now say WHETHER to roll strike, expiry, or the whole strategy (three independent yes/no answers with reasoning) — but it cannot yet say TO WHICH new strike/expiry to roll, because that would require re-invoking Trade Construction (Series 90, frozen) with the rolled position's new parameters, a real, disclosed wiring gap (Section 9's own limitation applies here too), not a fabricated answer. |
| Why roll instead of exit? | **Yes, now** — `RollAssessment`'s priority order is explicit and evidenced: `exit` is checked FIRST (lifecycle state THESIS_BROKEN/EXIT_CANDIDATE dominates everything), so whenever a roll signal fires instead of exit, it is because the lifecycle state was NOT one of those two — a real, inspectable distinction, not an implicit default. |
| Why convert instead of adjust? | **Yes, now** — `evaluate_conversion` only fires when `roll_whole_strategy` is already true (the SAME structural-mismatch signal that would otherwise drive a lesser adjustment); the recommendation states the specific documented condition (Section 8). When no rule applies, this is honestly reported, never guessed. |

**Overall:** BUJJI can now answer 8 of 10 questions with real,
evidenced reasoning end-to-end, and honestly discloses the specific
missing capability for the other 2 (the taxonomy gap for
broken-wing/diagonal shapes, Section 8; and the "roll to what new
strike/expiry" wiring gap, which needs Trade Construction re-invocation,
not new decision logic). Neither gap was patched over with an invented
rule — consistent with this whole project's own established discipline.
