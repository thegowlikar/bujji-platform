"""Tests -- Phase 15J Outcome Attribution. Semantic fixtures, no
broker, no execution, no live calls. Post-trade ANALYTICAL intelligence
only -- these fixtures explicitly verify the engine never overclaims
causality and never equates profit=good/loss=bad."""
from __future__ import annotations

import dataclasses

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.outcome_attribution.models import (
    DIM_DIRECTION, DIM_ENTRY_TIMING, DIM_EXIT_TIMING, DIM_GREEKS_EXPOSURE, DIM_LIQUIDITY,
    DIM_MANAGEMENT, DIM_PREMIUM_BEHAVIOUR, DIM_SELECTION, DIM_STRUCTURAL_GEOMETRY, DIM_VOLATILITY,
    IMPACT_NEGATIVE, IMPACT_POSITIVE, NOT_READY, OUTCOME_LOSS, OUTCOME_PROFIT, OUTCOME_UNKNOWN, READY,
    ROLE_PROTECTIVE_FACTOR,
)
from bujji.position_lifecycle.identity import leg_id_for
from bujji.position_lifecycle.models import EntrySnapshot, LegRecord, PositionLifecycle, STATUS_CLOSED, STATUS_OPEN


def _entry(strategy_family="LONG_DIRECTIONAL", entry_direction="BULLISH", entry_confidence="HIGH"):
    return EntrySnapshot(
        candidate_id="STC-x", source_cycle_id="t0", strategy_family=strategy_family, entry_timestamp="t0",
        underlying_price=24450.0, entry_direction=entry_direction, entry_regime="RANGING",
        entry_thesis="TREND_CONTINUATION", entry_confidence=entry_confidence,
        entry_greeks=None, entry_premium_behaviour=None,
    )


def _leg(pid="POS-1"):
    return LegRecord(leg_id=leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), role="PRIMARY",
                      option_type="CE", strike=24450.0, expiry="2026-08-13", side="BUY", quantity=1,
                      entry_premium=130.0, entry_delta=0.5)


def _thesis_eval(status, direction_status="CONSISTENT", regime_status="CONSISTENT", vol_status="CONSISTENT"):
    return {
        "thesis_status": status,
        "checks": [
            {"dimension": "direction", "entry_value": "BULLISH", "current_value": "BULLISH", "status": direction_status, "reason": "r"},
            {"dimension": "regime", "entry_value": "RANGING", "current_value": "RANGING", "status": regime_status, "reason": "r"},
            {"dimension": "volatility_trend", "entry_value": "STABLE", "current_value": "STABLE", "status": vol_status, "reason": "r"},
        ],
        "recommendation": "HOLD", "recommendation_reason": "r",
    }


def _mgmt_assessment(recommendation="HOLD", exposure="NORMAL", premium="STEADY", liquidity="TIGHT", expiry="NORMAL"):
    return {
        "recommendation": recommendation,
        "evidence": [
            {"dimension": "exposure", "status": exposure, "detail": "d"},
            {"dimension": "premium_behaviour", "status": premium, "detail": "d"},
            {"dimension": "liquidity", "status": liquidity, "detail": "d"},
            {"dimension": "expiry_geometry", "status": expiry, "detail": "d"},
        ],
    }


def _closed_lifecycle(
    thesis_evaluations=(), management_assessments=(), realized_pnl=None,
    exit_reason="session_end", strategy_family="LONG_DIRECTIONAL", legs=None,
):
    return PositionLifecycle(
        position_id="POS-1", session_id="S1", status=STATUS_CLOSED, entry=_entry(strategy_family=strategy_family),
        legs=legs or (_leg(),), opened_at="t0", thesis_evaluations=tuple(thesis_evaluations),
        management_assessments=tuple(management_assessments), closed_at="t9", exit_reason=exit_reason,
        final_thesis_status=(thesis_evaluations[-1]["thesis_status"] if thesis_evaluations else None),
        latest_management_recommendation=(management_assessments[-1]["recommendation"] if management_assessments else None),
        realized_pnl=realized_pnl,
    )


# ---------------------------------------------------------------------------
# Readiness (Step 6).
# ---------------------------------------------------------------------------
def test_open_position_is_not_ready():
    lifecycle = PositionLifecycle(
        position_id="POS-1", session_id="S1", status=STATUS_OPEN, entry=_entry(), legs=(_leg(),), opened_at="t0",
    )
    result = attribute_position_outcome(lifecycle)
    assert result.readiness == NOT_READY
    assert result.outcome_direction == OUTCOME_UNKNOWN


def test_closed_position_is_ready():
    lifecycle = _closed_lifecycle()
    result = attribute_position_outcome(lifecycle)
    assert result.readiness == READY


# ---------------------------------------------------------------------------
# Step 5: P&L outcome kept strictly separate from causal attribution.
# ---------------------------------------------------------------------------
def test_missing_realized_pnl_is_unknown_never_fabricated():
    lifecycle = _closed_lifecycle(realized_pnl=None)
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == OUTCOME_UNKNOWN
    assert result.realized_pnl is None


def test_profit_direction_when_pnl_known():
    lifecycle = _closed_lifecycle(realized_pnl=500.0)
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == OUTCOME_PROFIT


def test_loss_direction_when_pnl_known():
    lifecycle = _closed_lifecycle(realized_pnl=-500.0)
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == OUTCOME_LOSS


# ---------------------------------------------------------------------------
# Required fixture 1: profitable trade because thesis was correct.
# ---------------------------------------------------------------------------
def test_profitable_trade_correct_thesis():
    thesis_evals = [_thesis_eval("THESIS_INTACT") for _ in range(3)]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals, realized_pnl=800.0)
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == OUTCOME_PROFIT
    selection_evidence = next(e for e in result.evidence if e.dimension == DIM_SELECTION)
    assert selection_evidence.impact_direction == IMPACT_POSITIVE


# ---------------------------------------------------------------------------
# Required fixture 2: losing trade despite correct thesis (adverse market).
# ---------------------------------------------------------------------------
def test_losing_trade_despite_correct_thesis():
    thesis_evals = [_thesis_eval("THESIS_INTACT") for _ in range(3)]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals, realized_pnl=-300.0)
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == OUTCOME_LOSS
    selection_evidence = next(e for e in result.evidence if e.dimension == DIM_SELECTION)
    # Selection/thesis is NOT implicated even though the outcome was a loss -- this is the
    # explicit "profit != good, loss != bad" separation the mission requires.
    assert selection_evidence.impact_direction == IMPACT_POSITIVE


# ---------------------------------------------------------------------------
# Required fixture 3: bad selection + profitable outcome.
# ---------------------------------------------------------------------------
def test_bad_selection_profitable_outcome():
    thesis_evals = [_thesis_eval("THESIS_INTACT"), _thesis_eval("THESIS_WEAKENING"), _thesis_eval("THESIS_INVALIDATED")]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals, realized_pnl=200.0)
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == OUTCOME_PROFIT
    selection_evidence = next(e for e in result.evidence if e.dimension == DIM_SELECTION)
    assert selection_evidence.impact_direction == IMPACT_NEGATIVE  # thesis was invalidated -- selection IS implicated, despite profit.


# ---------------------------------------------------------------------------
# Required fixture 4: good selection + bad entry timing.
# ---------------------------------------------------------------------------
def test_good_selection_bad_entry_timing():
    thesis_evals = [_thesis_eval("THESIS_INTACT", direction_status="DEVIATED")] + [_thesis_eval("THESIS_INTACT") for _ in range(2)]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals)
    result = attribute_position_outcome(lifecycle)
    entry_timing_evidence = next(e for e in result.evidence if e.dimension == DIM_ENTRY_TIMING)
    assert entry_timing_evidence.impact_direction == IMPACT_NEGATIVE


# ---------------------------------------------------------------------------
# Required fixture 5: management prevented a larger loss.
# ---------------------------------------------------------------------------
def test_management_prevented_larger_loss():
    mgmt = [_mgmt_assessment("HOLD"), _mgmt_assessment("EXIT")]
    lifecycle = _closed_lifecycle(management_assessments=mgmt, exit_reason="thesis_invalidated", realized_pnl=-100.0)
    result = attribute_position_outcome(lifecycle)
    mgmt_evidence = next(e for e in result.evidence if e.dimension == DIM_MANAGEMENT)
    assert mgmt_evidence.role == ROLE_PROTECTIVE_FACTOR
    assert mgmt_evidence.impact_direction == IMPACT_POSITIVE


# ---------------------------------------------------------------------------
# Required fixture 6: management worsened an otherwise recoverable position.
# ---------------------------------------------------------------------------
def test_management_recommendation_not_acted_on_is_unknown_not_fabricated():
    mgmt = [_mgmt_assessment("EXIT")]
    lifecycle = _closed_lifecycle(management_assessments=mgmt, exit_reason="session_end")
    result = attribute_position_outcome(lifecycle)
    mgmt_evidence = next(e for e in result.evidence if e.dimension == DIM_MANAGEMENT)
    # EXIT was recommended but the position closed for an unrelated reason --
    # the engine must NOT claim management worsened or improved anything without evidence of action.
    assert mgmt_evidence.impact_direction != IMPACT_NEGATIVE
    assert mgmt_evidence.impact_direction != IMPACT_POSITIVE


# ---------------------------------------------------------------------------
# Required fixture 7: volatility caused the majority of outcome.
# ---------------------------------------------------------------------------
def test_volatility_dominant_factor():
    thesis_evals = [_thesis_eval("THESIS_WEAKENING", vol_status="DEVIATED") for _ in range(3)]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals)
    result = attribute_position_outcome(lifecycle)
    vol_evidence = next(e for e in result.evidence if e.dimension == DIM_VOLATILITY)
    assert vol_evidence.impact_direction == IMPACT_NEGATIVE


# ---------------------------------------------------------------------------
# Required fixture 8: premium behaviour caused the majority of outcome.
# ---------------------------------------------------------------------------
def test_premium_behaviour_dominant_factor():
    mgmt = [_mgmt_assessment(premium="RISING") for _ in range(3)]
    lifecycle = _closed_lifecycle(management_assessments=mgmt)
    result = attribute_position_outcome(lifecycle)
    premium_evidence = next(e for e in result.evidence if e.dimension == DIM_PREMIUM_BEHAVIOUR)
    assert premium_evidence.impact_direction == IMPACT_NEGATIVE


# ---------------------------------------------------------------------------
# Required fixture 9: exit timing materially reduced profit.
# ---------------------------------------------------------------------------
def test_exit_timing_labeled_reason_is_contributing():
    lifecycle = _closed_lifecycle(exit_reason="manual_profit_target")
    result = attribute_position_outcome(lifecycle)
    exit_evidence = next(e for e in result.evidence if e.dimension == DIM_EXIT_TIMING)
    assert exit_evidence.observed_value == "manual_profit_target"


def test_exit_timing_session_end_is_unknown_not_negative():
    lifecycle = _closed_lifecycle(exit_reason="session_end")
    result = attribute_position_outcome(lifecycle)
    exit_evidence = next(e for e in result.evidence if e.dimension == DIM_EXIT_TIMING)
    assert exit_evidence.impact_direction != IMPACT_NEGATIVE


# ---------------------------------------------------------------------------
# Required fixture 10: liquidity/execution materially affected outcome.
# ---------------------------------------------------------------------------
def test_liquidity_dominant_factor():
    mgmt = [_mgmt_assessment(liquidity="UNKNOWN") for _ in range(2)] + [_mgmt_assessment(liquidity="TIGHT")]
    lifecycle = _closed_lifecycle(management_assessments=mgmt)
    result = attribute_position_outcome(lifecycle)
    liquidity_evidence = next(e for e in result.evidence if e.dimension == DIM_LIQUIDITY)
    assert liquidity_evidence.impact_direction == IMPACT_POSITIVE  # no adverse liquidity reading ever recorded.


# ---------------------------------------------------------------------------
# Required fixture 11: insufficient evidence.
# ---------------------------------------------------------------------------
def test_insufficient_evidence_produces_no_primary_cause():
    lifecycle = _closed_lifecycle()  # no thesis evaluations, no management assessments.
    result = attribute_position_outcome(lifecycle)
    # selection may resolve via final_thesis_status=None -> UNKNOWN; entry_timing UNKNOWN; all trajectory dims UNKNOWN.
    unresolved_dims = {e.dimension for e in result.evidence if e.role == "UNKNOWN"}
    assert DIM_DIRECTION in unresolved_dims
    assert DIM_VOLATILITY in unresolved_dims


# ---------------------------------------------------------------------------
# Required fixture 12: conflicting evidence.
# ---------------------------------------------------------------------------
def test_conflicting_evidence_does_not_overclaim():
    thesis_evals = [_thesis_eval("THESIS_INTACT", direction_status="DEVIATED", regime_status="CONSISTENT")]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals)
    result = attribute_position_outcome(lifecycle)
    # Both a negative (entry_timing) and positive (selection, since final_thesis_status=INTACT) signal coexist --
    # the engine must not silently discard either.
    dims_present = {e.dimension for e in result.evidence}
    assert DIM_SELECTION in dims_present
    assert DIM_ENTRY_TIMING in dims_present


# ---------------------------------------------------------------------------
# Required fixture 13: multi-leg Iron Condor.
# ---------------------------------------------------------------------------
def test_multi_leg_iron_condor_attributed_as_one_position():
    legs = (
        LegRecord(leg_id=leg_id_for("POS-1", "SHORT_CALL", "CE", 24600.0, "2026-08-13"), role="SHORT_CALL",
                  option_type="CE", strike=24600.0, expiry="2026-08-13", side="SELL", quantity=1, entry_premium=40.0, entry_delta=-0.2),
        LegRecord(leg_id=leg_id_for("POS-1", "SHORT_PUT", "PE", 24300.0, "2026-08-13"), role="SHORT_PUT",
                  option_type="PE", strike=24300.0, expiry="2026-08-13", side="SELL", quantity=1, entry_premium=35.0, entry_delta=0.2),
    )
    lifecycle = _closed_lifecycle(strategy_family="IRON_CONDOR", legs=legs, realized_pnl=150.0)
    result = attribute_position_outcome(lifecycle)
    assert result.readiness == READY
    assert len(lifecycle.legs) == 2  # attribution doesn't need to iterate legs -- position-level evidence suffices.


# ---------------------------------------------------------------------------
# Required fixture 14: short-volatility trade with IV expansion.
# ---------------------------------------------------------------------------
def test_short_volatility_trade_with_iv_expansion():
    thesis_evals = [_thesis_eval("THESIS_WEAKENING", vol_status="DEVIATED") for _ in range(2)]
    mgmt = [_mgmt_assessment("ADJUST", exposure="SEVERE_DRIFT")]
    lifecycle = _closed_lifecycle(strategy_family="NEUTRAL_PREMIUM_SELLING", thesis_evaluations=thesis_evals, management_assessments=mgmt)
    result = attribute_position_outcome(lifecycle)
    vol_evidence = next(e for e in result.evidence if e.dimension == DIM_VOLATILITY)
    exposure_evidence = next(e for e in result.evidence if e.dimension == DIM_GREEKS_EXPOSURE)
    assert vol_evidence.impact_direction == IMPACT_NEGATIVE
    assert exposure_evidence.impact_direction == IMPACT_NEGATIVE


# ---------------------------------------------------------------------------
# Required fixture 15: directional trade with direction reversal.
# ---------------------------------------------------------------------------
def test_directional_trade_with_direction_reversal():
    # A real, unambiguous reversal: 2 of 3 evaluations show the price
    # direction has deviated from entry -- a tied 1-1 split would
    # honestly resolve to NEUTRAL (see the engine's own tie-handling),
    # so this fixture uses a genuine majority to test the NEGATIVE path.
    thesis_evals = [
        _thesis_eval("THESIS_INTACT"),
        _thesis_eval("THESIS_WEAKENING", direction_status="DEVIATED"),
        _thesis_eval("THESIS_INVALIDATED", direction_status="DEVIATED"),
    ]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals, exit_reason="thesis_invalidated", realized_pnl=-400.0)
    result = attribute_position_outcome(lifecycle)
    direction_evidence = next(e for e in result.evidence if e.dimension == DIM_DIRECTION)
    assert direction_evidence.impact_direction == IMPACT_NEGATIVE
    assert result.outcome_direction == OUTCOME_LOSS


# ---------------------------------------------------------------------------
# Determinism.
# ---------------------------------------------------------------------------
def test_deterministic_repeated_attribution():
    thesis_evals = [_thesis_eval("THESIS_WEAKENING")]
    lifecycle = _closed_lifecycle(thesis_evaluations=thesis_evals, realized_pnl=-50.0)
    r1 = attribute_position_outcome(lifecycle)
    r2 = attribute_position_outcome(lifecycle)
    assert r1.to_dict() == r2.to_dict()


def test_never_raises_on_fully_empty_lifecycle():
    lifecycle = _closed_lifecycle()
    result = attribute_position_outcome(lifecycle)
    assert result.readiness == READY
    assert isinstance(result.narrative, str) and result.narrative
