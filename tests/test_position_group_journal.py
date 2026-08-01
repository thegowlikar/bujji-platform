"""Tests — Numeric Risk Governor Gate A (position-group identity and
lifecycle foundation), final corrected contract."""
from __future__ import annotations

import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

import pytest

from bujji.core.process_lock import ProcessLock
from bujji.journal.position_group_journal import (
    EventSpec,
    JournalCorruptError,
    LinkageValidationError,
    MintUniquenessViolation,
    PartialLinkedBatchIntegrityError,
    PositionGroupJournal,
    ProcessLockNotHeldError,
)
from bujji.trading_brain.risk_governor.position_group_fill_reconciliation import (
    NonMonotonicFillReport,
    compute_fill_delta,
)
from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_ACKED,
    LEG_CANCEL_PENDING_UNKNOWN,
    LEG_CANCELLED,
    LEG_SUBMIT_FAILED,
    LEG_SUBMIT_PENDING_UNKNOWN,
    LIFECYCLE_ABORTED,
    LIFECYCLE_CLOSED,
    LIFECYCLE_MINTED,
    LIFECYCLE_OPEN,
    LIFECYCLE_PARTIALLY_OPEN,
    LIFECYCLE_PENDING_FINAL_RECONCILIATION,
    LIFECYCLE_UNRESOLVED,
    fold,
    net_quantity,
)
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
from bujji.trading_brain.risk_governor.position_group_recovery import recover_group
from bujji.journal.position_group_journal import IdempotencyKeyCollisionError
from bujji.trading_brain.risk_governor.position_group_validation import IllegalEventError


def _clock(iso="2026-07-31T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _journal(tmp_path):
    return PositionGroupJournal(tmp_path / "pg.db")


def _mint(journal, plan_id="PLAN-1", strategy_id="VERTICAL_SPREAD", underlying="NIFTY", clock=None):
    return mint_position_group_id(journal, plan_id, strategy_id, underlying, clock=clock or _clock())


def _construct(journal, pg, contract_client_order_map, requested_quantities=None, actions=None,
               target_position_group_ids=None, target_contract_ids=None, flip_link_ids=None, clock=None):
    return journal.append_event(
        pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
        {"contract_client_order_map": contract_client_order_map,
         "requested_quantities": requested_quantities or {},
         "actions": actions or {},
         "target_position_group_ids": target_position_group_ids or {},
         "target_contract_ids": target_contract_ids or {},
         "flip_link_ids": flip_link_ids or {}},
        clock=clock or _clock(),
    )


def _submit_intent(journal, pg, coid, clock=None):
    return journal.append_event(pg, "SUBMIT_INTENT", f"{pg}:SUBMIT_INTENT:{coid}",
                                 {"client_order_id": coid}, clock=clock or _clock())


def _submit_ack(journal, pg, coid, clock=None):
    return journal.append_event(pg, "SUBMIT_ACK", f"{pg}:SUBMIT_ACK:{coid}",
                                 {"client_order_id": coid, "broker_order_id": coid,
                                  "broker_reported_status": "ACCEPTED"}, clock=clock or _clock())


def _fill(journal, pg, coid, cum_qty, cum_price, clock=None, key_suffix=""):
    prior = fold(journal.read_events(pg)).legs[coid].fill
    delta = compute_fill_delta(prior, cum_qty, cum_price)
    ev = journal.append_event(
        pg, "FILL_OBSERVED", f"{pg}:FILL_OBSERVED:{coid}:{cum_qty}:{cum_price}{key_suffix}",
        {"client_order_id": coid, "cumulative_filled_quantity_after": cum_qty,
         "cumulative_average_fill_price_after": cum_price, "delta_quantity": delta.delta_quantity,
         "delta_value": delta.delta_value, "delta_cost_basis_status": delta.cost_basis_status,
         "fill_price": cum_price}, clock=clock or _clock(),
    )
    return ev, delta


# --------------------------------------------------------------------- #
# 1. Explicit bounded retry loop, real SQLITE_BUSY
# --------------------------------------------------------------------- #

def test_real_sqlite_busy_is_retried_and_eventually_succeeds(tmp_path):
    db_path = tmp_path / "busy.db"
    journal = PositionGroupJournal(db_path)
    mint = _mint(journal)

    blocker = sqlite3.connect(str(db_path), timeout=0.1, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("SELECT 1")  # hold the write lock without committing

    result = {}

    def release_after_delay():
        time.sleep(0.05)
        blocker.execute("COMMIT")
        blocker.close()

    releaser = threading.Thread(target=release_after_delay)
    releaser.start()

    ev = journal.append_event(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "busy-test-key",
        {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
    )
    releaser.join()
    assert ev is not None  # succeeded only because the retry loop waited out the real contention


def test_real_sqlite_busy_exhausts_retries_and_raises(tmp_path):
    db_path = tmp_path / "busy2.db"
    journal = PositionGroupJournal(db_path)
    mint = _mint(journal)

    blocker = sqlite3.connect(str(db_path), timeout=0.1)
    blocker.execute("BEGIN IMMEDIATE")
    blocker.execute("SELECT 1")
    try:
        with pytest.raises(sqlite3.OperationalError):
            journal.append_event(
                mint.position_group_id, "RECONCILIATION_ATTEMPTED", "busy-test-key-2",
                {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
            )
    finally:
        blocker.execute("COMMIT")
        blocker.close()


def test_corrupt_database_refuses_to_open(tmp_path):
    db_path = tmp_path / "corrupt.db"
    db_path.write_bytes(b"not a sqlite database")
    with pytest.raises(Exception):
        PositionGroupJournal(db_path)


# --------------------------------------------------------------------- #
# Transactional validation ordering: idempotency before validate
# --------------------------------------------------------------------- #

def test_idempotent_replay_succeeds_even_if_no_longer_a_legal_successor(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    ev1 = _submit_intent(journal, mint.position_group_id, "COID-1")
    assert ev1 is not None
    _submit_ack(journal, mint.position_group_id, "COID-1")
    # Leg is now ACKED. A replay of the ORIGINAL SUBMIT_INTENT event
    # (same idempotency_key) would be an illegal transition against the
    # CURRENT state (ACKED, not NOT_SUBMITTED) -- but it must still
    # succeed as a no-op because it's the exact same, already-recorded event.
    ev2 = journal.append_event(
        mint.position_group_id, "SUBMIT_INTENT", f"{mint.position_group_id}:SUBMIT_INTENT:COID-1",
        {"client_order_id": "COID-1"}, clock=_clock(),
    )
    assert ev2 is None  # no-op, not an IllegalEventError


def test_new_event_with_new_key_but_illegal_transition_is_rejected(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "SUBMIT_INTENT", f"{mint.position_group_id}:SUBMIT_INTENT:COID-1:second",
            {"client_order_id": "COID-1"}, clock=_clock(),
        )
    events = journal.read_events(mint.position_group_id)
    assert not any(e.idempotency_key.endswith(":second") for e in events)


# --------------------------------------------------------------------- #
# Boundary validation
# --------------------------------------------------------------------- #

def test_unknown_event_type_rejected(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    with pytest.raises(IllegalEventError):
        journal.append_event(mint.position_group_id, "NOT_A_REAL_EVENT", "bad-1", {}, clock=_clock())


def test_missing_required_field_rejected(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    with pytest.raises(IllegalEventError):
        journal.append_event(mint.position_group_id, "SUBMIT_INTENT", "bad-2", {}, clock=_clock())


def test_submit_failure_without_resolution_basis_rejected_at_boundary(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "SUBMIT_FAILURE", "bad-3",
            {"client_order_id": "COID-1", "failure_reason": "timeout"}, clock=_clock(),
        )
    state = fold(journal.read_events(mint.position_group_id))
    assert state.legs["COID-1"].submit_status == LEG_SUBMIT_PENDING_UNKNOWN


def test_naive_timestamp_rejected(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "RECONCILIATION_ATTEMPTED", "bad-4",
            {"client_order_id": "X", "outcome": "INCONCLUSIVE"},
            clock=lambda: datetime(2026, 7, 31, 9, 15, 0),  # naive
        )


def test_non_monotonic_fill_rejected_at_boundary(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    _fill(journal, mint.position_group_id, "COID-1", 30, 100.0)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", "bad-5",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 20,
             "delta_quantity": -10, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_double_mint_same_group_rejected(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "MINTED", "double-mint-key",
            {"plan_id": "PLAN-1", "strategy_id": "X", "underlying": "NIFTY"}, clock=_clock(),
        )


# --------------------------------------------------------------------- #
# Concurrency / mint races (regression from prior rounds)
# --------------------------------------------------------------------- #

def test_concurrent_appends_never_collide_sequence(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    errors = []

    def worker(i):
        try:
            journal.append_event(
                mint.position_group_id, "RECONCILIATION_ATTEMPTED", f"{mint.position_group_id}:worker:{i}",
                {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(20)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    seqs = [e.sequence_no for e in journal.read_events(mint.position_group_id)]
    assert len(seqs) == len(set(seqs)) == 21


def test_concurrent_mint_same_plan_id_yields_one_group(tmp_path):
    journal = _journal(tmp_path)
    results = []
    lock = threading.Lock()

    def worker(i):
        r = mint_position_group_id(journal, "PLAN-RACE", "IRON_CONDOR", "NIFTY",
                                    clock=_clock(f"2026-07-31T09:15:00.{i:06d}+00:00"))
        with lock:
            results.append(r)

    threads = [threading.Thread(target=worker, args=(i,)) for i in range(10)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len({r.position_group_id for r in results}) == 1
    assert sum(1 for r in results if r.newly_minted) == 1


# --------------------------------------------------------------------- #
# 2. Recovery: CANCELLED with nonzero fill must not drop the fill
# --------------------------------------------------------------------- #

@dataclass
class _FakeResult:
    client_order_id: str
    status: str
    filled_quantity: int
    average_price: Optional[float]
    found: bool


class _FakeBroker:
    def __init__(self, responses: Dict[str, _FakeResult]):
        self._responses = responses

    def get_order(self, coid):
        return self._responses.get(coid, _FakeResult(coid, "UNKNOWN", 0, None, found=False))


def test_recovery_cancelled_with_partial_fill_applies_fill_before_cancel_ack(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")

    broker = _FakeBroker({"COID-1": _FakeResult("COID-1", "CANCELLED", 20, 105.0, found=True)})
    recover_group(journal, broker, mint.position_group_id, clock=_clock())

    state = fold(journal.read_events(mint.position_group_id))
    assert state.legs["COID-1"].fill.cumulative_filled_quantity == 20
    assert state.legs["COID-1"].submit_status == LEG_CANCELLED
    assert state.lifecycle_state == LIFECYCLE_PARTIALLY_OPEN  # never ABORTED, never zero-fill
    assert net_quantity(state.legs["COID-1"]) == 20


def test_recovery_cancelled_zero_fill_still_aborts(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")

    broker = _FakeBroker({"COID-1": _FakeResult("COID-1", "CANCELLED", 0, None, found=True)})
    recover_group(journal, broker, mint.position_group_id, clock=_clock())
    state = fold(journal.read_events(mint.position_group_id))
    # Zero-fill cancellation is fail-closed, non-terminal: it must not
    # jump straight to ABORTED, since a late fill report is still legally
    # admissible from CANCELLED (see the fill-legality admission table).
    assert state.lifecycle_state == LIFECYCLE_PENDING_FINAL_RECONCILIATION

    # Only an explicit, broker-certified FINAL_RECONCILIATION_CONFIRMED
    # event -- never derived automatically -- may promote it to ABORTED.
    journal.append_event(
        mint.position_group_id, "FINAL_RECONCILIATION_CONFIRMED",
        f"{mint.position_group_id}:final-recon",
        {"broker_confirmation_reference": "REF-1", "confirmed_by": "ops-1",
         "confirmed_at": "2026-07-31T09:20:00+00:00"},
        clock=_clock(),
    )
    state = fold(journal.read_events(mint.position_group_id))
    assert state.lifecycle_state == LIFECYCLE_ABORTED

    # Terminal now: a late fill must be rejected.
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:late-fill",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 5,
             "delta_quantity": 5, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_recovery_never_reached_broker(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    broker = _FakeBroker({})
    recover_group(journal, broker, mint.position_group_id, clock=_clock())
    state = fold(journal.read_events(mint.position_group_id))
    assert state.legs["COID-1"].submit_status == LEG_SUBMIT_FAILED
    assert state.lifecycle_state == LIFECYCLE_PENDING_FINAL_RECONCILIATION


def test_recovery_idempotent_across_repeated_passes(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    broker = _FakeBroker({"COID-1": _FakeResult("COID-1", "PARTIAL", 20, 105.0, found=True)})
    recover_group(journal, broker, mint.position_group_id, clock=_clock())
    n1 = len(journal.read_events(mint.position_group_id))
    recover_group(journal, broker, mint.position_group_id, clock=_clock())
    n2 = len(journal.read_events(mint.position_group_id))
    state = fold(journal.read_events(mint.position_group_id))
    assert state.legs["COID-1"].fill.cumulative_filled_quantity == 20
    assert n1 == n2


# --------------------------------------------------------------------- #
# 3. Close/flip attribution — append_linked_events
# --------------------------------------------------------------------- #

def test_linked_fill_and_target_reduction_commit_atomically(tmp_path):
    journal = _journal(tmp_path)
    target_mint = _mint(journal, plan_id="PLAN-TARGET")
    _construct(journal, target_mint.position_group_id, {"C1": "COID-TARGET"}, {"COID-TARGET": 50})
    _submit_intent(journal, target_mint.position_group_id, "COID-TARGET")
    _submit_ack(journal, target_mint.position_group_id, "COID-TARGET")
    _fill(journal, target_mint.position_group_id, "COID-TARGET", 50, 100.0)
    assert fold(journal.read_events(target_mint.position_group_id)).lifecycle_state == LIFECYCLE_OPEN

    reduce_mint = _mint(journal, plan_id="PLAN-REDUCE")
    _construct(
        journal, reduce_mint.position_group_id, {"C1": "COID-REDUCE"}, {"COID-REDUCE": 20},
        actions={"COID-REDUCE": "REDUCING"},
        target_position_group_ids={"COID-REDUCE": target_mint.position_group_id},
        target_contract_ids={"COID-REDUCE": "C1"},
    )
    _submit_intent(journal, reduce_mint.position_group_id, "COID-REDUCE")
    _submit_ack(journal, reduce_mint.position_group_id, "COID-REDUCE")

    fill_spec = EventSpec(
        reduce_mint.position_group_id, "FILL_OBSERVED",
        f"{reduce_mint.position_group_id}:FILL_OBSERVED:COID-REDUCE:20:100.0",
        {"client_order_id": "COID-REDUCE", "cumulative_filled_quantity_after": 20,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": 20,
         "delta_value": 2000.0, "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
    )
    reduction_spec = EventSpec(
        target_mint.position_group_id, "TARGET_GROUP_REDUCTION_APPLIED",
        f"{target_mint.position_group_id}:TARGET_GROUP_REDUCTION_APPLIED:COID-REDUCE",
        {"source_client_order_id": "COID-REDUCE", "source_position_group_id": reduce_mint.position_group_id,
         "target_contract_id": "C1", "reduced_quantity_delta": 20},
    )
    results = journal.append_linked_events([fill_spec, reduction_spec], clock=_clock())
    assert all(r is not None for r in results)

    target_state = fold(journal.read_events(target_mint.position_group_id))
    target_leg = target_state.legs["COID-TARGET"]
    assert net_quantity(target_leg) == 30
    assert target_state.lifecycle_state == LIFECYCLE_PARTIALLY_OPEN


def test_full_close_via_linked_reduction_reaches_closed(tmp_path):
    journal = _journal(tmp_path)
    target_mint = _mint(journal, plan_id="PLAN-TARGET-2")
    _construct(journal, target_mint.position_group_id, {"C1": "COID-T"}, {"COID-T": 50})
    _submit_intent(journal, target_mint.position_group_id, "COID-T")
    _submit_ack(journal, target_mint.position_group_id, "COID-T")
    _fill(journal, target_mint.position_group_id, "COID-T", 50, 100.0)

    reduce_mint = _mint(journal, plan_id="PLAN-CLOSE-2")
    _construct(journal, reduce_mint.position_group_id, {"C1": "COID-C"}, {"COID-C": 50},
               target_position_group_ids={"COID-C": target_mint.position_group_id},
               target_contract_ids={"COID-C": "C1"})
    _submit_intent(journal, reduce_mint.position_group_id, "COID-C")
    _submit_ack(journal, reduce_mint.position_group_id, "COID-C")

    fill_spec = EventSpec(
        reduce_mint.position_group_id, "FILL_OBSERVED",
        f"{reduce_mint.position_group_id}:FILL_OBSERVED:COID-C:50:100.0",
        {"client_order_id": "COID-C", "cumulative_filled_quantity_after": 50,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": 50,
         "delta_value": 5000.0, "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
    )
    reduction_spec = EventSpec(
        target_mint.position_group_id, "TARGET_GROUP_REDUCTION_APPLIED",
        f"{target_mint.position_group_id}:TARGET_GROUP_REDUCTION_APPLIED:COID-C",
        {"source_client_order_id": "COID-C", "source_position_group_id": reduce_mint.position_group_id,
         "target_contract_id": "C1", "reduced_quantity_delta": 50},
    )
    journal.append_linked_events([fill_spec, reduction_spec], clock=_clock())
    target_state = fold(journal.read_events(target_mint.position_group_id))
    assert target_state.lifecycle_state == LIFECYCLE_CLOSED
    assert net_quantity(target_state.legs["COID-T"]) == 0


def test_rejected_close_leaves_target_group_untouched(tmp_path):
    journal = _journal(tmp_path)
    target_mint = _mint(journal, plan_id="PLAN-TARGET-3")
    _construct(journal, target_mint.position_group_id, {"C1": "COID-T"}, {"COID-T": 50})
    _submit_intent(journal, target_mint.position_group_id, "COID-T")
    _submit_ack(journal, target_mint.position_group_id, "COID-T")
    _fill(journal, target_mint.position_group_id, "COID-T", 50, 100.0)

    reduce_mint = _mint(journal, plan_id="PLAN-REJECT-3")
    _construct(journal, reduce_mint.position_group_id, {"C1": "COID-R"}, {"COID-R": 20},
               target_position_group_ids={"COID-R": target_mint.position_group_id},
               target_contract_ids={"COID-R": "C1"})
    _submit_intent(journal, reduce_mint.position_group_id, "COID-R")
    journal.append_event(
        reduce_mint.position_group_id, "SUBMIT_FAILURE", f"{reduce_mint.position_group_id}:fail",
        {"client_order_id": "COID-R", "failure_reason": "rejected", "resolution_basis": "CONFIRMED_REJECTION"},
        clock=_clock(),
    )
    # No FILL_OBSERVED, no TARGET_GROUP_REDUCTION_APPLIED ever produced --
    # target group's quantity is provably unchanged.
    target_state = fold(journal.read_events(target_mint.position_group_id))
    assert net_quantity(target_state.legs["COID-T"]) == 50
    assert target_state.lifecycle_state == LIFECYCLE_OPEN


def test_flip_old_group_closes_new_group_opens_independently(tmp_path):
    journal = _journal(tmp_path)
    old_mint = _mint(journal, plan_id="PLAN-OLD-FLIP")
    _construct(journal, old_mint.position_group_id, {"C1": "COID-OLD"}, {"COID-OLD": 50})
    _submit_intent(journal, old_mint.position_group_id, "COID-OLD")
    _submit_ack(journal, old_mint.position_group_id, "COID-OLD")
    _fill(journal, old_mint.position_group_id, "COID-OLD", 50, 100.0)

    new_mint = _mint(journal, plan_id="PLAN-NEW-FLIP")
    _construct(
        journal, new_mint.position_group_id,
        {"C1": "COID-CLOSE-OLD", "C2": "COID-NEW-OPEN"},
        {"COID-CLOSE-OLD": 50, "COID-NEW-OPEN": 50},
        actions={"COID-CLOSE-OLD": "CLOSING", "COID-NEW-OPEN": "OPENING"},
        target_position_group_ids={"COID-CLOSE-OLD": old_mint.position_group_id},
        target_contract_ids={"COID-CLOSE-OLD": "C1"},
        flip_link_ids={"COID-CLOSE-OLD": "FLIP-9", "COID-NEW-OPEN": "FLIP-9"},
    )
    _submit_intent(journal, new_mint.position_group_id, "COID-CLOSE-OLD")
    _submit_ack(journal, new_mint.position_group_id, "COID-CLOSE-OLD")
    _submit_intent(journal, new_mint.position_group_id, "COID-NEW-OPEN")
    _submit_ack(journal, new_mint.position_group_id, "COID-NEW-OPEN")

    close_fill = EventSpec(
        new_mint.position_group_id, "FILL_OBSERVED",
        f"{new_mint.position_group_id}:FILL_OBSERVED:COID-CLOSE-OLD:50:100.0",
        {"client_order_id": "COID-CLOSE-OLD", "cumulative_filled_quantity_after": 50,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": 50,
         "delta_value": 5000.0, "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
    )
    reduction = EventSpec(
        old_mint.position_group_id, "TARGET_GROUP_REDUCTION_APPLIED",
        f"{old_mint.position_group_id}:TARGET_GROUP_REDUCTION_APPLIED:COID-CLOSE-OLD",
        {"source_client_order_id": "COID-CLOSE-OLD", "source_position_group_id": new_mint.position_group_id,
         "target_contract_id": "C1", "reduced_quantity_delta": 50},
    )
    journal.append_linked_events([close_fill, reduction], clock=_clock())
    _fill(journal, new_mint.position_group_id, "COID-NEW-OPEN", 50, 110.0, key_suffix=":new")

    old_state = fold(journal.read_events(old_mint.position_group_id))
    new_state = fold(journal.read_events(new_mint.position_group_id))
    assert old_state.lifecycle_state == LIFECYCLE_CLOSED
    assert net_quantity(new_state.legs["COID-NEW-OPEN"]) == 50


def test_linkage_rejects_reduction_without_matching_fill_in_batch(tmp_path):
    journal = _journal(tmp_path)
    target_mint = _mint(journal, plan_id="PLAN-T4")
    _construct(journal, target_mint.position_group_id, {"C1": "COID-T4"}, {"COID-T4": 50})
    _submit_intent(journal, target_mint.position_group_id, "COID-T4")
    _submit_ack(journal, target_mint.position_group_id, "COID-T4")
    _fill(journal, target_mint.position_group_id, "COID-T4", 50, 100.0)

    orphan_reduction = EventSpec(
        target_mint.position_group_id, "TARGET_GROUP_REDUCTION_APPLIED",
        f"{target_mint.position_group_id}:orphan",
        {"source_client_order_id": "GHOST", "source_position_group_id": "PG-GHOST",
         "target_contract_id": "C1", "reduced_quantity_delta": 10},
    )
    with pytest.raises(LinkageValidationError):
        journal.append_linked_events([orphan_reduction], clock=_clock())
    target_state = fold(journal.read_events(target_mint.position_group_id))
    assert net_quantity(target_state.legs["COID-T4"]) == 50  # nothing inserted


def test_linkage_rejects_reduction_exceeding_source_fill_delta(tmp_path):
    journal = _journal(tmp_path)
    target_mint = _mint(journal, plan_id="PLAN-T5")
    _construct(journal, target_mint.position_group_id, {"C1": "COID-T5"}, {"COID-T5": 50})
    _submit_intent(journal, target_mint.position_group_id, "COID-T5")
    _submit_ack(journal, target_mint.position_group_id, "COID-T5")
    _fill(journal, target_mint.position_group_id, "COID-T5", 50, 100.0)

    reduce_mint = _mint(journal, plan_id="PLAN-R5")
    _construct(journal, reduce_mint.position_group_id, {"C1": "COID-R5"}, {"COID-R5": 10},
               target_position_group_ids={"COID-R5": target_mint.position_group_id},
               target_contract_ids={"COID-R5": "C1"})
    _submit_intent(journal, reduce_mint.position_group_id, "COID-R5")
    _submit_ack(journal, reduce_mint.position_group_id, "COID-R5")

    fill_spec = EventSpec(
        reduce_mint.position_group_id, "FILL_OBSERVED",
        f"{reduce_mint.position_group_id}:FILL_OBSERVED:COID-R5:10:100.0",
        {"client_order_id": "COID-R5", "cumulative_filled_quantity_after": 10,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": 10,
         "delta_value": 1000.0, "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
    )
    over_reduction = EventSpec(
        target_mint.position_group_id, "TARGET_GROUP_REDUCTION_APPLIED",
        f"{target_mint.position_group_id}:over",
        {"source_client_order_id": "COID-R5", "source_position_group_id": reduce_mint.position_group_id,
         "target_contract_id": "C1", "reduced_quantity_delta": 30},  # > delta_quantity of 10
    )
    with pytest.raises(LinkageValidationError):
        journal.append_linked_events([fill_spec, over_reduction], clock=_clock())
    target_state = fold(journal.read_events(target_mint.position_group_id))
    assert net_quantity(target_state.legs["COID-T5"]) == 50


def test_linked_batch_idempotent_replay_full_noop(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-IDEM")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    spec = EventSpec(mint.position_group_id, "RECONCILIATION_ATTEMPTED", "idem-linked-key",
                      {"client_order_id": "COID-1", "outcome": "INCONCLUSIVE"})
    r1 = journal.append_linked_events([spec], clock=_clock())
    r2 = journal.append_linked_events([spec], clock=_clock())
    assert r1[0] is not None
    assert r2 == [None]


def test_linked_batch_partial_presence_raises_integrity_error(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-PARTIAL")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    spec_a = EventSpec(mint.position_group_id, "RECONCILIATION_ATTEMPTED", "partial-a",
                        {"client_order_id": "COID-1", "outcome": "INCONCLUSIVE"})
    spec_b = EventSpec(mint.position_group_id, "RECONCILIATION_ATTEMPTED", "partial-b",
                        {"client_order_id": "COID-1", "outcome": "INCONCLUSIVE"})
    journal.append_linked_events([spec_a], clock=_clock())  # only spec_a recorded
    with pytest.raises(PartialLinkedBatchIntegrityError):
        journal.append_linked_events([spec_a, spec_b], clock=_clock())


# --------------------------------------------------------------------- #
# 4. Process-lock enforcement
# --------------------------------------------------------------------- #

def test_journal_construction_requires_held_lock_when_supplied(tmp_path):
    lock = ProcessLock(tmp_path / "pg.lock")
    with pytest.raises(ProcessLockNotHeldError):
        PositionGroupJournal(tmp_path / "pg.db", process_lock=lock)  # not acquired yet


def test_journal_construction_succeeds_with_acquired_lock(tmp_path):
    lock = ProcessLock(tmp_path / "pg2.lock")
    lock.acquire()
    try:
        journal = PositionGroupJournal(tmp_path / "pg2.db", process_lock=lock)
        assert journal is not None
    finally:
        lock.release()


def test_second_process_lock_on_same_path_fails_fast():
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        lock_path = Path(d) / "shared.lock"
        lock1 = ProcessLock(lock_path)
        lock1.acquire()
        try:
            lock2 = ProcessLock(lock_path)
            if lock1.enforced:
                from bujji.core.process_lock import LockAcquisitionError
                with pytest.raises(LockAcquisitionError):
                    lock2.acquire()
        finally:
            lock1.release()


def test_journal_construction_without_lock_still_allowed_for_readonly_use(tmp_path):
    journal = PositionGroupJournal(tmp_path / "pg3.db", process_lock=None)
    assert journal.read_all_group_ids() == []


# --------------------------------------------------------------------- #
# Lifecycle regression (unchanged shape from prior rounds)
# --------------------------------------------------------------------- #

def test_minted_only_before_construction(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    state = fold(journal.read_events(mint.position_group_id))
    assert state.lifecycle_state == LIFECYCLE_MINTED


def test_full_fill_reaches_open(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    _fill(journal, mint.position_group_id, "COID-1", 50, 100.0)
    state = fold(journal.read_events(mint.position_group_id))
    assert state.lifecycle_state == LIFECYCLE_OPEN


def test_pending_intent_blocks_authorization_via_unresolved(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal)
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    state = fold(journal.read_events(mint.position_group_id))
    assert state.lifecycle_state == LIFECYCLE_UNRESOLVED


# --------------------------------------------------------------------- #
# Round 3/4: terminality, fill-legality admission table,
# OPERATOR_CORRECTION_RECORDED, FILL_OBSERVED invariants, idempotency
# collision detection.
# --------------------------------------------------------------------- #

def _open_and_close(journal, plan_id="PLAN-TERM"):
    mint = _mint(journal, plan_id=plan_id)
    _construct(journal, mint.position_group_id, {"C1": "COID-T"}, {"COID-T": 50})
    _submit_intent(journal, mint.position_group_id, "COID-T")
    _submit_ack(journal, mint.position_group_id, "COID-T")
    _fill(journal, mint.position_group_id, "COID-T", 50, 100.0)
    reduce_mint = _mint(journal, plan_id=plan_id + "-CLOSE")
    _construct(journal, reduce_mint.position_group_id, {"C1": "COID-CLOSE"}, {"COID-CLOSE": 50},
               target_position_group_ids={"COID-CLOSE": mint.position_group_id},
               target_contract_ids={"COID-CLOSE": "C1"})
    _submit_intent(journal, reduce_mint.position_group_id, "COID-CLOSE")
    _submit_ack(journal, reduce_mint.position_group_id, "COID-CLOSE")
    fill_spec = EventSpec(
        reduce_mint.position_group_id, "FILL_OBSERVED",
        f"{reduce_mint.position_group_id}:FILL_OBSERVED:COID-CLOSE:50:100.0",
        {"client_order_id": "COID-CLOSE", "cumulative_filled_quantity_after": 50,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": 50,
         "delta_value": 5000.0, "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
    )
    reduction_spec = EventSpec(
        mint.position_group_id, "TARGET_GROUP_REDUCTION_APPLIED",
        f"{mint.position_group_id}:TARGET_GROUP_REDUCTION_APPLIED:COID-CLOSE",
        {"source_client_order_id": "COID-CLOSE", "source_position_group_id": reduce_mint.position_group_id,
         "target_contract_id": "C1", "reduced_quantity_delta": 50},
    )
    journal.append_linked_events([fill_spec, reduction_spec], clock=_clock())
    return mint.position_group_id


def test_terminality_closed_rejects_fill_observed(tmp_path):
    journal = _journal(tmp_path)
    pg = _open_and_close(journal)
    state = fold(journal.read_events(pg))
    assert state.lifecycle_state == LIFECYCLE_CLOSED
    with pytest.raises(IllegalEventError):
        journal.append_event(
            pg, "FILL_OBSERVED", f"{pg}:post-close-fill",
            {"client_order_id": "COID-T", "cumulative_filled_quantity_after": 60,
             "delta_quantity": 10, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_terminality_closed_rejects_reconciliation_attempted(tmp_path):
    journal = _journal(tmp_path)
    pg = _open_and_close(journal)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            pg, "RECONCILIATION_ATTEMPTED", f"{pg}:post-close-recon",
            {"client_order_id": "COID-T", "outcome": "INCONCLUSIVE"}, clock=_clock(),
        )


def test_terminality_allows_operator_correction_without_mutating_state(tmp_path):
    journal = _journal(tmp_path)
    pg = _open_and_close(journal)
    before = fold(journal.read_events(pg))
    journal.append_event(
        pg, "OPERATOR_CORRECTION_RECORDED", f"{pg}:correction-1",
        {"correcting_event_type": "FILL_OBSERVED",
         "correcting_payload": {"note": "late broker report"},
         "operator_id": "ops-1", "justification": "broker statement discrepancy",
         "evidence_reference": "TICKET-123",
         "reviewed_at": "2026-07-31T10:00:00+00:00"},
        clock=_clock(),
    )
    after = fold(journal.read_events(pg))
    assert after.lifecycle_state == before.lifecycle_state == LIFECYCLE_CLOSED
    assert net_quantity(after.legs["COID-T"]) == net_quantity(before.legs["COID-T"])
    assert len(after.operator_corrections) == 1


def test_operator_correction_rejects_disallowed_correcting_event_type(tmp_path):
    journal = _journal(tmp_path)
    pg = _open_and_close(journal)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            pg, "OPERATOR_CORRECTION_RECORDED", f"{pg}:bad-correction",
            {"correcting_event_type": "MINTED", "correcting_payload": {},
             "operator_id": "ops-1", "justification": "x", "evidence_reference": "TICKET-1",
             "reviewed_at": "2026-07-31T10:00:00+00:00"}, clock=_clock(),
        )


def test_operator_correction_rejects_naive_reviewed_at(tmp_path):
    journal = _journal(tmp_path)
    pg = _open_and_close(journal)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            pg, "OPERATOR_CORRECTION_RECORDED", f"{pg}:bad-tz",
            {"correcting_event_type": "FILL_OBSERVED", "correcting_payload": {},
             "operator_id": "ops-1", "justification": "x", "evidence_reference": "TICKET-1",
             "reviewed_at": "2026-07-31T10:00:00"}, clock=_clock(),  # no offset
        )


def test_final_reconciliation_confirmed_illegal_outside_pending_state(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-BAD-RECON")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FINAL_RECONCILIATION_CONFIRMED", f"{mint.position_group_id}:bad-recon",
            {"broker_confirmation_reference": "REF", "confirmed_by": "ops",
             "confirmed_at": "2026-07-31T10:00:00+00:00"}, clock=_clock(),
        )


def test_raw_closed_and_aborted_event_types_rejected(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-RAW")
    with pytest.raises(IllegalEventError):
        journal.append_event(mint.position_group_id, "CLOSED", "raw-closed", {}, clock=_clock())
    with pytest.raises(IllegalEventError):
        journal.append_event(mint.position_group_id, "ABORTED", "raw-aborted", {}, clock=_clock())


# --- Fill-legality admission table ---

def test_fill_rejected_from_not_submitted(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-FILL-NS")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:fill-ns",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
             "delta_quantity": 10, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_rejected_from_submit_pending_unknown_directly(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-FILL-PU")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:fill-pu",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
             "delta_quantity": 10, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_rejected_from_submit_failed(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-FILL-SF")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    journal.append_event(
        mint.position_group_id, "SUBMIT_FAILURE", f"{mint.position_group_id}:fail",
        {"client_order_id": "COID-1", "failure_reason": "rejected", "resolution_basis": "CONFIRMED_REJECTION"},
        clock=_clock(),
    )
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:fill-sf",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
             "delta_quantity": 10, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_admitted_from_acked_cancel_pending_and_cancelled(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-FILL-OK")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    ev, _ = _fill(journal, mint.position_group_id, "COID-1", 10, 100.0)
    assert ev is not None  # admitted from ACKED

    journal.append_event(
        mint.position_group_id, "CANCEL_INTENT", f"{mint.position_group_id}:ci",
        {"client_order_id": "COID-1"}, clock=_clock(),
    )
    ev2, _ = _fill(journal, mint.position_group_id, "COID-1", 20, 100.0, key_suffix=":cp")
    assert ev2 is not None  # admitted from CANCEL_PENDING_UNKNOWN

    journal.append_event(
        mint.position_group_id, "CANCEL_ACK", f"{mint.position_group_id}:ca",
        {"client_order_id": "COID-1", "cancelled_quantity": 20}, clock=_clock(),
    )
    ev3, _ = _fill(journal, mint.position_group_id, "COID-1", 30, 100.0, key_suffix=":cancelled")
    assert ev3 is not None  # admitted from CANCELLED -- late-fill reconciliation


# --- FILL_OBSERVED payload invariants ---

def test_fill_rejects_negative_cumulative_quantity(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-NEG")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:neg",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": -5,
             "delta_quantity": -5, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_rejects_non_int_cumulative_quantity(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-FLOAT")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:float",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 20.5,
             "delta_quantity": 20.5, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_rejects_negative_price(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-NEGPRICE")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:negprice",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
             "cumulative_average_fill_price_after": -1.0,
             "delta_quantity": 10, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_rejects_delta_disagreeing_with_recomputed_value(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-BADDELTA")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:baddelta",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
             "delta_quantity": 999, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_rejects_zero_delta_with_new_key(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-ZERO")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    _fill(journal, mint.position_group_id, "COID-1", 10, 100.0)
    with pytest.raises(IllegalEventError):
        journal.append_event(
            mint.position_group_id, "FILL_OBSERVED", f"{mint.position_group_id}:zero-delta-new-key",
            {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
             "delta_quantity": 0, "delta_cost_basis_status": "DERIVED"}, clock=_clock(),
        )


def test_fill_zero_delta_via_original_key_is_a_pure_noop(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-ZERO2")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    _submit_intent(journal, mint.position_group_id, "COID-1")
    _submit_ack(journal, mint.position_group_id, "COID-1")
    key = f"{mint.position_group_id}:FILL_OBSERVED:COID-1:10:100.0"
    payload = {"client_order_id": "COID-1", "cumulative_filled_quantity_after": 10,
               "cumulative_average_fill_price_after": 100.0, "delta_quantity": 10,
               "delta_value": 1000.0, "delta_cost_basis_status": "DERIVED", "fill_price": 100.0}
    ev1 = journal.append_event(mint.position_group_id, "FILL_OBSERVED", key, payload, clock=_clock())
    # Exact replay of the SAME verbatim payload under the ORIGINAL key --
    # never reaches the zero-delta validation rule at all, short-circuited
    # by idempotency first.
    ev2 = journal.append_event(mint.position_group_id, "FILL_OBSERVED", key, payload, clock=_clock())
    assert ev1 is not None
    assert ev2 is None


# --- Idempotency collision (genuine key reuse vs. exact replay) ---

def test_idempotency_collision_different_group(tmp_path):
    journal = _journal(tmp_path)
    mint1 = _mint(journal, plan_id="PLAN-COLL-1")
    mint2 = _mint(journal, plan_id="PLAN-COLL-2")
    journal.append_event(
        mint1.position_group_id, "RECONCILIATION_ATTEMPTED", "shared-key",
        {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
    )
    with pytest.raises(IdempotencyKeyCollisionError):
        journal.append_event(
            mint2.position_group_id, "RECONCILIATION_ATTEMPTED", "shared-key",
            {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
        )


def test_idempotency_collision_different_payload_value(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-COLL-3")
    journal.append_event(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "shared-key-2",
        {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
    )
    with pytest.raises(IdempotencyKeyCollisionError):
        journal.append_event(
            mint.position_group_id, "RECONCILIATION_ATTEMPTED", "shared-key-2",
            {"client_order_id": "Y", "outcome": "INCONCLUSIVE"}, clock=_clock(),
        )


def test_idempotency_exact_replay_with_reordered_payload_keys_is_noop(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-COLL-4")
    ev1 = journal.append_event(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "shared-key-3",
        {"client_order_id": "X", "outcome": "INCONCLUSIVE"}, clock=_clock(),
    )
    ev2 = journal.append_event(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "shared-key-3",
        {"outcome": "INCONCLUSIVE", "client_order_id": "X"}, clock=_clock(),  # same content, reordered
    )
    assert ev1 is not None
    assert ev2 is None


def test_linked_batch_idempotency_collision_rejects_whole_batch(tmp_path):
    journal = _journal(tmp_path)
    mint = _mint(journal, plan_id="PLAN-COLL-LINKED")
    _construct(journal, mint.position_group_id, {"C1": "COID-1"}, {"COID-1": 50})
    journal.append_event(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "linked-shared-key",
        {"client_order_id": "COID-1", "outcome": "INCONCLUSIVE"}, clock=_clock(),
    )
    colliding_spec = EventSpec(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "linked-shared-key",
        {"client_order_id": "COID-1", "outcome": "DIFFERENT"},  # disagrees with what's recorded
    )
    other_spec = EventSpec(
        mint.position_group_id, "RECONCILIATION_ATTEMPTED", "linked-other-key",
        {"client_order_id": "COID-1", "outcome": "INCONCLUSIVE"},
    )
    with pytest.raises(IdempotencyKeyCollisionError):
        journal.append_linked_events([other_spec, colliding_spec], clock=_clock())
    # Neither event from the rejected batch was inserted.
    events = journal.read_events(mint.position_group_id)
    assert not any(e.idempotency_key == "linked-other-key" for e in events)
