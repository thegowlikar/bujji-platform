"""Phase 15O Step 4 -- the complete vertical slice, end to end.

EVIDENCE CLASS: SYNTHETIC/SEMANTIC FIXTURE. The `ShadowTradeCandidate`
driving these tests is a deterministic fixture, NOT a real runtime
candidate -- because the forensic audit proved no real one exists (all
708 persisted real cycles are `opportunity_state=MONITOR`, whose
base-eligible family set is deliberately empty, so no TradeIntent and
therefore no candidate can ever be constructed from them). Per Step 5,
this fixture proves the INTEGRATION PATH only and is never presented as
real-market validation. Everything downstream of the candidate --
PaperBroker fills, charges, slippage, P&L, attribution, memory -- is
produced by the REAL engines, not mocked."""
from __future__ import annotations

import asyncio

from bujji.broker.paper import PaperBroker
from bujji.outcome_memory.recovery import hydrate_outcome_memory
from bujji.position_lifecycle.models import STATUS_CLOSED, STATUS_OPEN
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.shadow_lifecycle.orchestrator import (
    NOT_OPENED_ALREADY_OPEN, NOT_OPENED_CANDIDATE_NOT_CONSTRUCTED, NOT_OPENED_NO_CANDIDATE, OPENED,
    close_position, monitor_position, open_position_from_candidate, record_outcome_memory,
)
from bujji.state_persistence.store import EventStore


class FixtureLeg:
    def __init__(self, role="PRIMARY", option_type="CE", strike=24450.0, side="BUY", entry_mid=100.0):
        self.role, self.option_type, self.strike, self.expiry = role, option_type, strike, "2026-08-13"
        self.side, self.ratio, self.entry_mid, self.delta = side, 1, entry_mid, 0.5
        self.entry_bid, self.entry_ask = entry_mid - 2, entry_mid + 2


class FixtureCandidate:
    """A deterministic stand-in with EXACTLY the attribute surface the
    real `ShadowTradeCandidate` exposes (verified against
    shadow_trade_construction/models.py) -- never a real runtime one."""

    def __init__(self, candidate_id="STC-fixture", legs=None, construction_status="CONSTRUCTED"):
        self.candidate_id, self.source_cycle_id, self.strategy_family = candidate_id, "t0", "LONG_DIRECTIONAL"
        self.timestamp, self.market_regime, self.direction, self.thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
        self.selection_confidence, self.underlying_price = "HIGH", 24450.0
        self.legs = legs if legs is not None else (FixtureLeg(),)
        self.lot_size, self.underlying_symbol = 75, "NIFTY"
        self.construction_status = construction_status


class FixtureThesisEval:
    def __init__(self, status="THESIS_INTACT"):
        self.thesis_status = status
        self.evidence_confidence = "HIGH"
        self.candidate_id = "STC-fixture"

    def to_dict(self):
        return {"thesis_status": self.thesis_status, "evidence_confidence": self.evidence_confidence}


def _run(coro):
    return asyncio.run(coro)


def test_full_vertical_slice_candidate_to_outcome_memory(tmp_path):
    """The phase's central proof: one candidate carried all the way to
    a durable memory record, through every real engine."""
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    memory_store = EventStore(str(tmp_path / "memory.jsonl"))
    broker = PaperBroker()
    states = {}

    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(), "c0", "t0", states,
    ))
    assert open_step.outcome == OPENED
    pid = open_step.position_id
    assert states[pid].status == STATUS_OPEN

    states, monitor_steps = monitor_position(
        lifecycle_store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INTACT"),
    )
    assert any(s.step == "THESIS_EVALUATED" and s.outcome == "ACCEPTED" for s in monitor_steps)
    assert any(s.step == "MANAGEMENT_ASSESSED" and s.outcome == "ACCEPTED" for s in monitor_steps)

    states, close_steps = _run(close_position(
        lifecycle_store, broker, "S1", pid, "c2", "t5", "session_end", states,
    ))
    assert states[pid].status == STATUS_CLOSED
    assert any(s.step == "OUTCOME_ATTRIBUTED" and s.outcome == "ACCEPTED" for s in close_steps)

    mem_step = record_outcome_memory(memory_store, "S1", pid, "c2", "t6", states)
    assert mem_step.outcome == "RECORDED"

    # The whole chain must survive a full rehydration from disk.
    rehydrated, report = hydrate_position_lifecycles(EventStore(str(tmp_path / "lifecycle.jsonl")), "S1")
    assert rehydrated[pid].status == STATUS_CLOSED
    assert rehydrated[pid].structured_exit is not None
    assert rehydrated[pid].outcome_attribution is not None
    assert len(rehydrated[pid].thesis_evaluations) == 1
    assert len(rehydrated[pid].management_assessments) == 1

    memories, _ = hydrate_outcome_memory(EventStore(str(tmp_path / "memory.jsonl")))
    assert len(memories) == 1


def test_real_paperbroker_fill_evidence_flows_into_pnl(tmp_path):
    """Proves the P&L is traced to ACTUAL PaperBroker fills (real
    ChargesCalculator output), not a hand-authored exit price."""
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(), "c0", "t0", states))
    pid = open_step.position_id
    states, _ = _run(close_position(lifecycle_store, broker, "S1", pid, "c2", "t5", "session_end", states))

    se = states[pid].structured_exit
    assert se["pnl_status"] == "COMPLETE"
    assert se["fees"] is not None and se["fees"] > 0  # real ChargesCalculator output.
    assert se["legs"][0]["exit_bid"] is None  # genuinely unavailable from a fill -- never fabricated.
    assert states[pid].realized_pnl is not None


def test_multi_leg_position_full_slice(tmp_path):
    legs = (FixtureLeg("LONG_CE", "CE", 24600.0, "BUY"), FixtureLeg("SHORT_PE", "PE", 24300.0, "SELL"))
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(legs=legs), "c0", "t0", states))
    assert open_step.outcome == OPENED
    pid = open_step.position_id
    assert len(states[pid].legs) == 2
    states, _ = _run(close_position(lifecycle_store, broker, "S1", pid, "c2", "t5", "session_end", states))
    assert states[pid].structured_exit["pnl_status"] == "COMPLETE"
    assert len(states[pid].structured_exit["legs"]) == 2


def test_unconstructed_candidate_is_declined_honestly(tmp_path):
    """The real-data case: every one of the 708 persisted real cycles
    produces exactly this -- an INVALID_INTENT candidate. The
    orchestrator must decline it explicitly, never fabricate a
    position from it."""
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states, step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(construction_status="INVALID_INTENT"), "c0", "t0", {},
    ))
    assert step.outcome == NOT_OPENED_CANDIDATE_NOT_CONSTRUCTED
    assert states == {}


def test_no_candidate_is_declined_honestly(tmp_path):
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states, step = _run(open_position_from_candidate(lifecycle_store, broker, "S1", None, "c0", "t0", {}))
    assert step.outcome == NOT_OPENED_NO_CANDIDATE


def test_second_open_while_one_is_active_is_declined(tmp_path):
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, first = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate("STC-a"), "c0", "t0", states))
    assert first.outcome == OPENED
    states, second = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate("STC-b"), "c1", "t1", states))
    assert second.outcome == NOT_OPENED_ALREADY_OPEN


def test_thesis_can_weaken_and_invalidate_across_cycles(tmp_path):
    """Step 7's explicit requirement: thesis must be able to remain
    intact, weaken, and invalidate -- each recorded faithfully."""
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(), "c0", "t0", states))
    pid = open_step.position_id
    for i, status in enumerate(["THESIS_INTACT", "THESIS_WEAKENING", "THESIS_INVALIDATED"]):
        states, _ = monitor_position(lifecycle_store, "S1", pid, f"c{i+1}", f"t{i+1}", states, FixtureThesisEval(status))
    assert len(states[pid].thesis_evaluations) == 3
    assert states[pid].final_thesis_status == "THESIS_INVALIDATED"
    recommendations = [a["recommendation"] for a in states[pid].management_assessments]
    assert len(recommendations) == 3
    assert all(r in ("HOLD", "ADJUST", "HEDGE", "ROLL", "EXIT", "UNKNOWN") for r in recommendations)


def test_management_assessment_never_places_an_order(tmp_path):
    """A management recommendation (even EXIT) is ADVISORY -- the
    orchestrator must never turn it into a broker order by itself."""
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(), "c0", "t0", states))
    pid = open_step.position_id
    calls_before = broker.place_calls
    states, _ = monitor_position(lifecycle_store, "S1", pid, "c1", "t1", states, FixtureThesisEval("THESIS_INVALIDATED"))
    assert broker.place_calls == calls_before  # monitoring placed ZERO orders.
    assert states[pid].status == STATUS_OPEN   # and never closed the position on its own.


def test_monitoring_a_closed_position_is_skipped(tmp_path):
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(), "c0", "t0", states))
    pid = open_step.position_id
    states, _ = _run(close_position(lifecycle_store, broker, "S1", pid, "c2", "t5", "session_end", states))
    states, steps = monitor_position(lifecycle_store, "S1", pid, "c3", "t6", states, FixtureThesisEval())
    assert steps[0].outcome == "SKIPPED"


def test_unknown_evidence_remains_unknown(tmp_path):
    """Step 7: UNKNOWN evidence must remain UNKNOWN. With no Greeks and
    no premium behaviour supplied, the management assessment must not
    invent them."""
    lifecycle_store = EventStore(str(tmp_path / "lifecycle.jsonl"))
    broker = PaperBroker()
    states = {}
    states, open_step = _run(open_position_from_candidate(
        lifecycle_store, broker, "S1", FixtureCandidate(), "c0", "t0", states))
    pid = open_step.position_id
    states, _ = monitor_position(lifecycle_store, "S1", pid, "c1", "t1", states,
                                  FixtureThesisEval(), greeks=None, premium_behaviour=None)
    assessment = states[pid].management_assessments[0]
    assert assessment["recommendation"] in ("HOLD", "ADJUST", "HEDGE", "ROLL", "EXIT", "UNKNOWN")
    # Greeks were genuinely absent -- the entry snapshot must reflect that, never a fabricated value.
    assert states[pid].entry.entry_greeks is None
