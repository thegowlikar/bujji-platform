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
    # M2 INTEGRATION (2026-08-22). "Clean" now includes evidence the session
    # READ BACK, not evidence it reported about itself. `faithful` below is the
    # writer's own drop count; `verified` is a reader's verdict after
    # re-deriving the content hash and checking it against the manifest, and
    # `replay` is the post-session audit proving the recorded inputs reproduce.
    # A session that cannot produce these is not unsafe -- it is UNCERTIFIED,
    # which is a distinct exit code.
    "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
    "canonical_position_id": "POS-clean",
    "entry_filled": True,
    "final_positions_status": "FLAT",
    "final_positions": [],
    "closure_reason": "SESSION_COMPLETE",
    "canonical_close_outcome": "ACCEPTED",
    "closure_truths_agree": True,
    # EVIDENCE IS PART OF BEING CLEAN (M2). A session that opened a position
    # and cannot produce a sealed, faithful tick journal cannot account for the
    # market it acted on -- so it is no longer "clean" however well it closed.
    #
    # This fixture predates journaling and was INCOMPLETE rather than wrong: a
    # position closed FLAT with full evidence is still safe, which is what the
    # tests below assert. The test that the same shape WITHOUT a journal is
    # UNSAFE lives next to the rule, in
    # test_m2_tick_journal_is_evidence.py::test_an_open_position_with_no_journal_at_all_is_unsafe,
    # and `test_a_clean_close_without_evidence_is_not_clean` below pins it here
    # too so this fixture cannot be quietly completed into meaninglessness.
    "tick_journal": {
        "verified": {"outcome": "VERIFIED", "state": "FAITHFUL"},
        "replay": {"reproduced": True},"sealed": True, "faithful": True,
                     "dropped": 0, "offered": 1200, "written": 1200},
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

    def test_a_clean_close_without_evidence_is_not_clean(self):
        """M2. Closing the book well is not the same as being able to explain
        what you did. Strip the journal from an otherwise perfect session and
        it must stop being safe -- otherwise completing the fixture above would
        have quietly disabled the rule for every test in this class."""
        summary = {k: v for k, v in CLEAN_SESSION.items() if k != "tick_journal"}
        verdict = evaluate_session_safety(summary)
        assert verdict.safe is False
        assert any("tick journal" in r for r in verdict.reasons)

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
        mod, code = self._run_with_summary(monkeypatch, tmp_path, {
            "entry_filled": False,
            # A real no-trade session still inspects prior journals at
            # startup; only a session whose startup did not complete lacks
            # this, and that is worth flagging.
            "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
            # A real session always records this, stating `expected: False`
            # when the configuration has no feed. An ABSENT key means the
            # session never said, which is not certifiable.
            "tick_journal": {"expected": False,
                             "detail": "no tick feed in this configuration"},
        })
        assert code == mod.EXIT_OK

    def test_the_unsafe_code_is_distinct_from_the_crash_codes(self):
        mod = _runner_module()
        assert mod.EXIT_UNSAFE_SESSION not in (
            mod.EXIT_OK, mod.EXIT_CONFIG_ERROR, mod.EXIT_RUNTIME_ERROR), (
            "an unsafe session is not a crash and must be distinguishable in "
            "the journal from one")


class TestPendingEvidenceIsNotSuccess:
    """M2 INTEGRATION. A session that cannot prove what it saw is not a
    successful session -- but it is not the same claim as "something is
    wrong", and an operator must be able to tell them apart from the exit
    code alone."""

    def test_a_session_that_never_inspected_prior_journals_is_not_certified(
            self, monkeypatch, tmp_path):
        mod, code = TestTheExitPathIsWired._run_with_summary(monkeypatch, tmp_path, {"entry_filled": False})
        assert code == mod.EXIT_PENDING_EVIDENCE
        assert code != mod.EXIT_OK, "an uncertified session must not report success"
        assert code != mod.EXIT_UNSAFE_SESSION, (
            "nothing is known to be wrong -- conflating this with UNSAFE tells "
            "the operator the wrong thing")

    def test_an_unverified_tick_journal_is_not_certified(self, monkeypatch, tmp_path):
        mod, code = TestTheExitPathIsWired._run_with_summary(monkeypatch, tmp_path, {
            "entry_filled": False,
            "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
            "tick_journal": {"sealed": True, "faithful": True},
        })
        assert code == mod.EXIT_PENDING_EVIDENCE, (
            "`faithful` is the writer's own count; without a reader's verdict "
            "the session is not certified")

    def test_a_journal_that_fails_verification_is_UNSAFE_not_merely_pending(
            self, monkeypatch, tmp_path):
        """The writer's counters and the file on disk disagree. That is not
        missing evidence -- it is evidence that is wrong."""
        mod, code = TestTheExitPathIsWired._run_with_summary(monkeypatch, tmp_path, {
            "entry_filled": False,
            "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
            "tick_journal": {"sealed": True, "faithful": True,
                             "verified": {"outcome": "NOT_FAITHFUL",
                                          "state": "CORRUPT", "detail": "hash mismatch"},
                             "replay": {"reproduced": True}},
        })
        assert code == mod.EXIT_UNSAFE_SESSION

    def test_a_corrupt_prior_journal_is_UNSAFE(self, monkeypatch, tmp_path):
        mod, code = TestTheExitPathIsWired._run_with_summary(monkeypatch, tmp_path, {
            "entry_filled": False,
            "prior_tick_journals": {"inspected": True, "unsealed": [],
                                    "corrupt": [{"path": "/x/y.jsonl"}]},
            "tick_journal": {"sealed": True, "faithful": True,
                             "verified": {"outcome": "VERIFIED", "state": "FAITHFUL"},
                             "replay": {"reproduced": True}},
        })
        assert code == mod.EXIT_UNSAFE_SESSION

    def test_a_replay_mismatch_is_UNSAFE(self, monkeypatch, tmp_path):
        mod, code = TestTheExitPathIsWired._run_with_summary(monkeypatch, tmp_path, {
            "entry_filled": False,
            "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
            "tick_journal": {"sealed": True, "faithful": True,
                             "verified": {"outcome": "VERIFIED", "state": "FAITHFUL"},
                             "replay": {"reproduced": False, "detail": "streams differ"}},
        })
        assert code == mod.EXIT_UNSAFE_SESSION

    def test_the_pending_code_is_distinct_from_every_other_outcome(self):
        mod = _runner_module()
        assert mod.EXIT_PENDING_EVIDENCE not in (
            mod.EXIT_OK, mod.EXIT_CONFIG_ERROR, mod.EXIT_RUNTIME_ERROR,
            mod.EXIT_UNSAFE_SESSION)



def test_a_declared_absent_journal_still_certifies():
    """A replay or store-backed session records no ticks and none were
    expected. Declaring that is certifiable; staying silent is not."""
    from bujji.production_runtime.session_safety_verdict import evaluate_session_safety

    declared = evaluate_session_safety({
        "entry_filled": False,
        "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
        "tick_journal": {"expected": False, "detail": "no feed"},
    })
    silent = evaluate_session_safety({
        "entry_filled": False,
        "prior_tick_journals": {"inspected": True, "unsealed": [], "corrupt": []},
    })
    assert declared.certified
    assert not silent.certified, (
        "silence and a declared absence must not mean the same thing")
