# Phase 15E -- Greeks + Premium Behaviour Intelligence: Final Report

## 1. Forensic findings

**Greeks**: `bujji/intelligence/greeks_brain.py` (legacy) contains real,
validated Black-Scholes delta/gamma/theta/vega math, but is hardcoded
to a SHORT-straddle POSITION combination (`position_x = -(leg_ce +
leg_pe)`) -- an assumption inappropriate for a general, position-agnostic
observational layer. More significantly: `bujji/msi_volatility_structure/engine.py`
(the CURRENT, live VSB bridge, already wired into every real cycle)
**imports** `_bs_delta`/`_bs_gamma`/`_bs_theta`/`_bs_vega` and its own
docstring claims they are "imported and called DIRECTLY" -- but a
direct source check (`grep -n '_bs_delta(' ...`) found **zero actual
call sites**. This is a real, confirmed documentation/reality mismatch:
the math is validated, live-adjacent, even imported into production
code, but never actually invoked anywhere. `solve_implied_volatility`
IS genuinely live (feeds VSB's `iv_average`/expected-move), but
per-leg IV/Greeks are discarded internally by
`derive_iv_and_expected_move` (only the average is kept).

**Premium Behaviour**: confirmed absent entirely -- no temporal memory
of premium movement exists anywhere in the codebase before this phase.

**Root cause classification**: architectural gap for Premium Behaviour
(never built); a genuine dead-import / stale-docstring defect for
Greeks (math exists, partially imported, never executed) -- not a
missing capability so much as an unfinished connection.

## 2. Design

Both new capabilities follow the SAME layered pattern already
established by `msi_volatility_structure`/`msi_greeks`'s sibling
bridges and Phase 15B-D's persistence architecture -- no new
architectural style invented.

```
Option Observation (MarketSnapshot.option_chain)
      |
Bridge (plain-scalar extraction, mirrors msi_adapter.py exactly)
      |
Pure engine (msi_greeks.engine / premium_behaviour.engine)
      |
IntelligenceCycleRecorder (additive fields: "greeks", "premium_behaviour")
      |
intelligence_cycle.jsonl (persisted, observational)
```

- **`bujji/msi_greeks/`** (new): `models.py` (`GreeksLegAssessment`,
  per-leg, `available`/`reason` UNKNOWN-first-class), `engine.py`
  (`assess_atm_greeks` -- reuses `solve_implied_volatility`/`_bs_vega`
  (volatility_brain) and `_bs_delta`/`_bs_gamma`/`_bs_theta`
  (greeks_brain) DIRECTLY, unmodified; each leg solved
  **independently**, unlike VSB's both-or-neither IV average -- a CE
  failure never suppresses a real, computable PE read).
- **`bujji/market_perception/greeks_adapter.py`** (new): mirrors
  `msi_adapter.py`'s `build_volatility_structure_assessment` pattern
  exactly (`_atm_mid_premiums`/`_t_years_to_expiry` duplicated per the
  project's established "don't import a private helper" precedent, set
  by Phase 9's Liquidity Bridge).
- **`bujji/premium_behaviour/`** (new): `models.py`
  (`PremiumObservation`, `PremiumBehaviourState` -- frozen, bounded
  rolling window, `.advance()`-threaded exactly like
  `RegimeMemoryState`/`ObservationMemory`), `engine.py` (`evaluate()`
  -- pure; direction/rate/acceleration per series (CE/PE/combined),
  relative CE-vs-PE expansion, premium-vs-underlying co-movement),
  `recovery.py` (`hydrate_premium_behaviour` -- reuses
  `bujji.market_state_builder.recovery.read_market_snapshots_with_diagnostics`
  **directly**, the same generic diagnostics reader Phase 15D built,
  rather than inventing a second one for the same underlying file).

**Ownership boundary, explicit**: neither Greeks nor Premium Behaviour
feeds VSB, consensus, opportunity, or selection this phase --
confirmed by a dedicated safety test
(`test_premium_behaviour_not_wired_into_any_decision_engine`) asserting
neither name appears in `msi_consensus`/`msi_decision_synthesis`
source. Per the mission's explicit "establish contracts and prove
consumer benefit first" instruction, no consumer was proven this phase
-- both signals are persisted, observational-only, exactly like
Position Intelligence's own precedent.

## 3. Real-data validation (Session B, 174 real cycles)

**Greeks**: CE Greeks available in **1/174 cycles (0.6%)**, PE in
**3/174 (1.7%)** -- both legs failed almost every cycle for the exact
same reason: `missing_premium` (the ATM strike's bid AND ask were not
BOTH real simultaneously). This is an **honest, disclosed finding, not
a defect** -- it is directly consistent with the Phase 14 forensic
audit's earlier finding that this same session's ATM liquidity was
frequently one-sided (4/13 strategy families were only
`PARTIALLY_CONSTRUCTIBLE` for the same underlying reason). Where data
WAS sufficient, the computed Greeks passed every sanity check: delta
in `[0, 1]` for CE (0 out-of-bounds outliers across all computed
values), gamma always positive, theta always negative, vega always
positive, IV within the solver's `[1%, 300%]` bounds.

**Premium Behaviour**: confidence stayed `NONE` for all 174 cycles in
this real session -- a direct, honest consequence of the same
ATM-liquidity gap (a real CE/PE mid premium was almost never available
two cycles in a row). This is the CORRECT behavior, not a bug: "no
reliable data" must produce `NONE`/`UNKNOWN`, never a fabricated
reading.

**Restart equivalence**: tested at 5 real boundaries (cycles 1, 20,
87, 150, 173) -- **0 mismatches at every boundary**, confirmed by
direct state equality (not just a fingerprint), independent of how
sparse the real underlying premium data was.

## 4. Tests

- `tests/test_msi_greeks.py` -- 18 tests: real BS sanity (ATM delta ~0.5, gamma>0, theta<0, vega>0), deep ITM/OTM boundary behavior, missing premium/spot/strike/expiry, premium-below-intrinsic refusal, independent per-leg solving, adapter-level None/degraded-leg handling.
- `tests/test_premium_behaviour.py` -- 20 tests: absolute/relative/UNKNOWN-preservation behaviour, bounded lookback window, confidence scaling, recovery (missing file, continuous-replay equivalence, torn-line degradation).
- `tests/test_shadow_runtime_premium_behaviour_recovery.py` -- 5 tests: clean startup, recovery-not-requested, mid-session restart continuity, torn-line degradation at runtime, and a dedicated non-wiring safety check.
- `tests/test_greeks_and_premium_behaviour_safety.py` -- 12 tests: no forbidden imports/calls, no broker/capital/risk/position references, no internal wall-clock reads, no `random`, legacy math + live VSB engine byte-untouched, cumulative recorder diff additive-only, UNKNOWN propagation confirmed end-to-end.

**55/55 new tests passing.**

## 5. Safety

12/12 dedicated safety tests pass. No new documented exceptions were
needed this phase -- unlike Phases 15B-D, none of Phase 15E's changes
crossed a pre-existing protected boundary (the two touched files,
`shadow_session_runner.py`/`intelligence_cycle_recorder.py`, were
already opened for additive recovery wiring in prior phases; their
existing "additive-only diff" safety checks pass unmodified).

## 6. Full regression

`4638 passed` (1 pre-existing, unrelated deprecation warning).

## 7. Files changed

New:
- `bujji/msi_greeks/{__init__,models,engine}.py`
- `bujji/market_perception/greeks_adapter.py`
- `bujji/premium_behaviour/{__init__,models,engine,recovery}.py`
- `tests/test_msi_greeks.py`
- `tests/test_premium_behaviour.py`
- `tests/test_shadow_runtime_premium_behaviour_recovery.py`
- `tests/test_greeks_and_premium_behaviour_safety.py`

Modified (additive only):
- `bujji/market_state/intelligence_cycle_recorder.py` -- `greeks`/`premium_behaviour` record fields, `initial_premium_behaviour_state` constructor param, `_atm_mid_premiums` helper (duplicated per established precedent).
- `bujji/shadow_runtime/shadow_session_runner.py` -- `premium_behaviour_recovery_enabled` opt-in param.
- `bujji/shadow_runtime/shadow_session_artifact.py` -- `premium_behaviour_recovery_report` field.
- `tests/test_intelligence_cycle_recorder.py`, `tests/test_shadow_runtime_phase5.py` -- exact-key/field-set updates (established pattern).

## 8. Remaining gaps / disclosed limitations

- **ATM-only, single-strike**: neither Greeks nor Premium Behaviour looks beyond the ATM CE/PE pair -- no skew, smile, or multi-strike surface. Matches VSB's own existing, disclosed scope limitation.
- **No proven downstream consumer yet** -- by design this phase, per the mission's explicit instruction not to wire a new signal in without first establishing its contract and value. VSB/strategy selection/position intelligence are all plausible future consumers, not yet connected.
- **Real Session B's ATM liquidity is sparse enough that both new signals were mostly UNKNOWN in this specific session** -- this is a data-quality fact about that session (already known from Phase 14), not a defect in this phase's logic; the restart-equivalence and sanity-check results prove the logic itself is sound wherever real data supports it.
- **VWAP/ORB-relative premium behaviour** (mentioned in the mission's structural-behaviour category) was explicitly NOT built -- no VWAP/ORB computation exists anywhere in the currently-active pipeline (confirmed by source search; matches are only in older, unconnected lineage), and fabricating one for this purpose would violate the no-invention rule. Documented as deferred, not silently dropped.
- **rho** was investigated and deliberately deferred -- for near-dated NIFTY weekly options, rho's contribution is negligible next to delta/gamma/theta/vega, and the legacy brain never computed it either; not worth the added surface area without a proven need.

## 9. Architecture gap re-audit (Step 10) and recommended next phase

Re-inspecting the source tree (not repeating the Phase 15D audit
verbatim): the two intelligence gaps this phase targeted are now
built, tested, and real-data-validated, but genuinely UNCONNECTED to
any consumer -- an accurate, disclosed state, not a shortcut. Per the
mission's own priority ordering (market understanding first, more
strategies last), the next highest-leverage gap is not a third new
observational signal but **closing the loop on the signals Bujji
already has**: VSB, Greeks, Premium Behaviour, and Position
Intelligence all exist independently with zero cross-wiring, and
`msi_volatility_structure/engine.py`'s own stale docstring (discovered
this phase) is itself evidence that "built but not connected" is a
recurring failure mode worth addressing directly.

**Recommended: Phase 15F -- Establish the first real Greeks/Premium
Behaviour consumer.** Rather than building a fourth standalone signal,
prove that ONE of the two new capabilities from this phase measurably
improves an existing decision -- the most natural, smallest-blast-radius
candidate is feeding Greeks' `position_delta`-shaped exposure read (once
combined for a specific candidate structure) into `Position Intelligence`'s
existing thesis-monitoring checks (`_check_direction`,
`_check_volatility_trend`) as a NEW, additional check alongside the
existing ones -- not replacing them. This directly serves the
mission's stated end goal ("whether the original thesis remains
valid, what has changed") rather than adding another parallel brain
nobody consumes.
