"""Phase 15N Step 8 -- Outcome Memory reconstruction tests. Each test
proves one meaningful historical-reconstruction invariant, building
real `PositionLifecycle`/`PositionOutcomeAttribution` objects through
the real reducers (Phase 15G/15J), never hand-faking a memory record."""
from __future__ import annotations

from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.outcome_memory.engine import apply_event as mem_apply_event, build_outcome_memory_record, build_outcome_memory_recorded_payload
from bujji.outcome_memory.models import (
    STATUS_KNOWN, STATUS_NOT_APPLICABLE, STATUS_NOT_AVAILABLE, STATUS_UNKNOWN,
    TRANSITION_ACCEPTED, TRANSITION_IDEMPOTENT, TRANSITION_REJECTED, OutcomeMemoryRecord,
)
from bujji.position_lifecycle.engine import (
    apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
    build_position_closed_payload, build_position_opened_payload, build_structured_exit,
    build_thesis_evaluated_payload,
)
from bujji.position_management.engine import assess_position_management


class Leg:
    def __init__(self, role="PRIMARY", option_type="CE", strike=24450.0, side="BUY", entry_mid=100.0):
        self.role, self.option_type, self.strike, self.expiry = role, option_type, strike, "2026-08-13"
        self.side, self.ratio, self.entry_mid, self.delta = side, 1, entry_mid, 0.5
        self.entry_bid, self.entry_ask = entry_mid - 2, entry_mid + 2


class Candidate:
    def __init__(self, cid, legs=(Leg(),)):
        self.candidate_id, self.source_cycle_id, self.strategy_family = cid, "t0", "LONG_DIRECTIONAL"
        self.timestamp, self.market_regime, self.direction, self.thesis = "t0", "RANGING", "BULLISH", "X"
        self.selection_confidence, self.underlying_price, self.legs = "HIGH", 24450.0, legs
        self.lot_size = 75
        self.underlying_symbol = "NIFTY"


class ThesisEval:
    def __init__(self, status):
        self.thesis_status = status
        self.evidence_confidence = "HIGH"
        self.candidate_id = "c1"

    def to_dict(self):
        return {"thesis_status": self.thesis_status, "evidence_confidence": self.evidence_confidence}


def _open(candidate, cycle_record=None, session_id="S1", ts="t0"):
    entry = build_entry_snapshot_for_position(candidate, cycle_record)
    payload = build_position_opened_payload(session_id, candidate, entry, ts)
    states, _ = apply_event({}, session_id, "POSITION_OPENED", session_id, payload)
    return states, payload["position_id"]


def _close(states, pid, legs, exit_prices, session_id="S1", fees=None, slippage=None):
    structured_exit = build_structured_exit(legs, 75, "t5", "manual", exit_prices, fees=fees, slippage=slippage)
    payload = build_position_closed_payload(pid, "t5", "manual", structured_exit)
    return apply_event(states, session_id, "POSITION_CLOSED", session_id, payload)


def _close_legacy(states, pid, session_id="S1"):
    payload = build_position_closed_payload(pid, "t5", "manual", None)
    return apply_event(states, session_id, "POSITION_CLOSED", session_id, payload)


# 1. Profitable trade
def test_profitable_trade():
    states, pid = _open(Candidate("C1"))
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.outcome_direction == "PROFIT"
    assert record.realized_pnl == 50.0 * 75
    assert record.pnl_status == STATUS_KNOWN


# 2. Losing trade
def test_losing_trade():
    states, pid = _open(Candidate("C2"))
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 60.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.outcome_direction == "LOSS"
    assert record.realized_pnl == -40.0 * 75


# 3+4. Unknown outcome / unknown P&L (legacy close, no structured_exit)
def test_unknown_outcome_and_pnl():
    states, pid = _open(Candidate("C3"))
    states, _ = _close_legacy(states, pid)
    attribution = attribute_position_outcome(states[pid])
    assert attribution.readiness == "READY"
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.outcome_direction == "UNKNOWN"
    assert record.realized_pnl is None
    assert record.pnl_status == STATUS_UNKNOWN


# 5. Invalidated thesis
def test_invalidated_thesis():
    states, pid = _open(Candidate("C4"))
    payload = build_thesis_evaluated_payload(pid, "c1", ThesisEval("THESIS_INVALIDATED"))
    states, _ = apply_event(states, "S1", "THESIS_EVALUATED", "S1", payload)
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 60.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.final_thesis_status == "THESIS_INVALIDATED"


# 6. Intact thesis
def test_intact_thesis():
    states, pid = _open(Candidate("C5"))
    payload = build_thesis_evaluated_payload(pid, "c1", ThesisEval("THESIS_INTACT"))
    states, _ = apply_event(states, "S1", "THESIS_EVALUATED", "S1", payload)
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.final_thesis_status == "THESIS_INTACT"


def _with_management(states, pid, recommendation_status="THESIS_INTACT"):
    assessment = assess_position_management(pid, "c1", ThesisEval(recommendation_status), None, None)
    payload = build_management_assessed_payload(pid, "c1", assessment)
    return apply_event(states, "S1", "MANAGEMENT_ASSESSED", "S1", payload)


# 7-9. Management adjustment / hedge / roll -- recorded as whatever the real
# assess_position_management engine actually produces for the given thesis input
# (never hand-faked); assert the memory record faithfully carries it through.
def test_management_assessment_recorded_in_memory():
    states, pid = _open(Candidate("C6"))
    states, _ = apply_event(states, "S1", "THESIS_EVALUATED", "S1", build_thesis_evaluated_payload(pid, "c1", ThesisEval("THESIS_WEAKENING")))
    states, _ = _with_management(states, pid, "THESIS_WEAKENING")
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 90.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.management_status == STATUS_KNOWN
    assert len(record.lifecycle_snapshot["management_assessments"]) == 1
    recommendation = record.lifecycle_snapshot["management_assessments"][0]["recommendation"]
    assert recommendation in (
        "HOLD", "ADJUST", "HEDGE", "ROLL", "EXIT", "UNKNOWN",
    )


# 10. No management
def test_no_management_is_not_applicable():
    states, pid = _open(Candidate("C7"))
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.management_status == STATUS_NOT_APPLICABLE


# 11. Multi-leg trade
def test_multi_leg_trade():
    legs = (Leg("LONG_CE", "CE", 24600.0, "BUY"), Leg("SHORT_PE", "PE", 24300.0, "SELL"))
    states, pid = _open(Candidate("C8", legs=legs))
    real_legs = states[pid].legs
    exit_prices = {l.leg_id: {"exit_price": 120.0} for l in real_legs}
    states, _ = _close(states, pid, real_legs, exit_prices)
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.pnl_status == STATUS_KNOWN
    assert len(record.lifecycle_snapshot["legs"]) == 2


# 12. Partial exit (one leg's exit price genuinely unknown -> PNL_PARTIAL -> memory-level UNKNOWN)
def test_partial_exit_information_preserved_as_unknown():
    legs = (Leg("LEG1", "CE", 24450.0, "BUY"), Leg("LEG2", "PE", 24450.0, "SELL"))
    states, pid = _open(Candidate("C9", legs=legs))
    real_legs = states[pid].legs
    exit_prices = {real_legs[0].leg_id: {"exit_price": 150.0}}  # second leg has no evidence at all.
    states, _ = _close(states, pid, real_legs, exit_prices)
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.lifecycle_snapshot["structured_exit"]["pnl_status"] == "PARTIAL"
    assert record.pnl_status == STATUS_UNKNOWN  # partial resolution never presented as KNOWN at the memory level.


# 13. Execution slippage
def test_execution_slippage_preserved():
    states, pid = _open(Candidate("C10"))
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}}, fees=10.0, slippage=5.0)
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.lifecycle_snapshot["structured_exit"]["slippage"] == 5.0
    assert record.lifecycle_snapshot["structured_exit"]["fees"] == 10.0


# 14. Missing Greeks
def test_missing_greeks_is_unknown():
    states, pid = _open(Candidate("C11"), cycle_record=None)
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.greeks_status == STATUS_UNKNOWN


def test_present_greeks_is_known():
    cycle_record = {"greeks": {"strike": 24450.0, "ce": {"delta": 0.5}, "pe": {"delta": -0.5}}}
    states, pid = _open(Candidate("C12"), cycle_record=cycle_record)
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.greeks_status == STATUS_KNOWN


# 15. Missing premium behaviour
def test_missing_premium_behaviour_is_unknown():
    states, pid = _open(Candidate("C13"), cycle_record=None)
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record.premium_behaviour_status == STATUS_UNKNOWN


# 16. Old legacy record -- hand-built dict missing every Phase-15N-only field.
def test_old_legacy_record_hydrates_with_not_available():
    legacy_dict = {
        "memory_id": "MEM-legacy", "session_id": "S1", "position_id": "POS-old",
        "candidate_id": "C-old", "strategy_family": "LONG_DIRECTIONAL",
        "entry_timestamp": "t0", "exit_timestamp": "t5", "recorded_at": "t6",
        "lifecycle_snapshot": {}, "attribution_snapshot": {},
    }
    record = OutcomeMemoryRecord.from_dict(legacy_dict)
    assert record.management_status == STATUS_NOT_AVAILABLE
    assert record.greeks_status == STATUS_NOT_AVAILABLE
    assert record.premium_behaviour_status == STATUS_NOT_AVAILABLE
    assert record.portfolio_context_status == STATUS_NOT_AVAILABLE


# 17. Duplicate attribution -- same memory_id, identical content -> IDEMPOTENT.
def test_duplicate_attribution_is_idempotent():
    states, pid = _open(Candidate("C14"))
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    payload = build_outcome_memory_recorded_payload(record)
    mem_states, r1 = mem_apply_event({}, "OUTCOME_MEMORY_RECORDED", payload)
    mem_states, r2 = mem_apply_event(mem_states, "OUTCOME_MEMORY_RECORDED", payload)
    assert r1.outcome == TRANSITION_ACCEPTED
    assert r2.outcome == TRANSITION_IDEMPOTENT
    assert len(mem_states) == 1


# 18. Conflicting attribution -- same memory_id, DIFFERENT content -> REJECTED, original preserved.
def test_conflicting_attribution_rejected():
    states, pid = _open(Candidate("C15"))
    leg = states[pid].legs[0]
    states, _ = _close(states, pid, (leg,), {leg.leg_id: {"exit_price": 150.0}})
    attribution = attribute_position_outcome(states[pid])
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    payload = build_outcome_memory_recorded_payload(record)
    mem_states, _ = mem_apply_event({}, "OUTCOME_MEMORY_RECORDED", payload)

    tampered_dict = dict(record.to_dict())
    tampered_dict["realized_pnl"] = 999999.0  # conflicting content, same memory_id.
    tampered_payload = {"memory_id": record.memory_id, "record": tampered_dict}
    mem_states, r = mem_apply_event(mem_states, "OUTCOME_MEMORY_RECORDED", tampered_payload)
    assert r.outcome == TRANSITION_REJECTED
    assert mem_states[record.memory_id].realized_pnl == 50.0 * 75  # untouched -- immutable historical fact.


def test_not_ready_attribution_produces_no_record():
    """An OPEN position (never closed) has NOT_READY attribution --
    build_outcome_memory_record must return None, never a partial/
    speculative memory of an unfinished position."""
    states, pid = _open(Candidate("C16"))
    attribution = attribute_position_outcome(states[pid])
    assert attribution.readiness == "NOT_READY"
    record = build_outcome_memory_record(states[pid], attribution, "t6")
    assert record is None


def test_memory_id_deterministic_and_session_scoped():
    from bujji.outcome_memory.models import memory_id_for
    a = memory_id_for("S1", "POS-1", "t6")
    b = memory_id_for("S2", "POS-1", "t6")
    assert a != b
    assert a == memory_id_for("S1", "POS-1", "t6")
