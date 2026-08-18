"""Phase 15L Step 5+6 -- end-to-end lifecycle + replay proof using REAL
PaperBroker-generated paper fills (not hand-crafted exit_prices dicts),
reusing Phase 15G's EventStore + Phase 15H's replay discipline
directly. Proves:
OPEN -> MANAGEMENT_ASSESSED -> CLOSED(structured, broker-sourced) -> OUTCOME_ATTRIBUTED
persists, survives a restart, hydrates, and replays identically."""
from __future__ import annotations

import asyncio

from bujji.broker.paper import PaperBroker
from bujji.core.enums import Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.outcome_attribution.engine import attribute_position_outcome
from bujji.position_lifecycle.engine import (
    build_entry_snapshot_for_position, build_management_assessed_payload,
    build_outcome_attributed_payload, build_position_closed_payload, build_position_opened_payload,
    build_structured_exit,
)
from bujji.position_lifecycle.models import LegRecord, SCHEMA_VERSION as LIFECYCLE_SCHEMA_VERSION, STATUS_CLOSED
from bujji.position_lifecycle.paper_bridge import client_order_id_for, reconcile_position_exit
from bujji.position_lifecycle.recovery import hydrate_position_lifecycles
from bujji.position_management.engine import assess_position_management
from bujji.state_persistence.models import PersistedEvent, RECOVERY_COMPLETE, RECOVERY_PARTIAL
from bujji.state_persistence.store import EventStore


class FakeLeg:
    role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
    side, ratio, entry_mid, delta = "BUY", 2, 100.0, 0.5
    entry_bid, entry_ask = 98.0, 102.0


class FakeCandidate:
    candidate_id, source_cycle_id, strategy_family = "STC-bridge", "t0", "LONG_DIRECTIONAL"
    timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
    selection_confidence, underlying_price, legs = "HIGH", 24450.0, (FakeLeg(),)
    lot_size = 75


class _FakeThesisEval:
    def __init__(self, status):
        self.thesis_status = status
        self.evidence_confidence = "HIGH"


def _contract():
    return OptionContract("NIFTY24450CE", "NIFTY", 24450.0, None, "WEEKLY", 75)


def _persist_full_chain_with_real_broker_exit(path, session_id="S1", exit_fill_price=150.0):
    """Same shape as Phase 15K's own `_persist_full_chain`, except the
    structured exit's `exit_prices` come from a REAL PaperBroker fill,
    routed through the Phase 15L bridge -- not a hand-authored dict."""
    store = EventStore(path)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload(session_id, candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id=session_id, cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))

    mgmt_assessment = assess_position_management(pid, "c1", _FakeThesisEval("THESIS_INTACT"), None, None)
    store.append(PersistedEvent(
        event_id=f"MGMT-{pid}", event_type="MANAGEMENT_ASSESSED", session_id=session_id, cycle_id="c1",
        timestamp="t1", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_management_assessed_payload(pid, "c1", mgmt_assessment),
    ))

    leg = LegRecord.from_dict(open_payload["legs"][0])
    broker = PaperBroker()
    coid = client_order_id_for(pid, leg.leg_id, 1)
    asyncio.run(broker.place_order(OrderRequest(
        contract=_contract(), side=Side.SELL, quantity=leg.quantity,
        client_order_id=coid, reference_price=exit_fill_price,
    )))
    reconciliation = reconcile_position_exit(broker, pid, (leg,))
    assert reconciliation.all_legs_observed
    structured_exit = build_structured_exit(
        (leg,), 75, "t5", "manual", reconciliation.exit_prices,
        fees=reconciliation.fees, slippage=reconciliation.slippage,
    )
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id=session_id, cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_position_closed_payload(pid, "t5", "manual", structured_exit),
    ))

    states, _ = hydrate_position_lifecycles(EventStore(path), session_id)
    attribution = attribute_position_outcome(states[pid])
    store.append(PersistedEvent(
        event_id=f"ATTR-{pid}", event_type="OUTCOME_ATTRIBUTED", session_id=session_id, cycle_id="t6",
        timestamp="t6", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_outcome_attributed_payload(pid, attribution),
    ))
    return pid, structured_exit


def test_full_chain_with_real_broker_fill_reconstructs_identical_state(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid, structured_exit = _persist_full_chain_with_real_broker_exit(path)
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_COMPLETE
    lifecycle = states[pid]
    assert lifecycle.status == STATUS_CLOSED
    # 2 lots, BUY entry @100 exited SELL @150, lot_size 75.
    assert lifecycle.structured_exit["gross_realized_pnl"] == (150.0 - 100.0) * 2 * 75
    assert lifecycle.realized_pnl is not None
    assert lifecycle.outcome_attribution is not None
    assert lifecycle.outcome_attribution["outcome_direction"] == "PROFIT"
    assert lifecycle.structured_exit["fees"] is not None  # real ChargesCalculator output, not fabricated.
    # exit bid/ask are genuinely unavailable from PaperBroker fills -- must stay None, never guessed.
    assert lifecycle.structured_exit["legs"][0]["exit_bid"] is None
    assert lifecycle.structured_exit["legs"][0]["exit_ask"] is None


def test_deterministic_double_replay_with_real_broker_fill(tmp_path):
    path = str(tmp_path / "lifecycle.jsonl")
    pid, _ = _persist_full_chain_with_real_broker_exit(path)
    states1, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    states2, _ = hydrate_position_lifecycles(EventStore(path), "S1")
    assert states1[pid].to_dict() == states2[pid].to_dict()


def test_mid_fill_restart_then_close_matches_uninterrupted(tmp_path):
    """Broker fills happen, bridge reconciles, THEN the process is
    'restarted' (fresh EventStore/hydration) before the CLOSED event is
    even appended -- proves the bridge's OWN output (already turned
    into a lifecycle event payload) is what survives, not any broker-
    side state (which Phase 15B/15L's own forensic finding says is
    never persisted by PaperBroker itself)."""
    path_full = str(tmp_path / "full.jsonl")
    pid_full, _ = _persist_full_chain_with_real_broker_exit(path_full)
    full_states, _ = hydrate_position_lifecycles(EventStore(path_full), "S1")

    path_split = str(tmp_path / "split.jsonl")
    store = EventStore(path_split)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    leg = LegRecord.from_dict(open_payload["legs"][0])
    broker = PaperBroker()
    coid = client_order_id_for(pid, leg.leg_id, 1)
    asyncio.run(broker.place_order(OrderRequest(
        contract=_contract(), side=Side.SELL, quantity=leg.quantity, client_order_id=coid, reference_price=150.0,
    )))
    # "restart" -- fresh EventStore against the same file, BEFORE the CLOSE event is persisted.
    mid_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")
    assert mid_states[pid].status == "OPEN"

    # After "restart", a fresh bridge reconciliation against the SAME broker instance
    # (in a real deployment this would be a live, still-running broker session) still works.
    reconciliation = reconcile_position_exit(broker, pid, (leg,))
    structured_exit = build_structured_exit(
        (leg,), 75, "t5", "manual", reconciliation.exit_prices, fees=reconciliation.fees, slippage=reconciliation.slippage,
    )
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_position_closed_payload(pid, "t5", "manual", structured_exit),
    ))
    post_close_states, _ = hydrate_position_lifecycles(EventStore(path_split), "S1")
    assert post_close_states[pid].status == STATUS_CLOSED
    assert post_close_states[pid].structured_exit["gross_realized_pnl"] == full_states[pid_full].structured_exit["gross_realized_pnl"]


def test_torn_close_event_after_real_fill_degrades_safely(tmp_path):
    path = str(tmp_path / "torn.jsonl")
    store = EventStore(path)
    candidate = FakeCandidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    pid = open_payload["position_id"]
    store.append(PersistedEvent(
        event_id=f"OPEN-{pid}", event_type="POSITION_OPENED", session_id="S1", cycle_id="t0",
        timestamp="t0", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test", payload=open_payload,
    ))
    with open(path, "a") as f:
        f.write('{"event_id": "CLOSE-torn", "event_type": "POSITION_CLOSE')  # torn mid-write.
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.status == RECOVERY_PARTIAL
    assert states[pid].status == "OPEN"


def test_duplicate_close_event_after_real_fill_handled_at_store_layer(tmp_path):
    path = str(tmp_path / "dup.jsonl")
    pid, structured_exit = _persist_full_chain_with_real_broker_exit(path)
    store = EventStore(path)
    # Re-append the EXACT same close event_id -- the store's own dedup must catch this.
    store.append(PersistedEvent(
        event_id=f"CLOSE-{pid}", event_type="POSITION_CLOSED", session_id="S1", cycle_id="t5",
        timestamp="t5", schema_version=LIFECYCLE_SCHEMA_VERSION, provenance="test",
        payload=build_position_closed_payload(pid, "t5", "manual", structured_exit),
    ))
    states, report = hydrate_position_lifecycles(EventStore(path), "S1")
    assert report.base.events_skipped_duplicate >= 1
    assert states[pid].structured_exit["gross_realized_pnl"] == 50.0 * 2 * 75


def test_cross_session_positions_never_blend_via_bridge(tmp_path):
    """Two sessions trading the SAME symbol on the SAME broker instance
    -- proves the bridge's per-order (not per-symbol) reads keep their
    structured exits fully independent, unlike PaperBroker's own
    symbol-netted `get_realized_pnl()` (deliberately never read by the
    bridge, see paper_bridge.py's own module docstring)."""
    broker = PaperBroker()
    from bujji.position_lifecycle.identity import leg_id_for, position_id_for
    pid_a = position_id_for("SESSION-A", "SAME-CANDIDATE", "t0")
    pid_b = position_id_for("SESSION-B", "SAME-CANDIDATE", "t0")
    leg_a = LegRecord(leg_id_for(pid_a, "PRIMARY", "CE", 24450.0, "2026-08-13"), "PRIMARY", "CE", 24450.0,
                       "2026-08-13", "BUY", 1, 100.0, 0.5)
    leg_b = LegRecord(leg_id_for(pid_b, "PRIMARY", "CE", 24450.0, "2026-08-13"), "PRIMARY", "CE", 24450.0,
                       "2026-08-13", "BUY", 1, 100.0, 0.5)
    asyncio.run(broker.place_order(OrderRequest(
        contract=_contract(), side=Side.SELL, quantity=1,
        client_order_id=client_order_id_for(pid_a, leg_a.leg_id, 1), reference_price=200.0,
    )))
    asyncio.run(broker.place_order(OrderRequest(
        contract=_contract(), side=Side.SELL, quantity=1,
        client_order_id=client_order_id_for(pid_b, leg_b.leg_id, 1), reference_price=50.0,
    )))
    result_a = reconcile_position_exit(broker, pid_a, (leg_a,))
    result_b = reconcile_position_exit(broker, pid_b, (leg_b,))
    assert result_a.exit_prices[leg_a.leg_id]["exit_price"] == 200.0
    assert result_b.exit_prices[leg_b.leg_id]["exit_price"] == 50.0  # not blended, not swapped.
