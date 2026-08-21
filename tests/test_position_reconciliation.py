"""Continuous reconciliation: belief vs broker truth, every management pass.

WHAT THIS CLOSES. Broker truth was consulted at placement, at startup and at
EOD -- never in between. The only in-session position read went through
PositionRealityRegistry, which INTERSECTS the broker's positions with an
in-memory table of registered symbols, so a position at a symbol Bujji never
registered was mathematically undiscoverable through that API: never valued,
never stop-lossed, never escalated, unnoticed until EOD.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.production_runtime.position_reconciliation import (
    BROKER_ONLY, DIVERGED, EXPECTED_ONLY, MATCH, RECONCILED,
    SEVERITY_CRITICAL, SEVERITY_NONE, SEVERITY_WARNING, UNKNOWN, reconcile)


def _p(symbol, qty=75, side="SELL"):
    return {"symbol": symbol, "qty": qty, "side": side}


class TestBrokerWins:
    def test_memory_flat_broker_open_is_critical(self):
        """The exact gap: exposure Bujji is not managing."""
        r = reconcile(set(), [_p("PE")])
        assert r.verdict == DIVERGED and r.severity == SEVERITY_CRITICAL
        assert r.blocks_new_risk is True
        assert [f.finding for f in r.findings] == [BROKER_ONLY]

    def test_memory_open_broker_flat_is_a_warning_not_a_stop(self):
        """Risk-DECREASING. Usually a completed exit. Worth surfacing; not a
        reason to refuse new risk."""
        r = reconcile({"CE"}, [])
        assert r.verdict == DIVERGED and r.severity == SEVERITY_WARNING
        assert r.blocks_new_risk is False
        assert [f.finding for f in r.findings] == [EXPECTED_ONLY]

    def test_an_extra_broker_leg_outranks_a_stale_belief(self):
        """Both present -> the risk-increasing one decides."""
        r = reconcile({"CE"}, [_p("PE")])
        assert r.severity == SEVERITY_CRITICAL
        assert r.blocks_new_risk is True


class TestAgreement:
    def test_full_agreement_reconciles(self):
        r = reconcile({"CE", "PE"}, [_p("CE"), _p("PE")])
        assert r.verdict == RECONCILED and r.severity == SEVERITY_NONE
        assert {f.finding for f in r.findings} == {MATCH}
        assert r.blocks_new_risk is False

    def test_nothing_expected_nothing_held_reconciles(self):
        assert reconcile(set(), []).verdict == RECONCILED

    def test_a_zero_quantity_row_is_not_a_position(self):
        assert reconcile(set(), [_p("PE", 0)]).verdict == RECONCILED


class TestUnknownIsNeverClean:
    def test_a_failed_read_is_unknown_and_blocks(self):
        """A read we could not perform is not evidence of safety."""
        r = reconcile({"CE"}, None)
        assert r.verdict == UNKNOWN
        assert r.blocks_new_risk is True

    def test_a_malformed_row_is_unknown_not_absence(self):
        r = reconcile({"CE"}, [{"symbol": "CE", "qty": "not-a-number"}])
        assert r.verdict == UNKNOWN
        assert r.blocks_new_risk is True

    def test_unknown_never_reports_reconciled(self):
        assert reconcile(set(), None).verdict != RECONCILED


class TestDeclaredLimitation:
    def test_quantity_is_not_silently_claimed_as_compared(self):
        """PositionRealityRegistry deliberately never caches quantity, so no
        independent expected quantity exists. Inventing one would recreate
        exactly the stale cache the registry avoids; a fabricated comparison
        is worse than a declared gap."""
        r = reconcile({"CE"}, [_p("CE", 25)])
        assert r.quantity_compared is False
        assert "never caches quantity" in r.quantity_not_compared_reason

    def test_the_observed_quantity_is_still_reported(self):
        r = reconcile({"CE"}, [_p("CE", 25)])
        assert [f.observed_qty for f in r.findings if f.symbol == "CE"] == [25]


class TestSerialisation:
    def test_the_record_round_trips(self):
        r = reconcile(set(), [_p("PE")])
        assert json.loads(json.dumps(r.to_dict()))["severity"] == SEVERITY_CRITICAL

    def test_blocks_new_risk_is_in_the_record(self):
        assert reconcile(set(), None).to_dict()["blocks_new_risk"] is True


def _method_body(source: str, name: str) -> str:
    """The full body of one method, from its `def` to the next one.

    Fixed-size character windows were used here first and were brittle -- a
    method longer than the guessed window silently truncated the text being
    asserted against, so a correct implementation failed the test. This reads
    the real boundary instead.
    """
    start = source.index(f"    def {name}(")
    nxt = source.find("\n    def ", start + 1)
    return source[start:nxt if nxt != -1 else len(source)]


class TestProductionWiring:
    RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()

    def test_it_runs_on_every_management_pass(self):
        block = _method_body(self.RUNNER, "_run_one_management_pass")
        assert "self._reconcile_broker_positions(stage_label)" in block

    def test_the_read_is_unfiltered(self):
        """The registry intersects with registered symbols and therefore
        cannot surface an unregistered position."""
        block = _method_body(self.RUNNER, "_reconcile_broker_positions")
        assert "discover_broker_positions(self._broker" in block
        assert "positions_for_group" not in block

    def test_a_failure_to_reconcile_blocks_new_risk(self):
        block = _method_body(self.RUNNER, "_reconcile_broker_positions")
        assert "except Exception" in block
        assert "self._reconciliation_blocks_entry = True" in block

    def test_the_entry_gate_honours_it(self):
        block = _method_body(self.RUNNER, "_data_quality_permits_entry")
        assert "_reconciliation_blocks_entry" in block
        assert "POSITION_RECONCILIATION" in block

    def test_the_block_is_checked_before_the_data_quality_verdict(self):
        """Both gates must run; order only matters for the reported reason."""
        block = _method_body(self.RUNNER, "_data_quality_permits_entry")
        assert block.index("_reconciliation_blocks_entry") < block.index('"_data_quality"')

    def test_evidence_is_persisted(self):
        assert "position_reconciliation.jsonl" in self.RUNNER

    def test_reconciliation_never_mutates_position_state(self):
        """It reports and gates. Closure and registration stay owned by the
        executor, governor and registry."""
        block = _method_body(self.RUNNER, "_reconcile_broker_positions")
        for forbidden in ("mark_closed", "place_order", "register_entry", "_execute_reduce"):
            assert forbidden not in block
