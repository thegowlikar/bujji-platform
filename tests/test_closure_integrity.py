"""The book and the record must tell one story.

THE DEFECT (audit 2026-08-21). A session carries three independent accounts
of the same fact -- the broker's own position read, the canonical lifecycle
record, and the archived summary -- and NOTHING compared them. Each was
individually honest, so the contradiction survived indefinitely:

    broker:    FLAT, positively verified, session_closed=True
    lifecycle: canonical_close_outcome="NEVER_EXITED"
    summary:   final_positions_status="FLAT"   <- reads as a clean session

`_capture_exit_fills` had exactly ONE call site: the tail of
`_run_one_management_pass`. Two of the four routes that actually flatten a
position never reached it --

  * `_execute_emergency_close`, which returned straight after
    `run_market_close_sequence()` ON ITS SUCCESS BRANCH; and
  * `_run_eod_closure`, which lives outside the management path entirely

-- so a brake that genuinely worked, or an EOD closure that genuinely
flattened, left `_exit_prices_by_leg` empty. `_close_canonical_lifecycle`
then correctly refused to invent an exit and recorded NEVER_EXITED. The
money moved; the history did not.

These tests pin the funnel, the detector, and the termination path.
"""
from __future__ import annotations

import ast
import inspect
import signal
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

import importlib.util as _u

_spec = _u.spec_from_file_location("_closure_runner", REPO_ROOT / "bujji_options_os_runner.py")
runner_mod = _u.module_from_spec(_spec)
_spec.loader.exec_module(runner_mod)

RUNNER_SRC = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
RUNNER_AST = ast.parse(RUNNER_SRC)


def _method(name):
    for node in ast.walk(RUNNER_AST):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in the runner")


def _calls_in(name):
    return {ast.unparse(n.func).split(".")[-1]
            for n in ast.walk(_method(name)) if isinstance(n, ast.Call)}


class TestEveryFlatteningRouteFinalisesTheRecord:
    """The invariant: if it can flatten, it must funnel."""

    def test_there_is_exactly_one_place_fills_become_lifecycle_truth(self):
        """Two mappers would be two ways to attribute a fill."""
        mappers = [n for n in ast.walk(RUNNER_AST) if isinstance(n, ast.Call)
                   and ast.unparse(n.func).split(".")[-1] == "map_exit_fills_to_legs"]
        assert len(mappers) == 1, f"expected one mapping site, found {len(mappers)}"
        owner = _method("_capture_exit_fills_from")
        assert "map_exit_fills_to_legs" in ast.unparse(owner)

    def test_the_emergency_brake_captures_its_fills(self):
        """It returned after run_market_close_sequence() on SUCCESS, so a
        brake that worked still recorded NEVER_EXITED."""
        assert "_capture_exit_fills" in _calls_in("_execute_emergency_close")

    def test_the_eod_closure_captures_its_fills(self):
        assert "_capture_exit_fills_from" in _calls_in("_run_eod_closure")

    def test_the_ordinary_exit_still_captures_its_fills(self):
        assert "_capture_exit_fills" in _calls_in("_run_one_management_pass")

    def test_the_emergency_route_receives_a_pre_exit_snapshot(self):
        """Fills are attributed by zipping symbols to results. After the exit
        the registry is empty, so the snapshot must be taken BEFORE the brake
        -- it used to be taken after it, which is why the emergency route had
        no list at all."""
        sig = inspect.signature(runner_mod.OptionsOSRunner._execute_emergency_close)
        assert "symbols_before_exit" in sig.parameters

    def test_the_snapshot_is_taken_before_the_brake_not_after(self):
        body = ast.unparse(_method("_run_one_management_pass"))
        snapshot_at = body.index("positions_before_exit")
        brake_at = body.index("_emergency_brake")
        assert snapshot_at < brake_at, (
            "the pre-exit snapshot is taken after the brake again -- the emergency "
            "route will capture nothing")


class TestTheDetector:
    """The check that would have caught this three days ago."""

    @staticmethod
    def _runner_stub(**over):
        r = object.__new__(runner_mod.OptionsOSRunner)
        r._governor_result_summary = over.pop("summary", {})
        r._eod_closure_result = SimpleNamespace(flat=over.pop("broker_flat", None))
        r._canonical_position_id = over.pop("position_id", "POS-1")
        r._exit_prices_by_leg = over.pop("exit_prices", {})
        r._logger = SimpleNamespace(
            critical=lambda *a, **k: None, warning=lambda *a, **k: None)
        return r

    def test_a_clean_session_agrees(self):
        r = self._runner_stub(
            broker_flat=True, exit_prices={"L1": {"exit_price": 10.0}},
            summary={"canonical_close_outcome": "ACCEPTED",
                     "final_positions_status": "FLAT", "session_closed": True})
        r._assert_closure_truths_agree()
        assert r._governor_result_summary["closure_truths_agree"] is True
        assert "closure_truth_divergences" not in r._governor_result_summary

    def test_flat_broker_with_never_exited_lifecycle_is_caught(self):
        """Exactly today's session."""
        r = self._runner_stub(
            broker_flat=True, exit_prices={},
            summary={"canonical_close_outcome": "NEVER_EXITED",
                     "final_positions_status": "FLAT", "session_closed": True})
        r._assert_closure_truths_agree()
        assert r._governor_result_summary["closure_truths_agree"] is False
        divergences = r._governor_result_summary["closure_truth_divergences"]
        assert any("NEVER_EXITED" in d for d in divergences)
        assert any("no exit fill was captured" in d for d in divergences)

    def test_session_closed_while_the_broker_reports_open_legs_is_caught(self):
        r = self._runner_stub(
            broker_flat=False, exit_prices={"L1": {}},
            summary={"canonical_close_outcome": "ACCEPTED", "session_closed": True})
        r._assert_closure_truths_agree()
        assert r._governor_result_summary["closure_truths_agree"] is False

    def test_a_session_that_never_opened_a_position_is_not_flagged(self):
        """NEVER_EXITED is honest when nothing ever closed -- the detector
        must not cry wolf on a no-trade day."""
        r = self._runner_stub(
            broker_flat=True, position_id=None, exit_prices={},
            summary={"final_positions_status": "FLAT", "session_closed": True})
        r._assert_closure_truths_agree()
        assert r._governor_result_summary["closure_truths_agree"] is True

    def test_unknown_broker_truth_is_not_reported_as_agreement_or_divergence(self):
        """flat is None means we could not ask. It must not be graded as
        either -- UNKNOWN never becomes a verdict."""
        r = self._runner_stub(
            broker_flat=None, exit_prices={},
            summary={"canonical_close_outcome": "NEVER_EXITED", "session_closed": False})
        r._assert_closure_truths_agree()
        assert r._governor_result_summary["closure_truths_agree"] is True

    def test_the_detector_can_never_end_a_session(self):
        """A detector that can kill the session is a new way to lose one."""
        r = self._runner_stub(broker_flat=True)
        r._governor_result_summary = None  # force an internal failure
        r._assert_closure_truths_agree()   # must not raise

    def test_it_runs_after_the_lifecycle_is_closed(self):
        """Grading before _close_canonical_lifecycle would read a stale
        outcome and always pass."""
        body = ast.unparse(_method("_session_archive"))
        assert body.index("_close_canonical_lifecycle") < body.index("_assert_closure_truths_agree")


class TestOrderlyTermination:
    def test_sigterm_sets_a_flag_instead_of_killing_the_process(self):
        assert not runner_mod.termination_requested()
        try:
            runner_mod.install_termination_handlers()
            import os
            os.kill(os.getpid(), signal.SIGTERM)
            assert runner_mod.termination_requested(), "SIGTERM did not request a stop"
        finally:
            runner_mod._TERMINATION_REQUESTED.clear()
            signal.signal(signal.SIGTERM, signal.SIG_DFL)
            signal.signal(signal.SIGINT, signal.default_int_handler)

    def test_the_handler_does_not_raise(self):
        """Raising would unwind PAST _eod_close(), tearing the session down
        without ever flattening -- barely better than being killed."""
        handler_src = inspect.getsource(runner_mod.install_termination_handlers)
        inner = [n for n in ast.walk(ast.parse(handler_src.lstrip()))
                 if isinstance(n, (ast.FunctionDef,)) and n.name == "_request_stop"]
        assert inner, "the handler body was not found"
        assert not [n for n in ast.walk(inner[0]) if isinstance(n, ast.Raise)]

    def test_the_all_day_loop_checks_for_a_stop_request(self):
        """The observation loop occupies the whole session; a stop that this
        loop ignores is a stop that never happens."""
        assert "termination_requested" in ast.unparse(_method("_continuous_session"))

    def test_the_management_loop_checks_too(self):
        assert "termination_requested" in ast.unparse(_method("_position_management"))

    def test_the_handler_is_actually_installed(self):
        """A handler nobody calls is this codebase's most common defect --
        built, tested, never reached. AST, so a mention in a docstring or a
        comment cannot satisfy it."""
        installer = _method("install_termination_handlers")
        calls = [n for n in ast.walk(RUNNER_AST) if isinstance(n, ast.Call)
                 and ast.unparse(n.func).split(".")[-1] == "install_termination_handlers"
                 and not (installer.lineno <= n.lineno <= installer.end_lineno)]
        assert calls, "install_termination_handlers is defined but never called"

    def test_breaking_the_loop_still_reaches_eod_closure(self):
        """The point of a cooperative stop: control lands in the NORMAL
        closure path rather than skipping it.

        `_method("run")` would find the MODULE-level run(argv); this needs
        OptionsOSRunner.run, which is the one that sequences the session."""
        cls = next(n for n in ast.walk(RUNNER_AST)
                   if isinstance(n, ast.ClassDef) and n.name == "OptionsOSRunner")
        body = ast.unparse(next(n for n in cls.body
                                if isinstance(n, ast.FunctionDef) and n.name == "run"))
        assert "_eod_close" in body and "_continuous_session" in body
        assert body.index("_continuous_session") < body.index("_eod_close")


class TestTheEodClosureCarriesRealFills:
    def test_the_result_exposes_fills_for_the_lifecycle(self):
        from bujji.production_runtime.eod_closure import EodClosureResult
        assert "exit_fills" in EodClosureResult.__dataclass_fields__

    def test_fills_are_kept_out_of_the_json_artifact(self):
        """An OrderResult is not JSON; putting it in to_dict() would break
        the session artifact the moment a closure actually filled."""
        from bujji.production_runtime.eod_closure import EodClosureResult
        assert "exit_fills" not in EodClosureResult(state="X", flat=None).to_dict()

    def test_the_artifact_records_the_price_it_closed_at(self):
        src = (REPO_ROOT / "bujji" / "production_runtime" / "eod_closure.py").read_text()
        assert '"average_price"' in src, (
            "a flattened session recorded THAT it closed but never AT WHAT")
