"""Orders are journaled before they exist, and a partial entry is contained.

WHAT THE AUDIT FOUND (Layer 11, 2026-08-21). A complete order state machine
existed -- MINTED/CONSTRUCTED/SUBMIT_INTENT/SUBMIT_ACK/FILL_OBSERVED,
idempotency-keyed, transactional, with per-leg crash recovery against broker
truth. None of it ran. process_entry_cycle called place_order in a bare loop
and trusted is_filled; both journal databases held ZERO rows; recover_group
had zero callers; the composition root carried `journal` and never used it.

The consequence was the canonical uncontrolled-loss mechanism for an options
seller: a multi-leg entry where leg 1 fills and leg 2 does not leaves a naked
short -- and it was INVISIBLE, since the runner logged "did not fill" and
dropped the filled leg from its own model while it remained real at broker.
"""
from __future__ import annotations

import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.enums import OptionType, OrderStatus, Side
from bujji.core.models import OptionContract, OrderRequest, OrderResult
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.execution_journal_bridge import (
    contain_partial_entry, journaled_entry, recover_unresolved_at_startup,
    unresolved_group_ids)
from bujji.trading_brain.risk_governor.position_group_fold import (
    LEG_SUBMIT_PENDING_UNKNOWN, fold)

LOG = logging.getLogger("exec-test")
CLK = lambda: datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)


def _req(coid, side=Side.SELL, qty=75):
    return OrderRequest(
        contract=OptionContract(symbol=f"NSE:{coid}", underlying="NIFTY", strike=24400.0,
                                option_type=OptionType.CE, expiry="2026-08-25", lot_size=75),
        side=side, quantity=qty, client_order_id=coid,
        limit_price=None, reference_price=120.0, tag="t")


def _filled(coid, qty=75, px=120.0):
    return OrderResult(coid, OrderStatus.FILLED, broker_order_id=f"B-{coid}",
                       filled_quantity=qty, average_price=px)


def _rejected(coid):
    return OrderResult(coid, OrderStatus.REJECTED, message="margin refused")


def _journal(tmp_path, name="j.db"):
    return PositionGroupJournal(str(tmp_path / name)), str(tmp_path / name)


def _entry(journal, place_fn, reqs, plan="MTA-1"):
    return journaled_entry(journal, place_fn, reqs, plan_id=plan,
                           strategy_id="SHORT_STRANGLE", underlying="NIFTY",
                           clock=CLK, logger=LOG)


class TestOrdersAreJournaled:
    def test_a_clean_entry_writes_the_full_event_chain(self, tmp_path):
        j, _ = _journal(tmp_path)
        out = _entry(j, lambda r: _filled(r.client_order_id), [_req("CE-1"), _req("PE-1")])
        types = [e.event_type for e in j.read_events(out.position_group_id)]
        assert types == ["MINTED", "CONSTRUCTED",
                         "SUBMIT_INTENT", "SUBMIT_ACK", "FILL_OBSERVED",
                         "SUBMIT_INTENT", "SUBMIT_ACK", "FILL_OBSERVED"]
        assert fold(j.read_events(out.position_group_id)).lifecycle_state == "OPEN"

    def test_intent_is_journaled_before_the_order_is_placed(self, tmp_path):
        """The ordering that makes a crash recoverable. If placement happened
        first, a crash between the two would leave a real order that nothing
        on disk knows about -- unrecoverable by definition.

        Read straight from the database file at the moment place_fn is
        called, so this observes what is DURABLE, not what is in flight.
        """
        j, path = _journal(tmp_path)
        durable_at_placement = []

        def place(r):
            import sqlite3
            conn = sqlite3.connect(path)
            try:
                durable_at_placement.extend(
                    row[0] for row in conn.execute(
                        "SELECT event_type FROM position_group_events"))
            finally:
                conn.close()
            return _filled(r.client_order_id)

        _entry(j, place, [_req("CE-1")])
        assert "SUBMIT_INTENT" in durable_at_placement, \
            "the order was placed before its SUBMIT_INTENT was durable"

    def test_a_rejection_is_journaled_as_submit_failure(self, tmp_path):
        j, _ = _journal(tmp_path)
        out = _entry(j, lambda r: _rejected(r.client_order_id), [_req("CE-1")])
        types = [e.event_type for e in j.read_events(out.position_group_id)]
        assert "SUBMIT_FAILURE" in types and "FILL_OBSERVED" not in types
        assert out.all_filled is False

    def test_an_unjournalable_entry_places_nothing(self, tmp_path):
        """Fail closed BEFORE money moves."""
        j, _ = _journal(tmp_path)
        placed = []

        def boom(*a, **kw):
            raise RuntimeError("journal down")

        j.append_event = boom
        out = journaled_entry(j, lambda r: placed.append(r) or _filled(r.client_order_id),
                              [_req("CE-1")], plan_id="MTA-X", strategy_id="S",
                              underlying="NIFTY", clock=CLK, logger=LOG)
        assert placed == [], "an order was placed that the journal did not know about"
        assert out.blocked_reason and out.blocked_reason.startswith("JOURNAL_UNAVAILABLE")


class TestPartialEntryIsContained:
    def _partial(self, r):
        return _rejected(r.client_order_id) if r.client_order_id == "PE-1" \
            else _filled(r.client_order_id)

    def test_the_filled_leg_is_unwound(self, tmp_path):
        """THE test. A one-legged short is the uncontrolled-loss mechanism."""
        j, _ = _journal(tmp_path)
        out = _entry(j, self._partial, [_req("CE-1"), _req("PE-1")])
        assert out.all_filled is False and len(out.filled) == 1
        cont = contain_partial_entry(
            j, lambda r: _filled(r.client_order_id, r.quantity, 121.5), out,
            underlying="NIFTY", strategy_id="SHORT_STRANGLE", clock=CLK, logger=LOG)
        assert cont.clean is True
        assert cont.unwound_coids == ("CE-1",)

    def test_the_source_group_reaches_closed(self, tmp_path):
        """The journal's own fold must agree the position is flat -- not
        merely our belief that we sent an unwind."""
        j, _ = _journal(tmp_path)
        out = _entry(j, self._partial, [_req("CE-1"), _req("PE-1")])
        contain_partial_entry(j, lambda r: _filled(r.client_order_id, r.quantity, 121.5),
                              out, underlying="NIFTY", strategy_id="S", clock=CLK, logger=LOG)
        assert fold(j.read_events(out.position_group_id)).lifecycle_state == "CLOSED"

    def test_the_unwind_reverses_the_side(self, tmp_path):
        j, _ = _journal(tmp_path)
        out = _entry(j, self._partial, [_req("CE-1", side=Side.SELL), _req("PE-1")])
        sides = []
        contain_partial_entry(
            j, lambda r: sides.append(r.side) or _filled(r.client_order_id, r.quantity, 1.0),
            out, underlying="NIFTY", strategy_id="S", clock=CLK, logger=LOG)
        assert sides == [Side.BUY]

    def test_a_failed_unwind_is_reported_as_an_orphan_never_dropped(self, tmp_path):
        """An orphan is a LIVE position. Silence here is the worst outcome."""
        j, _ = _journal(tmp_path)
        out = _entry(j, self._partial, [_req("CE-1"), _req("PE-1")])
        cont = contain_partial_entry(j, lambda r: _rejected(r.client_order_id), out,
                                     underlying="NIFTY", strategy_id="S",
                                     clock=CLK, logger=LOG)
        assert cont.clean is False
        assert cont.orphaned_coids == ("CE-1",)

    def test_nothing_filled_means_nothing_to_contain(self, tmp_path):
        j, _ = _journal(tmp_path)
        out = _entry(j, lambda r: _rejected(r.client_order_id), [_req("CE-1")])
        cont = contain_partial_entry(j, lambda r: _filled(r.client_order_id), out,
                                     underlying="NIFTY", strategy_id="S",
                                     clock=CLK, logger=LOG)
        assert cont.attempted is False


class TestCrashRecovery:
    class _Crash(Exception):
        pass

    def _crash_after_intent(self, tmp_path, plan="MTA-C"):
        j, path = _journal(tmp_path)

        def crashing(r):
            raise TestCrashRecovery._Crash("died mid-placement")

        with pytest.raises(TestCrashRecovery._Crash):
            _entry(j, crashing, [_req("CE-1")], plan=plan)
        return j, path

    def test_a_crash_leaves_a_discoverable_pending_leg(self, tmp_path):
        j, path = self._crash_after_intent(tmp_path)
        pending = unresolved_group_ids(j, path)
        assert len(pending) == 1
        leg = list(fold(j.read_events(pending[0])).legs.values())[0]
        assert leg.submit_status == LEG_SUBMIT_PENDING_UNKNOWN

    def test_recovery_resolves_a_leg_that_actually_filled(self, tmp_path):
        j, path = self._crash_after_intent(tmp_path)

        class _Broker:
            async def get_order(self, coid):
                return _filled(coid)

        pg = _all_groups(path)[0]
        summary = recover_unresolved_at_startup(j, path, _Broker(), asyncio.run, LOG, CLK)
        assert summary["legs_resolved"] == 1
        assert summary["unresolved_after"] == []
        # The fill the broker knew about is now durably in Bujji's own record.
        recovered = list(fold(j.read_events(pg)).legs.values())[0]
        assert recovered.fill.cumulative_filled_quantity == 75

    def test_recovery_resolves_an_order_the_broker_never_received(self, tmp_path):
        j, path = self._crash_after_intent(tmp_path)

        class _NotFound:
            async def get_order(self, coid):
                return OrderResult(coid, OrderStatus.UNKNOWN, message="not_found")

        summary = recover_unresolved_at_startup(j, path, _NotFound(), asyncio.run, LOG, CLK)
        assert summary["legs_resolved"] == 1
        assert summary["unresolved_after"] == []

    def test_a_clean_journal_needs_no_recovery(self, tmp_path):
        j, path = _journal(tmp_path)
        _entry(j, lambda r: _filled(r.client_order_id), [_req("CE-1")])
        assert unresolved_group_ids(j, path) == []


def _all_groups(path):
    import sqlite3
    conn = sqlite3.connect(path)
    try:
        return [r[0] for r in conn.execute(
            "SELECT DISTINCT position_group_id FROM position_group_events")]
    finally:
        conn.close()


class TestRunnerWiring:
    def test_the_runtime_uses_the_journaled_path(self):
        src = (REPO_ROOT / "bujji" / "production_runtime" / "trading_brain_runtime.py").read_text()
        assert "journaled_entry(" in src
        assert "contain_partial_entry(" in src

    def test_the_runner_recovers_at_startup_and_fails_closed(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert "recover_unresolved_at_startup(" in src
        i = src.index("recover_unresolved_at_startup(")
        assert "ConfigurationError" in src[i:i + 1200], \
            "unresolved orders must REFUSE the session, not merely warn"

    def test_the_runner_registers_orphans_for_management(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert "_register_orphaned_legs" in src
        assert "PARTIAL_ORPHANED:" in src
