"""Safety tests -- Phase 15D Observation Memory Recovery.

Confirms bujji/market_state_builder/recovery.py and its wiring into
ShadowSessionRunner/IntelligenceCycleRecorder introduce zero
broker-write capability and leave the existing live-execution safety
boundary completely untouched."""
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
    "bujji/market_state_builder/recovery.py",
    "bujji/shadow_runtime/shadow_session_runner.py",
    "bujji/shadow_runtime/shadow_session_artifact.py",
    "bujji/market_state/intelligence_cycle_recorder.py",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def test_no_forbidden_imports():
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


def test_recovery_module_never_references_a_broker():
    """bujji/market_state_builder/recovery.py is pure file I/O + pure
    replay through MarketStateBuilder -- it must never even reference a
    broker object, live or paper, and never makes a network call."""
    with open(_abs("bujji/market_state_builder/recovery.py")) as f:
        content = f.read()
    for forbidden in ("broker", "Broker", ".connect(", "fyers", "Fyers", "aiohttp", "requests."):
        assert forbidden not in content, f"recovery.py unexpectedly references {forbidden!r}"


def test_recovery_module_never_mutates_capital_or_risk_or_position_state():
    with open(_abs("bujji/market_state_builder/recovery.py")) as f:
        content = f.read()
    for forbidden in ("PaperBroker", "position_intelligence", "capital", "risk_governor", "execution_engine"):
        assert forbidden not in content, f"recovery.py unexpectedly references {forbidden!r}"


def test_broker_guard_and_hybrid_and_paper_untouched_this_phase():
    """Phase 15D touches none of the broker package -- paper.py's only
    legitimate change remains the Phase 15B one, already covered by its
    own dedicated exception in test_phase14b_safety.py."""
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/broker/guard.py", "bujji/broker/hybrid.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"safety guard files were modified, expected untouched: {result.stdout}"


def test_shadow_session_runner_diff_adds_no_new_broker_call():
    result = subprocess.run(
        ["git", "diff", "360c003", "--", "bujji/shadow_runtime/shadow_session_runner.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    added_lines = "\n".join(
        l for l in result.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")
    )
    for forbidden in ("self._broker.place_order", "self._broker.modify_order", "self._broker.cancel_order",
                      "get_open_positions", "get_margins", "get_funds"):
        assert forbidden not in added_lines, f"shadow_session_runner.py diff adds forbidden broker call: {forbidden}"


def test_intelligence_cycle_recorder_diff_scoped_to_additive_params_only():
    """Confirms the ENTIRE Phase 15C + 15D diff to this file (both
    phases touched it) contains zero removed lines -- purely additive
    across both phases combined."""
    result = subprocess.run(
        ["git", "diff", "360c003", "--", "bujji/market_state/intelligence_cycle_recorder.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    removed_code_lines = [
        l for l in result.stdout.splitlines()
        if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
    ]
    assert removed_code_lines == [], f"unexpected removed lines: {removed_code_lines}"


def test_market_state_builder_module_itself_untouched():
    """Phase 15D is a NEW recovery.py alongside market_state.py -- the
    existing MarketStateBuilder/ObservationMemory pure-function core
    must remain byte-for-byte untouched (recovery.py only calls its
    already-existing public constructor/process()/memory interface)."""
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--", "bujji/market_state_builder/market_state.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"market_state.py was modified, expected untouched: {result.stdout}"
