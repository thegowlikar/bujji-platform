"""Phase 15N Step 9 -- Outcome Memory query correctness. Proves queries
never silently mix incompatible cohorts and correctly return
INSUFFICIENT_HISTORY rather than a statistically-impressive-looking
conclusion from a tiny sample."""
from __future__ import annotations

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.outcome_memory.engine import build_outcome_memory_record
from bujji.outcome_memory.query import (
    INSUFFICIENT_HISTORY, MIN_SAMPLE_SIZE, SUFFICIENT,
    filter_records, query_attribution_cause_distribution, query_management_recommendation_outcomes,
    query_outcome_distribution, query_pnl_summary, query_regime_performance, query_thesis_invalidation_rate,
)
from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
    build_position_opened_payload, build_structured_exit,
)


class Leg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 1, 100.0, 0.5
    entry_bid, entry_ask = 98.0, 102.0


class Candidate:
    def __init__(self, cid, family="LONG_DIRECTIONAL", regime="RANGING", underlying="NIFTY"):
        self.candidate_id, self.source_cycle_id, self.strategy_family = cid, "t0", family
        self.timestamp, self.market_regime, self.direction, self.thesis = "t0", regime, "BULLISH", "X"
        self.selection_confidence, self.underlying_price, self.legs = "HIGH", 24450.0, (Leg(),)
        self.lot_size = 75
        self.underlying_symbol = underlying


def _record(cid, exit_price, family="LONG_DIRECTIONAL", regime="RANGING", underlying="NIFTY", session_id="S1"):
    candidate = Candidate(cid, family, regime, underlying)
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload(session_id, candidate, entry, "t0")
    pid = open_payload["position_id"]
    states, _ = apply_event({}, session_id, "POSITION_OPENED", session_id, open_payload)
    leg = states[pid].legs[0]
    structured_exit = build_structured_exit((leg,), 75, "t5", "manual", {leg.leg_id: {"exit_price": exit_price}})
    states, _ = apply_event(states, session_id, "POSITION_CLOSED", session_id,
                             build_position_closed_payload(pid, "t5", "manual", structured_exit))
    attribution = attribute_position_outcome(states[pid])
    return build_outcome_memory_record(states[pid], attribution, "t6")


def test_small_sample_reports_insufficient_history():
    records = [_record("C1", 150.0)]  # only 1 record -- below MIN_SAMPLE_SIZE.
    result = query_outcome_distribution(records)
    assert result.status == INSUFFICIENT_HISTORY
    assert result.sample_size == 1


def test_sufficient_sample_reports_sufficient():
    records = [_record(f"C{i}", 150.0) for i in range(MIN_SAMPLE_SIZE)]
    result = query_outcome_distribution(records)
    assert result.status == SUFFICIENT


def test_filter_by_strategy_family_never_mixes_families():
    records = [
        _record("C1", 150.0, family="LONG_DIRECTIONAL"),
        _record("C2", 60.0, family="LONG_DIRECTIONAL"),
        _record("C3", 150.0, family="IRON_CONDOR"),
    ]
    cohort = filter_records(records, strategy_family="LONG_DIRECTIONAL")
    assert len(cohort) == 2
    assert all(r.strategy_family == "LONG_DIRECTIONAL" for r in cohort)


def test_filter_by_regime_never_mixes_regimes():
    records = [
        _record("C1", 150.0, regime="RANGING"),
        _record("C2", 150.0, regime="TRENDING"),
    ]
    result = query_regime_performance(records, entry_regime="RANGING")
    assert result.sample_size == 1


def test_filter_by_underlying_never_mixes_underlyings():
    records = [
        _record("C1", 150.0, underlying="NIFTY"),
        _record("C2", 150.0, underlying="BANKNIFTY"),
    ]
    cohort = filter_records(records, underlying_symbol="NIFTY")
    assert len(cohort) == 1
    assert cohort[0].underlying_symbol == "NIFTY"


def test_session_isolation_in_filter():
    records = [
        _record("C1", 150.0, session_id="SESSION-A"),
        _record("C1", 150.0, session_id="SESSION-B"),  # same candidate_id, different session -- different memory_id.
    ]
    assert records[0].memory_id != records[1].memory_id
    cohort = filter_records(records, session_id="SESSION-A")
    assert len(cohort) == 1


def test_pnl_summary_never_treats_unknown_as_zero():
    from bujji.outcome_memory.engine import apply_event as mem_apply_event
    # One record with real P&L, one with legacy/unknown P&L.
    profitable = _record("C1", 150.0)
    candidate = Candidate("C2")
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    pid = open_payload["position_id"]
    states, _ = apply_event({}, "S1", "POSITION_OPENED", "S1", open_payload)
    states, _ = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "manual", None))
    attribution = attribute_position_outcome(states[pid])
    unknown_pnl_record = build_outcome_memory_record(states[pid], attribution, "t6")

    result = query_pnl_summary([profitable, unknown_pnl_record])
    assert result.result["total_realized_pnl"] == 50.0 * 75  # unknown record contributes NOTHING, never zero.
    assert result.result["unknown_pnl_count"] == 1
    assert result.result["known_pnl_count"] == 1


def test_thesis_invalidation_rate_uses_real_field():
    records = [_record("C1", 150.0)]
    result = query_thesis_invalidation_rate(records)
    assert "thesis_status_counts" in result.result


def test_management_recommendation_outcomes_excludes_not_applicable():
    """Records with NO management event at all must never be counted as
    'management recommended X' -- they simply have no matching assessment."""
    records = [_record("C1", 150.0)]  # no management assessments recorded.
    result = query_management_recommendation_outcomes(records, "HEDGE")
    assert result.sample_size == 0


def test_attribution_cause_distribution_is_real_field_readback():
    records = [_record("C1", 150.0), _record("C2", 60.0)]
    result = query_attribution_cause_distribution(records)
    assert sum(result.result["primary_cause_counts"].values()) == 2
