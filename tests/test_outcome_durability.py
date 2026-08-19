"""D-8: a trade must cost what it cost, and must be remembered.

Two failures, both silent, both at the very end of a real trading day:

  1. PaperBroker computed full charges and slippage on every fill and filed
     them in its execution reports; `close_position()` has accepted `fees=`
     and `slippage=` all along; nothing connected them. Every outcome
     recorded a GROSS result as if it were net.

  2. `attribute_and_remember()` built a real OutcomeMemoryRecord and handed
     it into an in-memory dict. The process exited and it was gone -- so
     Bujji forgot every trade it ever made.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.production_runtime.execution_costs import collect_execution_costs
from bujji.production_runtime.outcome_memory_writer import (
    build_outcome_memory_event,
    persist_outcome_memory,
)


@dataclass(frozen=True)
class _Charges:
    total: float


@dataclass(frozen=True)
class _Report:
    charges: Optional[_Charges] = _Charges(41.75)
    slippage: float = 0.45


class _Broker:
    def __init__(self, reports):
        self._reports = reports

    def get_execution_report(self, order_id):
        return self._reports.get(order_id)


class TestCostIsMeasuredOrAbsentNeverZero:
    def test_charges_and_slippage_sum_across_entry_and_exit(self):
        """A round trip's cost is both sides. Charging one understates every
        trade Bujji will ever learn from."""
        broker = _Broker({"E1": _Report(), "E2": _Report(), "X1": _Report(), "X2": _Report()})
        costs = collect_execution_costs(broker, ["E1", "E2", "X1", "X2"])
        assert costs.fees == pytest.approx(41.75 * 4)
        assert costs.slippage == pytest.approx(0.45 * 4)
        assert costs.is_complete is True

    def test_no_reports_at_all_yields_None_not_zero(self):
        """Zero fees claims the trade was free, and is indistinguishable
        from one that genuinely was. None is the truth: we did not measure."""
        costs = collect_execution_costs(_Broker({}), ["E1", "E2"])
        assert costs.fees is None and costs.slippage is None
        assert costs.orders_missing_report == 2 and costs.is_complete is False

    def test_partial_coverage_is_recorded_and_flagged_incomplete(self):
        broker = _Broker({"E1": _Report()})
        costs = collect_execution_costs(broker, ["E1", "E2"])
        assert costs.fees == pytest.approx(41.75)
        assert costs.orders_missing_report == 1
        assert costs.is_complete is False, "partial cost must never present as the full cost"

    def test_a_report_without_charges_is_counted_not_treated_as_free(self):
        broker = _Broker({"E1": _Report(charges=None)})
        costs = collect_execution_costs(broker, ["E1"])
        assert costs.fees is None and costs.orders_without_charges == 1
        assert costs.is_complete is False

    def test_slippage_is_summed_as_magnitude(self):
        """Adverse slippage is a cost whichever sign the broker reports it
        with; signed cancellation would understate the total."""
        broker = _Broker({"A": _Report(slippage=0.5), "B": _Report(slippage=-0.5)})
        assert collect_execution_costs(broker, ["A", "B"]).slippage == pytest.approx(1.0)

    def test_duplicate_ids_are_not_double_charged(self):
        broker = _Broker({"E1": _Report()})
        assert collect_execution_costs(broker, ["E1", "E1"]).fees == pytest.approx(41.75)

    def test_an_unreadable_report_is_missing_not_zero(self):
        class _Exploding:
            def get_execution_report(self, order_id):
                raise RuntimeError("broker gone")

        costs = collect_execution_costs(_Exploding(), ["E1"])
        assert costs.fees is None and costs.orders_missing_report == 1

    def test_a_broker_without_reports_is_handled(self):
        class _Bare:
            pass

        costs = collect_execution_costs(_Bare(), ["E1"])
        assert costs.fees is None and costs.is_complete is False


def _Record(memory_id: str = "MEM-1", realized_pnl: float = 1234.5):
    """A REAL OutcomeMemoryRecord, not a stand-in.

    The first version of these tests used a thin fake, and the reducer
    rejected it as malformed -- correctly. Using the genuine record makes
    this a proof that what the live path actually produces round-trips
    through the durable store, rather than a proof about a test double.
    """
    from bujji.outcome_memory.models import OutcomeMemoryRecord

    return OutcomeMemoryRecord(
        memory_id=memory_id, session_id="SESSION-A", position_id="POS-1",
        candidate_id="CAND-1", strategy_family="SHORT_STRANGLE",
        entry_timestamp="2026-08-20T09:30:00+05:30",
        exit_timestamp="2026-08-20T15:15:00+05:30",
        recorded_at="2026-08-20T15:15:00+05:30",
        entry_regime="RANGE_BOUND", entry_direction="NEUTRAL", underlying_symbol="NIFTY",
        outcome_direction="OUTCOME_PROFIT", realized_pnl=realized_pnl, pnl_status="KNOWN",
        final_thesis_status="INTACT", primary_cause="THETA_DECAY",
        management_status="NOT_APPLICABLE", greeks_status="NOT_AVAILABLE",
        premium_behaviour_status="NOT_AVAILABLE", portfolio_context_status="NOT_AVAILABLE",
        lifecycle_snapshot={"status": "CLOSED"}, attribution_snapshot={"cause": "THETA_DECAY"},
        portfolio_context_snapshot=None,
    )


class TestTheMemoryOutlivesTheProcess:
    def test_the_event_is_keyed_by_memory_id_for_idempotency(self):
        """A retry, or a re-run over the same position, must be skipped on
        replay rather than double-counted into the campaign statistics."""
        event = build_outcome_memory_event(_Record(), session_id="S1", recorded_at="T")
        assert event.event_id == "MEM-1"
        assert event.event_type == "OUTCOME_MEMORY_RECORDED"
        assert event.payload["memory_id"] == "MEM-1"

    def test_a_written_record_survives_into_a_fresh_hydration(self, tmp_path):
        """The end-to-end claim of this phase, proven rather than asserted:
        write in one 'session', read back through the REAL cross-session
        hydration path with a brand new store object."""
        from bujji.state_persistence.store import EventStore

        path = str(tmp_path / "outcome_memory_events.jsonl")
        assert persist_outcome_memory(EventStore(path), _Record(),
                                      session_id="SESSION-A", recorded_at="T") == "PERSISTED"

        from bujji.outcome_memory.recovery import hydrate_outcome_memory

        records, report = hydrate_outcome_memory(EventStore(path))
        assert "MEM-1" in records, "the trade was forgotten -- the whole point of D-8"
        assert records["MEM-1"].realized_pnl == pytest.approx(1234.5)
        assert report.transitions_accepted == 1

    def test_two_sessions_accumulate_into_one_campaign_memory(self, tmp_path):
        from bujji.outcome_memory.recovery import hydrate_outcome_memory
        from bujji.state_persistence.store import EventStore

        path = str(tmp_path / "m.jsonl")
        persist_outcome_memory(EventStore(path), _Record("MEM-1"),
                               session_id="DAY-1", recorded_at="T1")
        persist_outcome_memory(EventStore(path), _Record("MEM-2"),
                               session_id="DAY-2", recorded_at="T2")
        records, _ = hydrate_outcome_memory(EventStore(path))
        assert set(records) == {"MEM-1", "MEM-2"}

    def test_writing_the_same_record_twice_is_idempotent_not_doubled(self, tmp_path):
        from bujji.outcome_memory.recovery import hydrate_outcome_memory
        from bujji.state_persistence.store import EventStore

        path = str(tmp_path / "m.jsonl")
        for _ in range(2):
            persist_outcome_memory(EventStore(path), _Record(), session_id="S", recorded_at="T")
        records, _ = hydrate_outcome_memory(EventStore(path))
        assert len(records) == 1

    def test_no_record_persists_nothing(self, tmp_path):
        """attribute_and_remember returns None when attribution was not
        READY. A speculative memory would poison every campaign statistic."""
        from bujji.state_persistence.store import EventStore

        path = tmp_path / "m.jsonl"
        assert persist_outcome_memory(EventStore(str(path)), None,
                                      session_id="S", recorded_at="T") == "NO_RECORD"
        assert not path.exists() or path.read_text() == ""


class TestTheRunnerWiring:
    def _runner_module(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "runner_d8", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        return mod

    def test_the_runner_persists_and_reports_the_outcome(self, tmp_path):
        import logging

        from bujji.state_persistence.store import EventStore

        mod = self._runner_module()
        path = str(tmp_path / "m.jsonl")

        store = EventStore(path)

        class _Stub:
            _logger = logging.getLogger("d8")
            _session_id = "SESSION-D8"

            def _outcome_memory_store(self):
                return store

        stub = _Stub()
        assert mod.OptionsOSRunner._persist_outcome_memory(
            stub, _Record(), recorded_at="T") == "PERSISTED"

        from bujji.outcome_memory.recovery import hydrate_outcome_memory

        records, _ = hydrate_outcome_memory(EventStore(path))
        assert "MEM-1" in records

    def test_a_write_failure_never_crashes_a_completed_session(self):
        """This runs at the very end of a real trading day. A write failure
        must not turn a correctly closed position into a crashed session --
        but it must be loud, not swallowed."""
        import logging

        mod = self._runner_module()

        class _Exploding:
            def append(self, event):
                raise OSError("disk full")

        class _Stub:
            _logger = logging.getLogger("d8")
            _session_id = "S"

            def _outcome_memory_store(self):
                return _Exploding()

        outcome = mod.OptionsOSRunner._persist_outcome_memory(_Stub(), _Record(), recorded_at="T")
        assert outcome.startswith("FAILED:")

    def test_the_store_path_is_cross_session_not_per_session(self, tmp_path):
        """Outcome memory is cross-session by design: burying it inside a
        per-session artifact directory would force a reader to enumerate
        every session to reconstruct the campaign."""
        mod = self._runner_module()

        class _Stub:
            _config = {"artifacts": {"outcome_memory_store_path": str(tmp_path / "sub" / "m.jsonl")}}

        store = mod.OptionsOSRunner._outcome_memory_store(_Stub())
        assert store.path.endswith("m.jsonl")
        assert "shadow_sessions" not in store.path
