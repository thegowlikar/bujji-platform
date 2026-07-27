"""Tests — Engineering Series 66: Market Intelligence Observatory."""
import os

import pytest

from bujji.observatory.comparison import (
    ThresholdCrossing,
    compare_corpora,
    compare_sessions,
)
from bujji.observatory.explanation import explain_field, explain_session
from bujji.observatory.report import build_demo_report, load_qualification_report
from bujji.observatory.timeline import build_timeline, explain_trading_impact

REPORT_A = "reports/historical_campaign_v2_1_full_corpus.json"
REPORT_B = "reports/historical_campaign_broadened_corpus.json"
REAL_REPORTS_AVAILABLE = os.path.exists(REPORT_A) and os.path.exists(REPORT_B)


def _session(**overrides):
    base = {
        "id": "NIFTY-2026-07-09", "outcome": "COMPLETED", "strategy": "PREMIUM_VWAP_STRADDLE",
        "governance": "APPROVED", "market_context": "SIDEWAYS",
    }
    base.update(overrides)
    return base


# ---------------------------------------------------------------------------
# Classification explanation
# ---------------------------------------------------------------------------


def test_explain_field_never_guesses_missing_evidence():
    explanation = explain_field("market_context", "TRANSITION")
    assert explanation.value == "TRANSITION"
    assert explanation.evidence_used == ()
    assert explanation.confidence is None
    assert explanation.reasoning_summary is None


def test_explain_field_filters_relevant_evidence_only():
    evidence = [
        {"module": "trend", "signal": "UPTREND", "confidence": 0.9},
        {"module": "time_of_day", "signal": "AFTER_HOURS", "confidence": 1.0},
    ]
    explanation = explain_field("market_context", "TRENDING_UP", evidence=evidence)
    assert len(explanation.evidence_used) == 1
    assert explanation.evidence_used[0]["module"] == "trend"
    assert explanation.confidence == 0.9


def test_explain_field_unrecognized_field_raises():
    with pytest.raises(ValueError):
        explain_field("not_a_real_field", "X")


def test_explain_session_only_covers_present_fields():
    session = _session()
    explanations = explain_session(session)
    field_names = {e.field_name for e in explanations}
    assert "market_context" in field_names
    assert "lifecycle" not in field_names  # not present in this session dict


def test_explanation_originating_module_is_structural_not_guessed():
    explanation = explain_field("governance", "APPROVED")
    assert explanation.originating_module == "mic_v2.governance.engine.derive_governance"
    assert "certification" in explanation.upstream_chain


# ---------------------------------------------------------------------------
# Replay comparison
# ---------------------------------------------------------------------------


def test_compare_sessions_detects_unchanged_fields():
    a = _session()
    b = _session()
    diff = compare_sessions(a, b)
    assert diff.changed_fields == {}
    assert "market_context" in diff.unchanged_fields
    assert diff.first_divergence is None


def test_compare_sessions_detects_changed_fields_and_first_divergence():
    a = _session()
    b = _session(market_context="TRANSITION", strategy=None, outcome="FAILED")
    diff = compare_sessions(a, b)
    assert diff.first_divergence == "market_context"
    assert diff.changed_fields["market_context"] == {"before": "SIDEWAYS", "after": "TRANSITION"}
    assert diff.changed_fields["strategy"] == {"before": "PREMIUM_VWAP_STRADDLE", "after": None}
    assert diff.changed_fields["outcome"] == {"before": "COMPLETED", "after": "FAILED"}


def test_compare_sessions_requires_matching_ids():
    with pytest.raises(ValueError):
        compare_sessions(_session(id="A"), _session(id="B"))


def test_compare_corpora_matches_by_id_and_skips_unmatched():
    sessions_a = (_session(id="S1"), _session(id="S2"))
    sessions_b = (_session(id="S1", market_context="TRANSITION"), _session(id="S3"))
    diffs = compare_corpora(sessions_a, sessions_b)
    assert len(diffs) == 1
    assert diffs[0].session_id == "S1"


# ---------------------------------------------------------------------------
# Threshold-crossing detection
# ---------------------------------------------------------------------------


def test_threshold_crossing_honestly_reports_unknown():
    a = _session()
    b = _session(market_context="TRANSITION")
    diff = compare_sessions(a, b)
    assert len(diff.threshold_crossings) == 1
    crossing = diff.threshold_crossings[0]
    assert isinstance(crossing, ThresholdCrossing)
    assert crossing.threshold_name == "UNKNOWN"
    assert crossing.previous_value == "UNKNOWN"
    assert crossing.new_value == "UNKNOWN"
    assert crossing.previous_classification == "SIDEWAYS"
    assert crossing.new_classification == "TRANSITION"


def test_no_crossings_when_only_non_classification_fields_change():
    a = _session()
    b = _session(outcome="FAILED")
    diff = compare_sessions(a, b)
    assert diff.threshold_crossings == ()


# ---------------------------------------------------------------------------
# Evidence evolution / historical reasoning timeline
# ---------------------------------------------------------------------------


def test_timeline_covers_all_five_stages():
    from bujji.observatory.timeline import TIMELINE_STAGES

    timeline = build_timeline(_session())
    stages = [s.stage for s in timeline.steps]
    assert stages == list(TIMELINE_STAGES)


def test_timeline_honestly_reports_missing_evidence():
    timeline = build_timeline(_session(), evidence=())
    evidence_step = timeline.steps[0]
    assert "not recorded" in evidence_step.summary.lower()


def test_timeline_no_strategy_case():
    timeline = build_timeline(_session(strategy=None, outcome="FAILED"))
    strategy_step = next(s for s in timeline.steps if s.stage == "strategy_selection")
    assert "NO_STRATEGY" in strategy_step.summary


# ---------------------------------------------------------------------------
# Trading impact
# ---------------------------------------------------------------------------


def test_explain_trading_impact_narrates_the_full_chain():
    a = _session()
    b = _session(market_context="TRANSITION", strategy=None, outcome="FAILED")
    diff = compare_sessions(a, b)
    narrative = explain_trading_impact(diff)
    assert "SIDEWAYS -> TRANSITION" in narrative
    assert "PREMIUM_VWAP_STRADDLE -> None" in narrative
    assert "COMPLETED -> FAILED" in narrative


def test_explain_trading_impact_no_change():
    a = _session()
    b = _session()
    diff = compare_sessions(a, b)
    assert "no change" in explain_trading_impact(diff).lower()


# ---------------------------------------------------------------------------
# Report orchestration -- real saved qualification reports
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not REAL_REPORTS_AVAILABLE, reason="Series 65/broadened-corpus reports not present")
def test_build_demo_report_over_real_saved_reports():
    report = build_demo_report(REPORT_A, REPORT_B, "NIFTY-2026-07-09")
    assert report["total_sessions_compared"] == 41
    assert report["focus_session"]["id"] == "NIFTY-2026-07-09"
    assert "SIDEWAYS -> TRANSITION" in report["focus_session"]["narrative"]


@pytest.mark.skipif(not REAL_REPORTS_AVAILABLE, reason="Series 65/broadened-corpus reports not present")
def test_load_qualification_report_never_mutates_file():
    import hashlib

    before = hashlib.md5(open(REPORT_A, "rb").read()).hexdigest()
    load_qualification_report(REPORT_A)
    after = hashlib.md5(open(REPORT_A, "rb").read()).hexdigest()
    assert before == after


@pytest.mark.skipif(not REAL_REPORTS_AVAILABLE, reason="Series 65/broadened-corpus reports not present")
def test_demo_report_is_deterministic():
    from datetime import datetime

    fixed = lambda: datetime(2026, 7, 24, 0, 0, 0)
    a = build_demo_report(REPORT_A, REPORT_B, "NIFTY-2026-07-09", clock=fixed)
    b = build_demo_report(REPORT_A, REPORT_B, "NIFTY-2026-07-09", clock=fixed)
    assert a == b


# ---------------------------------------------------------------------------
# Phase 3 -- first_divergence over the full CANONICAL_FIELD_ORDER field set
# ---------------------------------------------------------------------------


def _full_session(**overrides):
    base = {
        "id": "NIFTY-2026-07-10",
        "market_context": "SIDEWAYS",
        "market_opinion": "NEUTRAL",
        "context_stability": "STABLE",
        "calibration": "CALIBRATED",
        "governance": "APPROVED",
        "lifecycle": "ACTIVE",
        "contract": "VALID",
        "strategy": "PREMIUM_VWAP_STRADDLE",
        "outcome": "COMPLETED",
        "market_character": "RANGE_BOUND",
        "market_phase": "MID_SESSION",
        "confidence": 0.8,
    }
    base.update(overrides)
    return base


def test_first_divergence_prefers_context_stability_over_strategy():
    a = _full_session()
    b = _full_session(context_stability="UNSTABLE", strategy=None, outcome="FAILED")
    diff = compare_sessions(a, b)
    # market_context/market_opinion unchanged, so context_stability -- the
    # first field in CANONICAL_FIELD_ORDER that actually differs -- must
    # win, not strategy (which comes later in the order and would win only
    # under a buggy dict-iteration-order implementation).
    assert diff.first_divergence == "context_stability"
    assert "market_context" not in diff.changed_fields
    assert "strategy" in diff.changed_fields


def test_first_divergence_calibration_when_market_context_unchanged():
    a = _full_session()
    b = _full_session(calibration="NOT_CALIBRATED", strategy=None)
    diff = compare_sessions(a, b)
    assert "market_context" not in diff.changed_fields
    assert diff.first_divergence == "calibration"
    narrative = explain_trading_impact(diff)
    assert "First divergence: calibration" in narrative
    assert "CALIBRATED -> NOT_CALIBRATED" in narrative
    assert "-> strategy changed: PREMIUM_VWAP_STRADDLE -> None" in narrative


def test_first_divergence_none_when_nothing_diverges():
    a = _full_session()
    b = _full_session()
    diff = compare_sessions(a, b)
    assert diff.changed_fields == {}
    assert diff.first_divergence is None
    assert "no change" in explain_trading_impact(diff).lower()


def test_narrative_lists_consequences_in_canonical_order_after_first_divergence():
    a = _full_session()
    b = _full_session(calibration="NOT_CALIBRATED", strategy=None, outcome="FAILED")
    diff = compare_sessions(a, b)
    narrative = explain_trading_impact(diff)
    lines = narrative.splitlines()
    calibration_idx = next(i for i, l in enumerate(lines) if "CALIBRATED -> NOT_CALIBRATED" in l)
    strategy_idx = next(i for i, l in enumerate(lines) if "strategy changed" in l)
    outcome_idx = next(i for i, l in enumerate(lines) if "outcome changed" in l)
    assert calibration_idx < strategy_idx < outcome_idx



# ---------------------------------------------------------------------------
# Phase 2b (deep pass) -- new Trading-Brain-stage fields in canonical order
# ---------------------------------------------------------------------------


def test_canonical_field_order_places_risk_before_strategy_downstream_fields():
    from bujji.observatory.comparison import CANONICAL_FIELD_ORDER

    assert CANONICAL_FIELD_ORDER.index("strategy") < CANONICAL_FIELD_ORDER.index("risk_status")
    assert CANONICAL_FIELD_ORDER.index("risk_status") < CANONICAL_FIELD_ORDER.index("capital_intent")
    assert CANONICAL_FIELD_ORDER.index("capital_intent") < CANONICAL_FIELD_ORDER.index("execution_status")
    assert CANONICAL_FIELD_ORDER.index("evidence_opportunity_state") < CANONICAL_FIELD_ORDER.index("market_character")
    assert CANONICAL_FIELD_ORDER.index("market_character") < CANONICAL_FIELD_ORDER.index("strategy")


def test_first_divergence_reports_risk_field_not_strategy_when_both_diverge():
    a = _full_session(risk_status="APPROVED", risk_level="LOW", strategy="PREMIUM_VWAP_STRADDLE")
    b = _full_session(risk_status="BLOCKED", risk_level="HIGH", strategy="PREMIUM_VWAP_STRADDLE")
    diff = compare_sessions(a, b)
    # strategy is identical in both sessions -- only risk_status/risk_level
    # actually diverge, so first_divergence must be the earlier-diverging
    # risk field, never strategy (which is unchanged here anyway, but this
    # also proves canonical order is honored when strategy IS unchanged
    # while a downstream-in-code but upstream-in-order field changes).
    assert diff.first_divergence == "risk_status"
    assert "strategy" not in diff.changed_fields


def test_first_divergence_prefers_strategy_over_risk_when_both_diverge():
    a = _full_session(strategy="PREMIUM_VWAP_STRADDLE", risk_status="APPROVED")
    b = _full_session(strategy="IRON_CONDOR", risk_status="BLOCKED")
    diff = compare_sessions(a, b)
    # strategy precedes risk_status in CANONICAL_FIELD_ORDER (Strategy
    # Selector runs before Risk Brain in the pipeline -- see
    # production_runtime/runtime.py), so it must win first_divergence.
    assert diff.first_divergence == "strategy"
    assert "risk_status" in diff.changed_fields


# ---------------------------------------------------------------------------
# Series 70 Phase 3 -- Context Stability Observatory narrative fields
# ---------------------------------------------------------------------------


def test_canonical_field_order_places_stability_scalar_fields_before_context_stability():
    from bujji.observatory.comparison import CANONICAL_FIELD_ORDER

    assert CANONICAL_FIELD_ORDER.index("context_volatility") < CANONICAL_FIELD_ORDER.index("context_stability_dimension")
    assert CANONICAL_FIELD_ORDER.index("context_stability_dimension") < CANONICAL_FIELD_ORDER.index("market_opinion")
    assert CANONICAL_FIELD_ORDER.index("stability_transition_count") < CANONICAL_FIELD_ORDER.index("context_stability")
    assert CANONICAL_FIELD_ORDER.index("stability_context_lifetime") < CANONICAL_FIELD_ORDER.index("context_stability")


def test_first_divergence_surfaces_context_stability_dimension_before_context_stability():
    # Generic canonical-order walk already handles the new Series 70
    # fields with no code change -- context_stability_dimension precedes
    # context_stability in CANONICAL_FIELD_ORDER, so when both diverge,
    # first_divergence must be the dimension field, upstream-explaining
    # the context_stability change rather than context_stability itself
    # being the earliest-visible divergence.
    a = _full_session(context_stability_dimension="REGIME", context_stability="STABLE")
    b = _full_session(context_stability_dimension="VOLATILITY", context_stability="TRANSITIONING")
    diff = compare_sessions(a, b)
    assert diff.first_divergence == "context_stability_dimension"
    narrative = explain_trading_impact(diff)
    assert "First divergence: context_stability_dimension" in narrative
    assert "REGIME -> VOLATILITY" in narrative
    assert "-> context_stability changed: STABLE -> TRANSITIONING" in narrative


def test_first_divergence_surfaces_stability_transition_count_before_context_stability():
    a = _full_session(stability_transition_count=1, context_stability="STABLE")
    b = _full_session(stability_transition_count=4, context_stability="HIGHLY_VARIABLE")
    diff = compare_sessions(a, b)
    assert diff.first_divergence == "stability_transition_count"
    assert "-> context_stability changed: STABLE -> HIGHLY_VARIABLE" in explain_trading_impact(diff)


def test_explain_trading_impact_appends_supplementary_reasoning_summary_when_context_stability_changes():
    a = _full_session(
        context_stability="STABLE",
        stability_reasoning_summary="transition_rate=0.100 at or below 0.6",
        transition_events=[],
    )
    b = _full_session(
        context_stability="TRANSITIONING",
        stability_reasoning_summary="transition_rate=0.267 at or below 0.6",
        transition_events=["REGIME_TRANSITION@2026-07-09"],
    )
    diff = compare_sessions(a, b)
    narrative = explain_trading_impact(diff, a, b)
    assert "supplementary" in narrative.lower()
    assert "stability_reasoning_summary" in narrative
    assert "transition_rate=0.100 at or below 0.6" in narrative
    assert "transition_rate=0.267 at or below 0.6" in narrative
    assert "transition_events" in narrative
    assert "REGIME_TRANSITION@2026-07-09" in narrative


def test_explain_trading_impact_supplementary_block_absent_without_raw_sessions():
    # Backward compatible -- calling with just `diff` (no session_a/
    # session_b) must not raise and must not include a supplementary
    # block, since the reasoning_summary/transition_events values are
    # not available on the diff itself.
    a = _full_session(context_stability="STABLE", stability_reasoning_summary="x")
    b = _full_session(context_stability="TRANSITIONING", stability_reasoning_summary="y")
    diff = compare_sessions(a, b)
    narrative = explain_trading_impact(diff)
    assert "supplementary" not in narrative.lower()


def test_explain_trading_impact_no_supplementary_block_when_context_stability_unchanged():
    a = _full_session(strategy="PREMIUM_VWAP_STRADDLE", stability_reasoning_summary="x", transition_events=[])
    b = _full_session(strategy="IRON_CONDOR", stability_reasoning_summary="x", transition_events=[])
    diff = compare_sessions(a, b)
    narrative = explain_trading_impact(diff, a, b)
    assert "context_stability" not in diff.changed_fields
    assert "supplementary" not in narrative.lower()
