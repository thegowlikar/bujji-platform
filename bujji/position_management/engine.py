"""Adaptive Position Management Engine -- Phase 15I. Pure functions, no
state, no IO, no broker, no execution. Never mutates the canonical
position lifecycle (Phase 15G) merely because an assessment was
produced -- an assessment is advisory evidence, never an action.

EVIDENCE HIERARCHY (Step 4), in DECISION PRECEDENCE order (highest
first):

1. Thesis validity (Position Intelligence's own `ThesisEvaluation`,
   Phase 15F) -- the single most authoritative signal, because it is
   ITSELF already a synthesis of market direction, regime, and
   structure evidence (see position_intelligence/engine.py's own
   checks). Re-weighing those underlying domains independently here
   would duplicate, not extend, that synthesis -- exactly the
   ownership-boundary discipline established in Phase 15F. A genuinely
   INVALIDATED thesis dominates every other signal: EXIT, regardless
   of exposure/expiry state.
2. Position exposure (Greeks-derived net delta bias drift since entry)
   -- a real, quantifiable risk fact independent of how the thesis
   itself is being interpreted. A position can develop dangerous
   exposure even while price direction alone still looks fine, which
   is exactly why this is checked SECOND, not folded into thesis logic.
3. Expiry/premium geometry (time-to-expiry via the real Greeks
   `t_years` field) -- checked third: a viable, on-thesis position can
   still need to ROLL purely because time is running out, independent
   of thesis or exposure health.
4. Premium behaviour -- corroborating evidence only this phase (see
   Remaining Limitations in the Phase 15I report): informs the human-
   readable reason text but does not independently change the
   recommendation TYPE, since Position Intelligence (15F) already
   folds a premium-direction-confirmation check into thesis_status
   itself -- using it again here as an independent trigger would
   double-count the same evidence.
5. Liquidity -- evidence-only this phase (recorded, never a decision
   driver) -- see Remaining Limitations.

Never "if greek_bad: EXIT" -- every recommendation is reached by
walking this ordered hierarchy, and a genuinely conflicting evidence
combination (e.g. thesis INTACT but exposure severely diverged)
resolves to the CONSERVATIVE HEDGE recommendation, tagged with BOTH
reason codes, never silently resolved to EXIT nor silently ignored.
"""
from __future__ import annotations

from typing import Optional

from bujji.position_intelligence.models import (
    THESIS_INTACT, THESIS_INVALIDATED, THESIS_UNKNOWN, THESIS_WEAKENING,
)

from .models import (
    RECOMMEND_ADJUST, RECOMMEND_EXIT, RECOMMEND_HEDGE, RECOMMEND_HOLD, RECOMMEND_ROLL, RECOMMEND_UNKNOWN,
    REASON_CONFLICTING_EVIDENCE, REASON_EXPIRY_GEOMETRY_UNFAVORABLE, REASON_EXPOSURE_DRIFT_SEVERE,
    REASON_INSUFFICIENT_EVIDENCE, REASON_THESIS_INTACT, REASON_THESIS_INVALIDATED,
    REASON_THESIS_WEAKENING_RECOVERABLE,
    EvidenceItem, PositionManagementAssessment,
)

# Bounded, explicitly documented new constants (Step 1 Q9/10 -- no
# existing taxonomy defines these; both are new, disclosed defaults):
SEVERE_EXPOSURE_DRIFT_THRESHOLD = 0.5   # |net delta bias change| since entry -- half the [-1,1] real range is a large, real directional shift.
ROLL_TRIGGER_T_YEARS = 2 / 365          # <=2 real calendar days to expiry -- the same expiry-close convention Greeks already use.


def _net_delta_bias(greeks: Optional[dict]) -> Optional[float]:
    """Same real, put-call-parity-grounded proxy as Position
    Intelligence's own `_net_delta_bias` (Phase 15F) -- duplicated
    here per the established small-private-helper precedent, not
    imported (leading-underscore, not a cross-module API)."""
    if not greeks:
        return None
    ce, pe = greeks.get("ce") or {}, greeks.get("pe") or {}
    if not ce.get("available") or not pe.get("available"):
        return None
    delta_ce, delta_pe = ce.get("delta"), pe.get("delta")
    if delta_ce is None or delta_pe is None:
        return None
    return delta_ce + delta_pe


def _assess_exposure(entry_greeks: Optional[dict], current_greeks: Optional[dict]) -> EvidenceItem:
    entry_bias = _net_delta_bias(entry_greeks)
    current_bias = _net_delta_bias(current_greeks)
    if entry_bias is None or current_bias is None:
        return EvidenceItem("exposure", "UNKNOWN", "Greeks unavailable at entry and/or now -- exposure drift cannot be assessed")
    drift = abs(current_bias - entry_bias)
    if drift >= SEVERE_EXPOSURE_DRIFT_THRESHOLD:
        return EvidenceItem("exposure", "SEVERE_DRIFT",
                             f"net delta bias drifted {drift:.3f} since entry (>= {SEVERE_EXPOSURE_DRIFT_THRESHOLD} threshold)")
    return EvidenceItem("exposure", "NORMAL", f"net delta bias drift {drift:.3f} -- within normal range")


def _assess_expiry_geometry(current_greeks: Optional[dict]) -> EvidenceItem:
    t_years = (current_greeks or {}).get("t_years")
    if t_years is None:
        return EvidenceItem("expiry_geometry", "UNKNOWN", "time-to-expiry unavailable")
    if t_years <= ROLL_TRIGGER_T_YEARS:
        return EvidenceItem("expiry_geometry", "UNFAVORABLE",
                             f"t_years={t_years:.5f} <= {ROLL_TRIGGER_T_YEARS:.5f} -- expiry is imminent")
    return EvidenceItem("expiry_geometry", "NORMAL", f"t_years={t_years:.5f} -- expiry not yet a concern")


def _assess_premium_behaviour(current_premium_behaviour: Optional[dict]) -> EvidenceItem:
    if not current_premium_behaviour:
        return EvidenceItem("premium_behaviour", "UNKNOWN", "premium behaviour unavailable")
    combined_direction = (current_premium_behaviour.get("combined") or {}).get("direction")
    if combined_direction is None or combined_direction == "UNKNOWN":
        return EvidenceItem("premium_behaviour", "UNKNOWN", "combined premium direction unresolved")
    return EvidenceItem("premium_behaviour", combined_direction, f"combined premium is {combined_direction.lower()} -- corroborating evidence only, not an independent trigger")


def _assess_liquidity(current_liquidity: Optional[dict]) -> EvidenceItem:
    if not current_liquidity:
        return EvidenceItem("liquidity", "UNKNOWN", "liquidity reading unavailable")
    tightness = current_liquidity.get("tightness")
    if tightness is None:
        return EvidenceItem("liquidity", "UNKNOWN", "liquidity tightness unresolved")
    return EvidenceItem("liquidity", tightness, f"quoted tightness={tightness} -- evidence-only this phase, not a decision driver")


def assess_position_management(
    position_id: str, evaluation_timestamp: str, thesis_evaluation, entry_greeks: Optional[dict],
    current_greeks: Optional[dict], current_premium_behaviour: Optional[dict] = None,
    current_liquidity: Optional[dict] = None, previous_recommendation: Optional[str] = None,
) -> PositionManagementAssessment:
    """`thesis_evaluation`: a real Position Intelligence `ThesisEvaluation`
    (Phase 15F) already computed for this position/cycle -- NEVER
    recomputed here (ownership boundary). Never raises; every missing
    input degrades to an honest UNKNOWN evidence item, never a
    fabricated value."""
    thesis_status = getattr(thesis_evaluation, "thesis_status", None)
    evidence_confidence = getattr(thesis_evaluation, "evidence_confidence", None)

    thesis_evidence = EvidenceItem(
        "thesis", thesis_status or "UNKNOWN",
        f"Position Intelligence thesis_status={thesis_status}, evidence_confidence={evidence_confidence}",
    )
    exposure_evidence = _assess_exposure(entry_greeks, current_greeks)
    expiry_evidence = _assess_expiry_geometry(current_greeks)
    premium_evidence = _assess_premium_behaviour(current_premium_behaviour)
    liquidity_evidence = _assess_liquidity(current_liquidity)
    evidence = (thesis_evidence, exposure_evidence, expiry_evidence, premium_evidence, liquidity_evidence)

    reason_codes = []
    if thesis_status is None or thesis_status == THESIS_UNKNOWN or evidence_confidence in (None, "NONE"):
        recommendation = RECOMMEND_UNKNOWN
        reason_codes.append(REASON_INSUFFICIENT_EVIDENCE)
        reason = "insufficient thesis evidence to produce a management recommendation"

    elif thesis_status == THESIS_INVALIDATED:
        recommendation = RECOMMEND_EXIT
        reason_codes.append(REASON_THESIS_INVALIDATED)
        reason = "thesis is invalidated -- exit dominates regardless of exposure/expiry state"

    elif exposure_evidence.status == "SEVERE_DRIFT":
        recommendation = RECOMMEND_HEDGE
        reason_codes.append(REASON_EXPOSURE_DRIFT_SEVERE)
        if thesis_status == THESIS_INTACT:
            reason_codes.append(REASON_CONFLICTING_EVIDENCE)
            reason = ("thesis remains intact but exposure has drifted severely since entry -- "
                      "a genuine conflict, resolved conservatively to HEDGE rather than HOLD or EXIT")
        else:
            reason_codes.append(REASON_THESIS_WEAKENING_RECOVERABLE)
            reason = "exposure has drifted severely since entry, and the thesis is also weakening"

    elif expiry_evidence.status == "UNFAVORABLE":
        recommendation = RECOMMEND_ROLL
        reason_codes.append(REASON_EXPIRY_GEOMETRY_UNFAVORABLE)
        reason = "the position remains thesis-viable but expiry geometry has become unfavorable"

    elif thesis_status == THESIS_WEAKENING:
        recommendation = RECOMMEND_ADJUST
        reason_codes.append(REASON_THESIS_WEAKENING_RECOVERABLE)
        reason = "thesis is weakening but evidence remains recoverable -- adjust rather than exit"

    else:  # THESIS_INTACT, no severe exposure drift, no expiry concern.
        recommendation = RECOMMEND_HOLD
        reason_codes.append(REASON_THESIS_INTACT)
        reason = "thesis remains intact and no exposure/expiry concerns were found"

    return PositionManagementAssessment(
        position_id=position_id, evaluation_timestamp=evaluation_timestamp, recommendation=recommendation,
        reason_codes=tuple(reason_codes), recommendation_reason=reason, evidence=evidence,
        thesis_status=thesis_status, evidence_confidence=evidence_confidence,
        previous_recommendation=previous_recommendation,
    )
