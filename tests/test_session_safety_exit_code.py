"""A session that cannot prove its position closed must FAIL its unit.

THE DEFECT THIS PINS. On 2026-08-21 a paper session ended with all of:

    closure_reason                  = CRITICAL_UNFLATTENED_POSITION
    emergency_close_execution_error = "NameError: 'PositionHealthThresholds'"
    emergency_close_broker_flat     = False
    canonical_close_outcome         = NEVER_EXITED

and `_run_session` returned EXIT_OK. Every one of those facts was computed
correctly and logged at CRITICAL. None of them reached the exit code, so
systemd recorded success, `OnFailure=bujji-alert@%n.service` never fired,
ALERTS.jsonl gained no line, and no push was sent.

An alert DID land that day -- at 15:54:56, twenty-one minutes after the
session logged SHUTDOWN, because an unrelated websocket hang got the
process SIGTERM-killed and systemd failed the unit on the signal. The alarm
worked by accident. A clean exit would have been silent.

These tests assert BOTH halves, because this repo's dominant defect class is
"built, tested, never called":
  * the predicate grades correctly            (TestTheVerdict)
  * `_run_session` actually returns its code  (TestTheExitPathIsWired)
"""
import importlib.util
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.production_runtime.session_safety_verdict import (  # noqa: E402
    SessionSafetyVerdict, evaluate_session_safety,
)


def _runner_module():
    """Load the runner by path, exactly as test_single_symbol_vocabulary does."""
    spec = importlib.util.spec_from_file_location(
        "_runner_exit_guard", REPO_ROOT / "bujji_options_os_runner.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# The real summary from the 2026-08-21 15:33:45 session, trimmed to the keys
# this predicate reads. Copied from the systemd journal, not invented.
REAL_UNSAFE_SESSION = {
    "canonical_position_id": "POS-6745dce3dae0b575df2c3d68",
    "entry_filled": True,
    "final_positions_status": "FLAT",
    "final_positions": [],
    "closure_reason": "CRITICAL_UNFLATTENED_POSITION",
    "emergency_close_reason": (
        "EMERGENCY_BLIND: 4 consecutive unpriced cycles with an open position "
        "-- cannot see, will not hold"),
    "emergency_close_execution_error": (
        "NameError: name 'PositionHealthThresholds' is not defined"),
    "emergency_close_broker_flat": False,
    "canonical_close_outcome": "NEVER_EXITED",
    "session_closed": True,
    "realized_pnl": -14.242799999999551,
}

CLEAN_SESSION = {
    "canonical_position_id": "POS-clean",
    "entry_filled": True,
    "final_positions_status": "FLAT",
    "final_positions": [],
    "closure_reason": "SESSION_COMPLETE",
    "canonical_close_outcome": "ACCEPTED",
    "closure_truths_agree": True,
}


class TestTheVerdict:
    def test_the_real_2026_08_21_session_is_unsafe(self):
        verdict = evaluate_session_safety(REAL_UNSAFE_SESSION)
        assert verdict.safe is False
        assert verdict.position_existed is True
        joined = " ".join(verdict.reasons)
        assert "CRITICAL_UNFLATTENED_POSITION" in joined
        assert "emergency brake" in joined
        assert "did not confirm flat" in joined

    def test_a_session_that_never_opened_a_position_is_safe(self):
        """No trade cannot leave an open position. This must stay quiet --
        an alarm that fires on ordinary no-trade days trains the operator to
        ignore it, which is a safety regression, not a safety feature."""
        assert evaluate_session_safety({}).safe is True
        assert evaluate_session_safety(
            {"entry_allowed": True, "entry_filled": False}).safe is True

    def test_a_clean_close_is_safe(self):
        assert evaluate_session_safety(CLEAN_SESSION).safe is True

    @pytest.mark.parametrize("status", ["OPEN", "UNKNOWN", None])
    def test_only_FLAT_proves_the_book_is_closed(self, status):
        """UNKNOWN is not FLAT. A position whose final state could not be
        established must not exit clean."""
        summary = dict(CLEAN_SESSION, final_positions_status=status)
        verdict = evaluate_session_safety(summary)
        assert verdict.safe is False
        assert "only 'FLAT' proves" in " ".join(verdict.reasons)

    def test_closure_truth_divergence_is_unsafe(self):
        summary = dict(CLEAN_SESSION, closure_truths_agree=False,
                       closure_truth_divergences=["broker FLAT but lifecycle NEVER_EXITED"])
        verdict = evaluate_session_safety(summary)
        assert verdict.safe is False
        assert "closure truths disagree" in " ".join(verdict.reasons)

    def test_an_unresolved_exit_is_unsafe(self):
        summary = dict(CLEAN_SESSION, has_unresolved_exit=True,
                       exits_unresolved=[{"status": "TIMEOUT"}])
        verdict = evaluate_session_safety(summary)
        assert verdict.safe is False
        assert "never resolved" in " ".join(verdict.reasons)

    def test_an_ungradeable_summary_is_unsafe(self):
        """A runner that returned something un-gradeable must not be reported
        clean -- fail closed, never open."""
        for bad in (None, "SESSION_COMPLETE", 0, []):
            assert evaluate_session_safety(bad).safe is False

    def test_the_verdict_never_mutates_its_input(self):
        before = dict(REAL_UNSAFE_SESSION)
        evaluate_session_safety(REAL_UNSAFE_SESSION)
        assert REAL_UNSAFE_SESSION == before

    def test_the_verdict_is_frozen(self):
        verdict = evaluate_session_safety(CLEAN_SESSION)
        assert isinstance(verdict, SessionSafetyVerdict)
        with pytest.raises(Exception):
            verdict.safe = False  # type: ignore[misc]


class TestTheExitPathIsWired:
    """BUILT is not WIRED. These call `_run_session` for real.

    A predicate nothing consults is not a safety mechanism, and a structural
    test asserting the function EXISTS cannot tell the difference.
    """

    @staticmethod
    def _run_with_summary(monkeypatch, tmp_path, summary):
        runner_mod = _runner_module()

        class _FakeRunner:
            def __init__(self, **kwargs):
                pass

            def run(self):
                return summary

        monkeypatch.setattr(runner_mod, "load_config",
                            lambda _p: {"logging": {"namespace": "test-exit-code"}})
        monkeypatch.setattr(runner_mod, "OptionsOSRunner", _FakeRunner)

        class _Args:
            config = str(tmp_path / "cfg.yaml")
            bhavcopy_path = None
            skip_market_hours_check = False
            trend_regime = None
            volatility_regime = None
            session_id = "TEST-SESSION"

        return runner_mod, runner_mod._run_session(_Args(), "2026-08-21")

    def test_an_unsafe_session_exits_nonzero(self, monkeypatch, tmp_path):
        mod, code = self._run_with_summary(monkeypatch, tmp_path, REAL_UNSAFE_SESSION)
        assert code == mod.EXIT_UNSAFE_SESSION
        assert code != 0, "systemd only fires OnFailure= on a NON-ZERO exit"

    def test_a_clean_session_still_exits_zero(self, monkeypatch, tmp_path):
        mod, code = self._run_with_summary(monkeypatch, tmp_path, CLEAN_SESSION)
        assert code == mod.EXIT_OK

    def test_a_no_trade_session_still_exits_zero(self, monkeypatch, tmp_path):
        mod, code = self._run_with_summary(monkeypatch, tmp_path, {"entry_filled": False})
        assert code == mod.EXIT_OK

    def test_the_unsafe_code_is_distinct_from_the_crash_codes(self):
        mod = _runner_module()
        assert mod.EXIT_UNSAFE_SESSION not in (
            mod.EXIT_OK, mod.EXIT_CONFIG_ERROR, mod.EXIT_RUNTIME_ERROR), (
            "an unsafe session is not a crash and must be distinguishable in "
            "the journal from one")
