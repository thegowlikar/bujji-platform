"""A session that held open risk while blind is never certified.

THE DISTINCTION THIS LOCKS IN, and it is three-way rather than two:

  transient invalidity   price-dependent management is suspended for those
                         cycles, the invalid path is recorded, and NO
                         fabricated valuation is used;
  repeated invalidity    the existing EMERGENCY_BLIND closure path runs;
  ANY open risk held      the session is NOT certified and does NOT exit 0 --
  while blind            whatever happened afterwards, including a perfect
                         closure.

The third is the one that was missing. A session could be blind for two
cycles, never arm the brake, close cleanly at 15:30 and exit 0 -- reporting a
blind session and a sighted one as the same outcome.
"""
import ast
import pathlib

import pytest

from bujji.production_runtime.session_safety_verdict import (
    evaluate_session_safety)

REPO = pathlib.Path(__file__).resolve().parent.parent


def _clean_session(**over):
    """A session that IS certified, so every refusal below is attributable."""
    base = {
        "entry_filled": True,
        "final_positions_status": "FLAT",
        "tick_journal": {"faithful": True, "sealed": True, "records": 500,
                         "dropped": 0},
        "tick_evidence": {"prior_journals": {"corrupt": [], "incomplete": []}},
    }
    base.update(over)
    return base


class TestTheControlIsRealFirst:
    def test_a_clean_session_with_a_position_is_certified(self):
        """POSITIVE CONTROL. Without this, every 'not safe' below could be
        produced by something unrelated and the tests would prove nothing."""
        v = evaluate_session_safety(_clean_session())
        assert v.safe, f"the control session is not clean: {v.reasons}"
        assert v.position_existed


class TestBlindOpenRiskIsNeverCertified:
    def test_one_invalid_cycle_with_an_open_position_is_unsafe(self):
        """BELOW the three-cycle brake threshold, and still not certified."""
        v = evaluate_session_safety(_clean_session(
            price_path_invalid=[{"at": "t1", "reason": "PRICE_QUALITY_REFUSED"}]))
        assert not v.safe
        assert any("price path was INVALID" in r for r in v.reasons)

    @pytest.mark.parametrize("n", [1, 2])
    def test_below_the_brake_threshold_is_still_unsafe(self, n):
        v = evaluate_session_safety(_clean_session(
            price_path_invalid=[{"at": f"t{i}", "reason": "PRICE_QUALITY_REFUSED"}
                                for i in range(n)]))
        assert not v.safe, f"{n} blind cycle(s) with open risk was certified"

    def test_a_perfect_closure_afterwards_does_not_redeem_it(self):
        """The risk was held blind. A later clean exit does not un-hold it."""
        v = evaluate_session_safety(_clean_session(
            price_path_invalid=[{"at": "t1", "reason": "PRICE_PROVIDER_FAILED"}],
            final_positions_status="FLAT",
            closure_reason="EOD_CLOSE_COMPLETE",
            emergency_close_broker_flat=True))
        assert not v.safe

    def test_the_emergency_path_having_run_does_not_redeem_it_either(self):
        v = evaluate_session_safety(_clean_session(
            price_path_invalid=[{"at": f"t{i}", "reason": "PRICE_QUALITY_REFUSED"}
                                for i in range(3)],
            emergency_close_reason="EMERGENCY_BLIND: 3 consecutive unpriced cycles",
            emergency_close_broker_flat=True))
        assert not v.safe

    @pytest.mark.parametrize("reason", [
        "NO_PRICE_PROVIDER", "PRICE_PROVIDER_FAILED", "PRICE_QUALITY_REFUSED"])
    def test_every_typed_reason_reaches_the_verdict(self, reason):
        v = evaluate_session_safety(_clean_session(
            price_path_invalid=[{"at": "t1", "reason": reason}]))
        assert not v.safe
        assert any(reason in r for r in v.reasons), (
            f"{reason} never reached the operator-facing verdict")

    def test_the_reason_names_the_cycle_count(self):
        v = evaluate_session_safety(_clean_session(
            price_path_invalid=[{"at": f"t{i}", "reason": "PRICE_QUALITY_REFUSED"}
                                for i in range(7)]))
        assert any("7 cycle(s)" in r for r in v.reasons)


class TestNoOpenRiskIsDifferent:
    def test_blind_cycles_without_a_position_do_not_fail_the_session(self):
        """The distinction is OPEN RISK, not blindness. A session that never
        filled held nothing it could not see, and failing it would train the
        operator to ignore this signal."""
        v = evaluate_session_safety({
            "entry_filled": False,
            "price_path_invalid": [{"at": "t1", "reason": "NO_PRICE_PROVIDER"}],
        })
        assert v.safe, f"a no-position session was failed for blindness: {v.reasons}"
        assert not v.position_existed


class TestExitCodeConsequence:
    def test_unsafe_maps_to_a_non_zero_exit(self):
        """The verdict must reach the process exit, or it reaches nobody."""
        src = (REPO / "bujji_options_os_runner.py").read_text()
        assert "EXIT_UNSAFE_SESSION" in src
        assert "if not verdict.safe:" in src
        tree = ast.parse(src)
        # The unsafe branch must return the unsafe code, not fall through.
        found = False
        for node in ast.walk(tree):
            if isinstance(node, ast.If) and "verdict.safe" in ast.unparse(node.test):
                if "EXIT_UNSAFE_SESSION" in ast.unparse(node):
                    found = True
        assert found, "an unsafe verdict does not reach a non-zero exit code"


class TestNegativeControls:
    """Each removes the wiring and proves the outcome flips back."""

    def test_removing_the_evidence_key_restores_a_certified_outcome(self):
        """If dropping `price_path_invalid` did NOT restore safe=True, the
        refusal above would be coming from somewhere else."""
        blind = _clean_session(
            price_path_invalid=[{"at": "t1", "reason": "PRICE_QUALITY_REFUSED"}])
        assert not evaluate_session_safety(blind).safe
        del blind["price_path_invalid"]
        assert evaluate_session_safety(blind).safe, (
            "removing the evidence did not restore certification -- the failure "
            "was never attributable to blind open risk")

    def test_an_empty_list_is_not_treated_as_blindness(self):
        """CONTROL against over-triggering: the key present but empty means no
        invalid cycle occurred, and must not fail a healthy session."""
        v = evaluate_session_safety(_clean_session(price_path_invalid=[]))
        assert v.safe, f"an empty invalid-cycle list failed the session: {v.reasons}"

    def test_the_verdict_source_actually_reads_the_key(self):
        """STRUCTURAL: a future refactor that stops reading the key would make
        every test above pass vacuously if they only checked `safe`."""
        src = (REPO / "bujji/production_runtime/session_safety_verdict.py").read_text()
        assert 'summary.get("price_path_invalid")' in src, (
            "the verdict no longer reads the invalid-price evidence")

    def test_the_runtime_actually_writes_the_key(self):
        """The other half of the wiring: evidence nothing writes proves
        nothing, which is the built-not-wired pattern this codebase keeps
        finding."""
        src = (REPO / "bujji_options_os_runner.py").read_text()
        assert 'setdefault("price_path_invalid"' in src, (
            "the runner no longer records invalid price cycles")
