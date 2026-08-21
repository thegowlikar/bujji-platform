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


class TestOneReadNotNPlusOne:
    """A safety check that can invent its own alarm is worse than none.

    DEFECT IN d6fbfaf (my own). _expected_symbols called
    registry.get_group_reality() per group, and that re-reads
    get_open_positions() on EVERY call to compute is_open. So reconciling N
    groups issued N+1 broker reads, and derived EXPECTED from reads 1..N while
    OBSERVED came from read N+1. A position closing between them dropped its
    symbol out of EXPECTED while it still appeared in OBSERVED -- a
    manufactured BROKER_ONLY, the CRITICAL finding that blocks new risk.
    """

    def test_the_registry_exposes_a_pure_symbols_accessor(self):
        """Pure means no broker call -- that is the whole point.

        Asserted over the AST, not the source text: the docstring explains
        WHY it does not call get_open_positions, so a substring check trips
        on the explanation rather than on any real call."""
        import ast
        import inspect
        import textwrap

        from bujji.production_runtime.position_reality_registry import PositionRealityRegistry
        tree = ast.parse(textwrap.dedent(
            inspect.getsource(PositionRealityRegistry.symbols_for_group)))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "get_open_positions" not in called
        assert "_open_symbols" not in called
        assert not [n for n in ast.walk(tree) if isinstance(n, ast.Await)]

    def test_expected_symbols_takes_the_observed_set(self):
        """If it computes its own view, the two sides can disagree again."""
        import inspect
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "runner_race", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        sig = inspect.signature(mod.OptionsOSRunner._expected_symbols)
        assert "observed_symbols" in sig.parameters

    def test_expected_symbols_makes_no_broker_call(self):
        """AST again -- the docstring names the methods it deliberately avoids."""
        import ast
        import textwrap

        body = _method_body(
            (REPO_ROOT / "bujji_options_os_runner.py").read_text(), "_expected_symbols")
        tree = ast.parse(textwrap.dedent(body))
        called = {n.func.attr for n in ast.walk(tree)
                  if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
        assert "get_group_reality" not in called, "still re-reads the broker per group"
        assert "get_open_positions" not in called
        assert "run" not in called, "asyncio.run implies a broker call"

    def test_the_reconcile_call_uses_a_single_read(self):
        body = _method_body(
            (REPO_ROOT / "bujji_options_os_runner.py").read_text(),
            "_reconcile_broker_positions")
        assert body.count("discover_broker_positions(") == 1
        assert "self._expected_symbols(observed_symbols)" in body

    def test_a_group_whose_legs_all_closed_raises_no_stale_belief(self):
        """The is_open semantics must survive the fix: a fully-closed group is
        not a belief Bujji still holds, so it must not produce EXPECTED_ONLY."""
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "runner_race2", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        class _Registry:
            def all_group_ids(self):
                return ("PG-CLOSED", "PG-OPEN")

            def symbols_for_group(self, pg):
                return ("CE",) if pg == "PG-OPEN" else ("OLD",)

        class _Stub:
            _registry = _Registry()

        expected = mod.OptionsOSRunner._expected_symbols(_Stub(), {"CE"})
        assert expected == {"CE"}, "a closed group leaked into EXPECTED"


class TestReconciliationIsNotDeadWhereItMatters:
    """DEFECT IN d6fbfaf (mine). _run_one_management_pass opens with
    `if not self._entry_prices: return`, and _entry_prices is populated only
    when an entry FILLS. Reconciliation was called far below that guard, so it
    never ran unless Bujji already believed it held a position -- the exact
    opposite of the case it exists to detect. A position at the broker that
    Bujji does not know about means, by definition, no entry of ours filled:
    _entry_prices is empty, the pass returns at line one, and the unfiltered
    read never happens. The detector was dead precisely where it mattered.

    Asserted over the AST: the fix's own comment quotes the guard it moved
    ahead of, so a source-text check matches the prose instead of the code.
    """

    @staticmethod
    def _statements():
        import ast
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == "_run_one_management_pass":
                return node.body
        raise AssertionError("_run_one_management_pass not found")

    def test_reconciliation_is_the_very_first_statement(self):
        import ast
        first = self._statements()[0]
        assert isinstance(first, ast.Expr)
        assert ast.unparse(first).strip() == "self._reconcile_broker_positions(stage_label)"

    def test_it_precedes_the_entry_prices_guard(self):
        """The guard that made it dead."""
        import ast
        body = self._statements()
        recon = next(i for i, st in enumerate(body)
                     if "_reconcile_broker_positions" in ast.unparse(st))
        guard = next(i for i, st in enumerate(body)
                     if isinstance(st, ast.If) and "_entry_prices" in ast.unparse(st.test))
        assert recon < guard

    def test_it_precedes_every_early_return(self):
        """Two further early returns (no valuation, emergency brake) also sat
        between the guard and the old call site."""
        import ast
        body = self._statements()
        recon = next(i for i, st in enumerate(body)
                     if "_reconcile_broker_positions" in ast.unparse(st))
        returns = [i for i, st in enumerate(body)
                   if any(isinstance(n, ast.Return) for n in ast.walk(st))]
        assert all(recon < r for r in returns), (
            f"an early return at statement {[r for r in returns if r < recon]} "
            "precedes reconciliation")

    def test_there_is_exactly_one_call_site(self):
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert src.count("self._reconcile_broker_positions(stage_label)") == 1


class TestTheEntryGatesAreReachableInProduction:
    """THE defect this file exists to prevent recurring.

    run() branches: `if session.continuous -> _continuous_session() else
    _entry_window()`. The production config SETS session.continuous, so
    production runs _continuous_session(), which calls _attempt_entry
    DIRECTLY. _entry_window was the only caller of
    _data_quality_permits_entry -- so the hard data-quality boundary AND the
    reconciliation block were both unreachable in production while being
    reported as wired.

    The gates now sit at _attempt_entry, the choke point BOTH paths share.
    """

    @staticmethod
    def _fn(name):
        import ast
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_the_gate_is_the_first_executable_statement_of_attempt_entry(self):
        import ast
        body = [st for st in self._fn("_attempt_entry").body
                if not (isinstance(st, ast.Expr) and isinstance(st.value, ast.Constant))]
        assert "_data_quality_permits_entry" in ast.unparse(body[0])

    def test_the_gate_precedes_strategy_selection(self):
        """Selecting a strategy locks it for the day (one strategy per day),
        so gating after selection would burn the lock on a blocked cycle."""
        import ast
        body = self._fn("_attempt_entry").body
        gate = next(i for i, st in enumerate(body)
                    if "_data_quality_permits_entry" in ast.unparse(st))
        select = next(i for i, st in enumerate(body)
                      if "select_and_lock_strategy" in ast.unparse(st))
        assert gate < select

    def test_the_continuous_path_reaches_the_gate(self):
        """Production's actual branch. _continuous_session calls
        _attempt_entry directly -- which is exactly why the gate had to move
        out of _entry_window."""
        import ast
        assert "_attempt_entry" in ast.unparse(self._fn("_continuous_session"))

    def test_the_single_shot_path_also_reaches_it(self):
        import ast
        assert "_attempt_entry" in ast.unparse(self._fn("_entry_window"))

    def test_the_gate_has_exactly_one_call_site(self):
        """One authority, one home. Two homes drift."""
        import ast
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        calls = [n for n in ast.walk(ast.parse(src))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                 and n.func.attr == "_data_quality_permits_entry"]
        assert len(calls) == 1

    def test_the_reconciliation_block_is_read_by_that_gate(self):
        """_reconciliation_blocks_entry was read only inside the gate, so it
        died with it. It must ride the same reachable path."""
        import ast
        assert "_reconciliation_blocks_entry" in ast.unparse(
            self._fn("_data_quality_permits_entry"))

    def test_production_config_actually_takes_the_continuous_branch(self):
        """If this ever flips, the reachability argument above changes and
        this suite should be re-read rather than trusted."""
        import yaml
        cfg = yaml.safe_load(
            (REPO_ROOT / "config" / "options_os_paper_trading.yaml").read_text())
        assert cfg["session"].get("continuous"), (
            "production no longer runs the continuous branch -- re-verify which "
            "path reaches the entry gates")


class TestTheMonitoringLoopRunsWithoutAnEntry:
    """PROVEN LIVE 2026-08-21. A NO_TRADE session produced ZERO records in
    position_reconciliation.jsonl, because _position_management() -- the only
    caller of _run_one_management_pass, which reconciles first -- was gated on
    `entered or _orphan_position_live`.

    That is precisely backwards. The case reconciliation exists for is "Bujji
    believes it holds nothing while the broker holds something", and `entered`
    is False in exactly that case. Moving reconciliation to the top of the
    pass fixed the INNER gate and left this OUTER one closed, so the detector
    still could not run on the day it was needed.
    """

    @staticmethod
    def _fn(name):
        import ast
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, ast.FunctionDef) and node.name == name:
                return node
        raise AssertionError(f"{name} not found")

    def test_continuous_mode_calls_management_unconditionally(self):
        import ast
        body = ast.unparse(self._fn("_continuous_session"))
        assert "self._position_management()" in body
        i = body.index("self._position_management()")
        preceding = body[max(0, i - 220):i]
        assert "if entered" not in preceding, (
            "the monitoring loop is still gated on having entered")

    def test_the_loop_still_reconciles_first(self):
        """The inner fix must survive: reconciliation before every early
        return, so a monitoring-only pass still reads the broker."""
        import ast
        body = self._fn("_run_one_management_pass").body
        assert "_reconcile_broker_positions" in ast.unparse(body[0])

    def test_a_no_position_pass_costs_one_read_then_returns(self):
        """Monitoring-only must not evaluate exits or place anything."""
        import ast
        body = self._fn("_run_one_management_pass").body
        recon = 0
        guard = next(i for i, st in enumerate(body)
                     if isinstance(st, ast.If) and "_entry_prices" in ast.unparse(st.test))
        assert recon < guard
        before = ast.unparse(ast.Module(body=body[:guard], type_ignores=[]))
        for forbidden in ("evaluate_and_enforce_exit", "place_order", "_execute_reduce"):
            assert forbidden not in before

    def test_monitoring_only_does_not_claim_a_naked_position(self):
        """_position_is_undefined_risk fails closed to 'naked' when no
        strategy is recorded -- correct for a real position, but on a
        no-entry day it would log that warning every pass, all day, about a
        position that does not exist."""
        import ast
        body = ast.unparse(self._fn("_position_management"))
        assert "monitoring_only" in body
        i = body.index("monitoring_only")
        assert "naked = False if monitoring_only" in body[i:i + 400]

    def test_every_ticking_loop_reconciles(self):
        """THE INVARIANT CHANGED, deliberately (2026-08-21).

        It was "exactly one call site". That was wrong, and the reasoning
        behind it was wrong: I argued the 15:15->15:30 window did not need
        coverage because _eod_close() follows immediately. But the continuous
        ENTRY loop breaks at observe_until (15:30), not at entry_cutoff --
        past the cutoff it `continue`s, "observation only, all day". So on a
        no-entry day that loop occupies the ENTIRE session and
        _position_management() (whose own loop ends at monitor_until, 15:15)
        runs only afterwards, when its deadline has already passed.

        The clock that ticks all day lives in the continuous loops. The
        invariant that actually protects the property is therefore: EVERY
        loop that ticks must reconcile -- not "there is only one site".
        """
        import ast

        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        tree = ast.parse(src)

        def _reconciles(node) -> bool:
            return any(
                isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "_reconcile_broker_positions"
                for n in ast.walk(node))

        checked = 0
        for name in ("_continuous_session", "_position_management"):
            fn = next(n for n in ast.walk(tree)
                      if isinstance(n, ast.FunctionDef) and n.name == name)
            loops = [n for n in ast.walk(fn) if isinstance(n, (ast.While, ast.For))]
            assert loops, f"{name} has no loop"
            for loop in loops:
                checked += 1
                if name == "_position_management":
                    # Its loop calls the pass, which reconciles first.
                    assert any(
                        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                        and n.func.attr == "_run_one_management_pass"
                        for n in ast.walk(loop))
                else:
                    assert _reconciles(loop), (
                        f"a ticking loop in {name} does not reconcile -- the "
                        "detector is dead for however long that loop runs")
        assert checked >= 3

    def test_the_management_pass_still_reconciles_first(self):
        import ast
        src = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        fn = next(n for n in ast.walk(ast.parse(src))
                  if isinstance(n, ast.FunctionDef) and n.name == "_run_one_management_pass")
        assert "_reconcile_broker_positions" in ast.unparse(fn.body[0])
