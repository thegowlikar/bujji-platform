# Phase 15I -- Adaptive Position Management: Final Report

## 1. Forensic findings

**Answers to Step 1's 10 questions**:
1. At entry: everything `PositionEntrySnapshot`/`EntrySnapshot` already carry (Phase 15F/15G) -- direction, regime, thesis, confidence, entry Greeks/premium behaviour if available.
2. Every subsequent cycle: a real intelligence_cycle record's `market_direction`/`market_state`/`greeks`/`premium_behaviour`/`liquidity` fields.
3. **Position Intelligence's `ThesisEvaluation` is reused directly as the single most authoritative input** -- never re-derived (see Evidence Hierarchy).
4. Greeks/Premium Behaviour dicts (Phase 15E) are consumed exactly as already shaped -- no re-parsing, no new derivation.
5. Existing lifecycle events (15G): `POSITION_OPENED`/`THESIS_EVALUATED`/`POSITION_CLOSED`. Smallest additive vocabulary needed: **one** new event, `MANAGEMENT_ASSESSED`.
6. **ADJUST/HEDGE/ROLL are recommendation types ONLY this phase** -- no structured action plan (target strike, hedge ratio, roll destination) is produced, since no downstream consumer or PaperBroker integration exists to use one yet.
7. Distinguished explicitly via the evidence hierarchy (below): thesis deterioration (Position Intelligence's own domain), risk/exposure deterioration (Greeks-derived delta drift, a NEW dimension this phase adds), opportunity deterioration (folded into thesis via Position Intelligence's existing regime/consensus checks, never duplicated), normal evolution (HOLD), genuine exit (thesis INVALIDATED with real confidence).
8. **`trading_brain/exit_engine`, `risk_governor`, `position_sizing` were NOT reused** -- confirmed again disconnected legacy lineage (same finding as every prior phase); none imported.
9. **Representation/taxonomy finding**: `bujji.position_intelligence.models.RECOMMEND_ADJUST` has existed since Phase 15/15F but `evaluate_thesis()` never actually produces it (`THESIS_WEAKENING` always maps to `RECOMMEND_HOLD` in that module) -- another "built but not connected" instance (same pattern as Phase 15E's dead Greeks import, Phase 15F's unused per-leg delta). This phase does NOT modify Position Intelligence's own recommendation field; it builds a separate, higher-level assessment instead.
10. Minimum trustworthy input: a real `ThesisEvaluation` with `evidence_confidence != NONE`. Everything else (exposure, expiry, premium, liquidity) degrades independently and honestly to `UNKNOWN` without blocking a HOLD verdict when thesis evidence alone is sufficient.

## 2. Architecture

```
PositionLifecycle (15G) + ThesisEvaluation (15F) + Greeks/PremiumBehaviour (15E)
                    |
     bujji.position_management.engine.assess_position_management()
                    |
       PositionManagementAssessment
         - recommendation: HOLD/ADJUST/HEDGE/ROLL/EXIT/UNKNOWN
         - reason_codes: machine-readable
         - evidence: 5 EvidenceItem(dimension, status, detail)
         - thesis_status / evidence_confidence: copied for traceability
         - previous_recommendation: continuity, never re-derived
                    |
     bujji.position_lifecycle.engine.build_management_assessed_payload()
                    |
          MANAGEMENT_ASSESSED event (EventStore, Phase 15B)
                    |
     PositionLifecycle.management_assessments (accumulated, replay-derived)
```

No parallel position engine was created -- the existing 15G lifecycle
model was extended with exactly one new event type and two new fields
(`management_assessments`, `latest_management_recommendation`), both
additive and defaulted.

## 3. Evidence hierarchy (Step 4), in decision precedence order

1. **Thesis validity** (Position Intelligence's `ThesisEvaluation`) -- most authoritative, since it is ITSELF already a synthesis of direction/regime/structure evidence (Phase 15F). An INVALIDATED thesis dominates: `EXIT`, regardless of exposure/expiry.
2. **Position exposure** (Greeks-derived net delta bias drift since entry) -- a real, quantifiable risk fact independent of thesis interpretation. Checked second because a position can develop dangerous exposure even while price direction still looks fine.
3. **Expiry/premium geometry** (`t_years` from Greeks) -- a viable, on-thesis position can still need to `ROLL` purely because time is running out.
4. **Premium behaviour** -- corroborating evidence only (recorded, informs reason text) -- NOT an independent trigger, since Position Intelligence already folds a premium-direction-confirmation check into `thesis_status` itself; using it again here would double-count the same evidence.
5. **Liquidity** -- evidence-only this phase (recorded, never a decision driver) -- disclosed limitation, not silently ignored.

## 4. Recommendation semantics (Step 3) -- all 7 rules implemented

1. Thesis intact, no exposure/expiry concern → `HOLD`
2. Thesis weakening, evidence recoverable → `ADJUST`
3. Exposure drift ≥ `0.5` net-delta-bias threshold (new, bounded, disclosed default) → `HEDGE`
4. `t_years ≤ 2/365` (new, bounded, disclosed default) with thesis still viable → `ROLL`
5. Thesis invalidated with real confidence → `EXIT`
6. Insufficient evidence (`THESIS_UNKNOWN` or `evidence_confidence == NONE`) → `UNKNOWN`
7. Conflicting strong evidence (thesis `INTACT` but exposure severely diverged) → conservative `HEDGE`, tagged `CONFLICTING_EVIDENCE`, never fabricated `EXIT` or silently ignored

## 5. Lifecycle integration (Step 6)

One new event type, `MANAGEMENT_ASSESSED`, with the identical guard
discipline as `THESIS_EVALUATED`: only accepted while the position is
`OPEN`; rejected for unknown `position_id`, wrong session, or malformed
payload. **No `POSITION_ADJUSTED`/`POSITION_HEDGED`/`POSITION_ROLLED`
event was created** -- verified by a dedicated safety test (AST-based,
confirms no such constant is defined) -- recording an assessment NEVER
implies the recommended action was actually taken; a direct regression
test proves a strong `EXIT` recommendation never changes
`PositionLifecycle.status` or its legs.

## 6. Replayability (Step 7)

Reuses Phase 15G's own `EventStore`-based `hydrate_position_lifecycles`
directly -- no new persistence mechanism. Proven: deterministic double
replay (byte-identical `to_dict()`), and a genuine restart simulation
(2 management events persisted, "restart" via a fresh `EventStore`
against the same file, then the 3rd event persisted and replayed)
matches a single uninterrupted 3-event replay exactly.

## 7. Semantic test matrix (Step 8) -- all 13 required scenarios covered

| Scenario | Result |
|---|---|
| Healthy directional position | `HOLD` -- PASS |
| Weakening directional thesis | `ADJUST` -- PASS |
| Invalidated directional thesis | `EXIT` -- PASS |
| Excessive delta/exposure drift | `HEDGE` -- PASS |
| Viable position, unfavorable expiry geometry | `ROLL` -- PASS |
| Insufficient evidence | `UNKNOWN` -- PASS |
| Conflicting evidence | conservative `HEDGE`, never `EXIT` -- PASS |
| `UNKNOWN` Greeks | exposure honestly `UNKNOWN`, never fabricated -- PASS |
| `UNKNOWN` premium behaviour | never fabricates deterioration -- PASS |
| Repeated identical assessment | deterministic (`to_dict()` equality) -- PASS |
| Already-closed position | `MANAGEMENT_ASSESSED` rejected, nothing recorded -- PASS |
| Wrong-session position | rejected -- PASS |
| Multi-leg position | one assessment history at the position level, legs untouched -- PASS |

**28 new tests across `test_position_management.py` (18),
`test_position_lifecycle_management.py` (7), and
`test_position_management_replay.py` (3), all passing.**

## 8. Safety (Step 9)

7/7 dedicated safety tests: no forbidden imports/calls (AST-based), no
broker/PaperBroker/capital/margin references, a real regression proof
that a strong `EXIT` recommendation never mutates canonical position
status or legs, no side-effecting-looking calls in the engine, no
premature `POSITION_ADJUSTED`/`POSITION_HEDGED`/`POSITION_ROLLED` event
constant defined, and `trading_brain`/`risk_governor`/broker guard
files confirmed byte-untouched. No new documented exceptions were
needed -- this phase touched no pre-existing protected boundary.

## 9. Real-data validation (Step 10) -- kept explicitly separate from fixtures

**REAL MARKET EVIDENCE** (`SHADOW-OBSERVATORY-2026-08-06`, 173 real
cycle-pairs): no real position was fabricated (this session never
opened one, confirmed again). Evidence-availability was measured using
the same real per-cycle inputs a position would have consumed:
- Exposure evidence: **100% UNKNOWN** (173/173)
- Expiry geometry evidence: **100% UNKNOWN** (173/173)
- Premium behaviour evidence: **100% UNKNOWN** (173/173)
- Liquidity evidence: 172/173 UNKNOWN, 1/173 `TIGHT`

These rates directly and honestly reflect that this archived session
predates Phase 15E (0 real `greeks`/`premium_behaviour` fields exist
in its persisted records at all) -- NOT a defect in this phase's
evidence-gathering logic, consistent with every prior phase's own
finding about this specific session. Recommendation distribution:
172/173 `HOLD` (thesis-only evidence sufficient), 1/173 `UNKNOWN`
(the one real `NO_CONSENSUS` cycle). `UNKNOWN` was preserved honestly
throughout -- never silently converted into a positive signal.

**SEMANTIC FIXTURE EVIDENCE**: Section 7's 13-scenario matrix, clearly
labeled as fixtures, never presented as real-market evidence.

## 10. Regression (Step 11)

`4772 passed` (1 pre-existing, unrelated deprecation warning). Zero
regressions; all pre-existing safety tests preserved unmodified.

## 11. Files changed

New:
- `bujji/position_management/{__init__,models,engine}.py`
- `tests/test_position_management.py`
- `tests/test_position_lifecycle_management.py`
- `tests/test_position_management_replay.py`
- `tests/test_position_management_safety.py`

Modified (additive only):
- `bujji/position_lifecycle/models.py` -- `EVENT_MANAGEMENT_ASSESSED`, `management_assessments`, `latest_management_recommendation` fields.
- `bujji/position_lifecycle/engine.py` -- `build_management_assessed_payload`, `_apply_management_assessed` reducer, dispatch wiring.

## 12. UNKNOWN handling -- explicit summary

`UNKNOWN` is preserved at every layer: missing Greeks → exposure
`UNKNOWN` (never a fabricated drift number); missing premium behaviour
→ `UNKNOWN` (never a fabricated deterioration signal); missing
`t_years` → expiry geometry `UNKNOWN` (never a fabricated urgency);
`thesis_status is None`/`THESIS_UNKNOWN`/`evidence_confidence == NONE`
→ the WHOLE recommendation is `UNKNOWN`, dominating over any other
evidence. No single weak/unknown signal ever forces `EXIT` -- confirmed
by dedicated fixtures.

## 13. Trustworthiness decision (Step 12's explicit requirement)

**Is Adaptive Position Management trustworthy enough to become a
future PaperBroker consumer? Not yet -- and this is an honest, evidence-based
conclusion, not a deferral for its own sake.** Reasons:
- It has never been exercised against a real position with real Greeks/premium evidence (this session's 100% UNKNOWN rate on those dimensions means the POSITIVE decision paths -- HEDGE, ROLL, ADJUST driven by real data -- remain proven only via semantic fixtures, not real-market evidence).
- ADJUST/HEDGE/ROLL remain recommendation TYPES ONLY -- no structured action plan exists, which any real PaperBroker consumer would need.
- Position Lifecycle itself (15G) has never recorded a real opened position either -- this layer's trustworthiness is capped by that same upstream gap.
- What IS proven and trustworthy: the recommendation LOGIC is deterministic, replayable, evidence-preserving, and safe (zero regressions, zero execution capability) -- a strong foundation, but "foundation" is not yet "PaperBroker-ready."

## 14. Newly discovered gaps / architecture re-audit (mandatory next-gap identification)

- **`RECOMMEND_ADJUST` dead code in Position Intelligence** (Section 1, finding 9) -- not fixed this phase (out of scope; Position Intelligence's own recommendation field is a separate, narrower concept from this phase's fuller vocabulary).
- **No structured action plans** for ADJUST/HEDGE/ROLL -- correctly deferred (no proven consumer yet).
- **Liquidity and Premium Behaviour remain evidence-only**, never independent decision drivers -- a disclosed scope limitation, not an oversight.
- **The 100% real-session UNKNOWN rate on Greeks/Premium** is the same root limitation traced through every phase since 15E: this specific archived session predates the intelligence that would make it useful. A fresh live session (once market hours + a valid FYERS token are available) would be the highest-value real-data re-validation across Phases 15E-15I simultaneously.
- **Outcome attribution still does not exist** -- Position Lifecycle (15G) now preserves everything needed for it (entry reasoning, thesis evolution, management assessment history, exit reason), and Adaptive Management (this phase) adds a further layer of "what was recommended and why" -- but the actual attribution LOGIC (was a bad outcome caused by selection, construction, timing, or management?) remains unbuilt.

## 15. Recommended Phase 15J (not started this phase, per instruction)

**Phase 15J -- Outcome Attribution.** Position Lifecycle (15G) and
Adaptive Position Management (this phase) together now preserve a
complete, replayable record of a position's entire reasoning history:
why it was opened, how its thesis evolved, what was recommended and
when, and why it closed. The one missing piece to close the
"observe → understand → remember → reason → select → construct →
monitor → adapt → **learn**" loop the mission describes is the
attribution logic itself -- turning that preserved history into an
explicit judgment on WHY an outcome happened (selection fault,
construction fault, timing fault, or management fault) and whether the
original strategy selection was validated or contradicted by what
actually occurred. This is the natural next step because: (a) it has
real data to consume now (the lifecycle events this phase and 15G both
produce), (b) it requires no new market intelligence signal, only
reasoning over what already exists, and (c) it is the direct
prerequisite for any future "did this strategy family actually work"
feedback -- which must remain strictly analytical, per the mission's
own explicit warning against letting outcome data silently contaminate
same-session strategy selection.
