# Phase 15F -- First Trusted Consumer: Greeks + Premium Behaviour → Position Intelligence

## 1. Forensic findings

**What Position Intelligence knew before**: 3 checks (`direction`,
`volatility_trend`, `regime`), each comparing an entry-time categorical
state to a later one, scoped by strategy family via 4 explicit
frozensets (`_DIRECTION_SENSITIVE_FAMILIES` etc.). Verdict = `INTACT`
(no deviations) / `WEAKENING` (minority deviated) / `INVALIDATED`
(majority deviated) / `UNKNOWN` (nothing resolvable) -- purely a
function of how many checks land in each bucket.

**What it could NOT answer**: whether the position's actual *risk
exposure* (delta/gamma sensitivity) still matched the entry thesis, and
whether option *premium itself* was behaving in a way that corroborated
or contradicted the price-direction read.

**Key finding**: `ShadowTradeCandidate.legs[i].delta` already exists
(Phase 14 solved it per-leg at construction time) but nothing downstream
ever reads it -- another instance of "built but not connected," same
pattern discovered in Phase 15E. However, per-leg delta alone is
insufficient for a genuine Greeks baseline (no gamma/theta/vega/IV, no
combined premium) -- confirmed by direct inspection of
`ShadowTradeCandidate`'s full field list.

**Ownership boundary (Step 2), verified by source inspection**: none of
the 3 existing checks recompute direction/volatility/regime -- they
only read already-published fields (`market_direction.overall_direction`,
`volatility_structure.volatility_regime`, `market_state.regime`). The
same discipline was extended, not replaced: the 3 new checks only read
`greeks`/`premium_behaviour` -- both already-published, real fields
from Phase 15E -- never reimplementing VSB/MDI/MPPI logic.

## 2. Design

**Smallest additive baseline extension (Step 6)**: `ShadowTradeCandidate`
(Phase 14's protected model) was NOT touched. Instead,
`PositionEntrySnapshot` gained two new, defaulted fields
(`entry_greeks: Optional[dict] = None`, `entry_premium_behaviour: Optional[dict] = None`),
and `build_entry_snapshot()` gained one new, defaulted parameter
(`source_intelligence_cycle_record: Optional[dict] = None`) that
captures the SAME real cycle's own `"greeks"`/`"premium_behaviour"`
fields verbatim when supplied. Every pre-Phase-15F call site
(positional construction, no new args) hydrates safely as `None` ->
honest `UNKNOWN` in every new check, never a fabricated zero.

**3 new checks added** (all pure, all reading only already-published fields):
1. `_check_delta_exposure` (direction-sensitive families) -- compares
   `delta_ce + delta_pe` (a real, put-call-parity-grounded moneyness/
   direction proxy: `delta_ce - delta_pe == 1` always, so this sum
   equals `2*delta_ce - 1`) between entry and current. A
   `|bias| < 0.05` band near zero is honestly `UNKNOWN` (too close to
   neutral to call), not forced to a side.
2. `_check_premium_direction_confirmation` (direction-sensitive
   families) -- does the CURRENT premium's `ce_vs_pe_relative` reading
   corroborate the CURRENT price direction band? `SYMMETRIC` (no clear
   differential) is honestly `UNKNOWN`, per the mission's own example
   ("underlying moves but premium barely responds -> weakening/UNKNOWN").
3. `_check_premium_selling_pressure` (compression/range-dependent
   families) -- does combined premium direction favor or work against
   a premium-selling thesis (`FALLING`/`STEADY` = consistent, `RISING`
   = deviated, with an "accelerating" qualifier in the reason text when
   applicable).

No new thresholds were invented arbitrarily: the `0.05` neutral-delta
band is the only new bounded constant, explicitly documented and
justified (a delta bias that close to zero is genuinely ambiguous for
ANY threshold choice); every other check is a direct categorical
comparison or reuses Phase 15E's own already-defined categories
(`RISING`/`FALLING`/`CE_EXPANDING_FASTER`/etc.).

**Evidence fusion (Step 5)**: the 3 new checks were added to the SAME
existing `checks` tuple and the SAME existing deviated/resolved-ratio
verdict logic -- not a competing decision engine. This was a deliberate
design choice, not a shortcut: the existing logic already satisfies the
mission's stated invariants for free (`UNKNOWN` checks are excluded from
the deviated/resolved ratio, so `UNKNOWN` evidence can never by itself
force `INVALIDATED`; a single deviated check among several consistent
ones produces `WEAKENING`, never an arbitrary `EXIT`). One new field,
`evidence_confidence` (`NONE`/`LOW`/`MODERATE`/`HIGH`, purely a function
of resolved-check fraction), was added so a caller can see how much of
the verdict is actually grounded in resolved evidence versus mostly
`UNKNOWN`.

## 3. Real-data validation (Step 7)

Ran end-to-end against the real, complete `SHADOW-OBSERVATORY-2026-08-06`
session: 152 real (candidate-cycle -> later-cycle) pairs evaluated.

- **Usable Greeks: 0/174, usable Premium Behaviour: 0/174** -- a direct,
  expected consequence of Phase 15E's own finding (ATM bid/ask both real
  in only 1/174 CE, 3/174 PE cycles in this specific session).
- **Thesis verdict changed by the new evidence: 0/152** -- correct,
  since no usable evidence existed to change anything.
- **UNKNOWN correctly prevented any change: 105/152** -- the new checks
  degraded to `UNKNOWN` and the verdict matched what it would have been
  without Phase 15F's changes at all, every time.
- **Contradictory-evidence cases: 0/152** -- none arose in this
  session's real data (expected, since evidence was almost always
  absent rather than present-but-conflicting).

This is an honest, disclosed real-data result, not a failure: the
consumer's CORRECTNESS (never inventing a verdict from missing data) is
exactly what was proven, even though this specific session's market
data couldn't exercise the POSITIVE path (real evidence materially
changing a verdict). That positive path is covered by the semantic
fixtures below instead, clearly labeled as fixtures, not real-market
evidence.

## 4. Semantic fixture validation (Step 8) -- all 8 required scenarios

1. Strong thesis + supportive Greeks -> stays `INTACT` -- PASS
2. Strong thesis + adverse delta exposure -> `WEAKENING`/`INVALIDATED` (never silently `INTACT`) -- PASS
3. Strong thesis + 3-signal adverse evidence (direction + delta + premium) -> `INVALIDATED` -- PASS
4. `UNKNOWN` Greeks -> pre-existing direction check completely unaffected, same verdict with or without Greeks present -- PASS
5. `UNKNOWN` Premium Behaviour -> never forces invalidation -- PASS
6. Contradictory Greeks (consistent) + Premium (deviated) -> `WEAKENING`, never an arbitrary forced `EXIT` -- PASS
7. Recovery from `INVALIDATED` when evidence genuinely reverses (same entry, two independent, stateless evaluations) -- PASS
8. Backward-compatible old-style `PositionEntrySnapshot` (positional, no new fields) -> new checks honestly `UNKNOWN`, old checks unaffected -- PASS

Plus 9 additional tests: ownership-boundary checks (new checks absent
for out-of-scope families), `build_entry_snapshot` additive-param
behavior (including a record from before Phase 15E existed, with no
`"greeks"`/`"premium_behaviour"` keys at all -- degrades to `None`,
never `KeyError`), and `evidence_confidence` scaling. **17/17 fixture
tests passing.**

## 5. Recovery (Step 10)

**Determined nothing new needs persistence.** Position Intelligence
itself holds zero cross-cycle state (`evaluate_thesis` is a pure
function of its two inputs). Its two new inputs are already
restart-invariant: `ObservationMemory`/direction (Phase 15D, real-data
proven) and `PremiumBehaviourState` (Phase 15E, real-data proven at 5
real boundaries). Greeks need no state at all (`assess_atm_greeks` is
stateless per-cycle). Rather than assuming this transitively, one
integration test explicitly proves the COMPOSITION using real Session
B data at the boundary=87 restart point already used in Phase 15D/15E:
an uninterrupted replay and a killed-and-restarted-then-hydrated replay
produce **byte-identical** `ThesisEvaluation` results (status,
recommendation, evidence_confidence, and every individual check).

## 6. Safety (Step 9)

18 dedicated safety tests across two files (12 for Greeks/Premium
Behaviour themselves in Phase 15E, 6 new for the Position Intelligence
consumer): no forbidden imports/calls (AST-based), no broker/network/
capital/margin references, recommendation fields confirmed to be
advisory-text-only (no side-effecting call names in the module), and a
dedicated regression guard proving `UNKNOWN` Greeks/Premium Behaviour
produce the EXACT SAME verdict as pre-Phase-15F code would have. No new
documented exceptions were needed -- this phase touched no
pre-existing protected boundary.

## 7. Full regression

`4662 passed` (1 pre-existing, unrelated deprecation warning). Exact
counts by category: 17 fixture tests (Step 8), 6 safety tests (Step 9),
1 real-data restart-equivalence test (Step 10) -- **24 new Phase 15F
tests**, all passing, on top of the full pre-existing suite.

## 8. Consumer trust gate (Step 12)

**Can Position Intelligence now be trusted to consume Greeks/Premium
Behaviour? YES.**

- Contracts are explicit: ownership boundary documented and verified (checks never recompute market logic).
- `UNKNOWN` behavior is correct: proven by 3 dedicated fixtures + 1 safety regression guard.
- No duplicated market logic exists: confirmed by source inspection -- every new check only reads already-published fields.
- Real data has been exercised: yes, 152 real pairs, with an honestly disclosed 0% usable-evidence rate reflecting this specific session's known liquidity sparsity, not a code defect.
- Semantic fixtures cover the paths real data couldn't reach: all 8 required scenarios pass, clearly labeled as fixtures.
- Restart equivalence is proven: with real data, not merely assumed.
- Safety is proven: 18 tests, zero new exceptions needed.
- Full regression is green: 4662/4662.
- Explanations identify which evidence caused the verdict: every `ThesisCheck.reason` is a real, specific sentence citing the actual values compared.

The one caveat, disclosed rather than hidden: in the ONE real session
tested, this new evidence never had the data to materially matter. That
is a fact about this session's option-chain liquidity (already known
since Phase 14), not a flaw in the consumer's logic -- the fixture
suite exists precisely to prove correctness for the cases real data
couldn't reach yet.

## 9. Files changed

Modified (additive only):
- `bujji/position_intelligence/models.py` -- 2 new `PositionEntrySnapshot` fields, 1 new `ThesisEvaluation` field, all defaulted.
- `bujji/position_intelligence/engine.py` -- 3 new check functions, 1 new confidence helper, `build_entry_snapshot`'s new optional parameter, all additive.

New:
- `tests/test_position_intelligence_greeks_premium.py` (17 fixture tests)
- `tests/test_position_intelligence_greeks_premium_safety.py` (6 safety tests)
- `tests/test_position_intelligence_restart_equivalence.py` (1 real-data restart-equivalence test)

## 10. Architecture gap re-audit (Step 13)

Re-inspecting the source tree with the mission's stated priority order
(A. position lifecycle, B. adaptive management, C. outcome memory, D.
replay, E. strategy intelligence):

- **A. Position lifecycle intelligence**: confirmed absent as a unified
  model. `ShadowTradeCandidate` -> `PositionEntrySnapshot` ->
  `ThesisEvaluation` exists, but there is no persistent, evolving
  "Position" object that threads a candidate through its whole life
  (entry -> monitoring -> weakening -> exit -> realized outcome, all
  linked by one real identity). Each `ThesisEvaluation` call today is
  independent/stateless -- there is no history of a single position's
  sequence of evaluations over its lifetime.
- **B. Adaptive management** (adjustment/hedge/roll/partial exit/profit
  protection): confirmed entirely absent -- no such package or module
  exists anywhere in the codebase.
- **C. Outcome memory**: confirmed absent -- no mechanism records why a
  trade was selected, what was expected, what happened, or attributes a
  failure to selection/construction/timing/management.
- **D. Replay**: multiple ad hoc, purpose-specific replay scripts now
  exist (Phase 15D/15E/15F's own real-data validation scripts, Phase
  12's Shadow Validation Recorder) but no formal, reusable replay engine.
- **E. Strategy intelligence**: richer inputs (Greeks/Premium Behaviour)
  now exist but are deliberately NOT yet fed into Strategy Selection
  itself, per this phase's own "prove one consumer at a time" discipline.

**Recommended: Phase 15G -- Position Lifecycle Intelligence (Gap A).**
This is the highest-leverage gap because it is the actual missing
foundation for B and C: adaptive management needs a real position
identity to act on, and outcome memory needs a real position lifecycle
to attribute against. Building A first, then B, then C in that
dependency order directly serves the mission's stated end goal: an
evolving model of "Candidate -> Entry -> Position -> Thesis ->
Monitoring -> Weakening -> Adjustment -> Exit -> Realized outcome,"
threaded by one real identity, with Position Intelligence's own
`ThesisEvaluation` calls becoming a real HISTORY on that object instead
of independent, disconnected snapshots.
