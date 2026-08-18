"""Safety tests -- Phase 15C Runtime Recovery Integration.

Confirms the additive Phase 15C changes (bujji/shadow_runtime/recovery.py,
the constructor/wiring changes to shadow_session_runner.py and
intelligence_cycle_recorder.py) introduce zero broker-write capability
and leave the project's existing live-execution safety boundary
completely untouched."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "fyers_apiv3",
)
_CHECKED_FILES = (
    "bujji/shadow_runtime/recovery.py",
    "bujji/shadow_runtime/shadow_session_runner.py",
    "bujji/shadow_runtime/shadow_session_artifact.py",
    "bujji/market_state/intelligence_cycle_recorder.py",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def test_no_forbidden_imports_in_recovery_module():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel} imports forbidden module {name}"


def test_no_forbidden_order_calls_ast_based():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_recovery_module_never_references_broker_at_all():
    """The recovery module is pure event-store I/O -- it must never even
    reference a broker object, live or paper."""
    with open(_abs("bujji/shadow_runtime/recovery.py")) as f:
        content = f.read()
    for forbidden in ("broker", "Broker", ".connect(", "fyers", "Fyers"):
        assert forbidden not in content, f"recovery.py unexpectedly references {forbidden!r}"


def test_broker_guard_and_hybrid_untouched():
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--", "bujji/broker/guard.py", "bujji/broker/hybrid.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"safety guard files were modified, expected untouched: {result.stdout}"


def test_shadow_session_runner_still_makes_no_new_broker_calls():
    """The runner's own module docstring makes an exhaustive claim about
    which broker methods it calls -- confirm the Phase 15C diff added no
    NEW broker method call. Diff-based, not just current-content-based,
    so an accidental addition would be caught even if it looked
    superficially like an existing call."""
    result = subprocess.run(
        ["git", "diff", "b148e39", "--", "bujji/shadow_runtime/shadow_session_runner.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    added_lines = "\n".join(
        l for l in result.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")
    )
    for forbidden in ("self._broker.place_order", "self._broker.modify_order", "self._broker.cancel_order",
                      "get_open_positions", "get_margins", "get_funds"):
        assert forbidden not in added_lines, f"shadow_session_runner.py diff adds forbidden broker call: {forbidden}"


def test_intelligence_cycle_recorder_diff_is_scoped_to_the_recovery_parameter():
    """The only functional change to intelligence_cycle_recorder.py
    should be the additive `initial_regime_state` constructor parameter
    -- confirm no removed lines (nothing existing was changed)."""
    result = subprocess.run(
        ["git", "diff", "b148e39", "--", "bujji/market_state/intelligence_cycle_recorder.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    removed_code_lines = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    assert removed_code_lines == [], f"unexpected removed lines: {removed_code_lines}"
