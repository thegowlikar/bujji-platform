"""Phase 19.20.2 -- market_microstructure safety boundary.

Same AST-based verification pattern as
tests/test_market_observation_contract.py: ast.parse + ast.walk over
every module in the package, checking ast.Import/ast.ImportFrom nodes
against a forbidden-module list, plus a source-text scan for any
execution-surface method name. This is a structural proof, not a
convention -- a forbidden import or an order-placement reference would
fail this test even if never actually called.
"""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "bujji" / "market_microstructure"

FORBIDDEN_MODULES = ("bujji.trading_brain", "bujji.production_runtime", "bujji.mic_replay")
FORBIDDEN_EXECUTION_METHODS = ("place_order", "modify_order", "cancel_order")


def _package_files():
    return sorted(PACKAGE_ROOT.glob("*.py"))


def test_package_exists_and_is_non_empty():
    files = _package_files()
    assert files, f"expected .py files under {PACKAGE_ROOT}"


def test_no_forbidden_bujji_module_imports():
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in FORBIDDEN_MODULES:
                        assert not alias.name.startswith(forbidden), (
                            f"{path.name} imports forbidden module {alias.name!r}"
                        )
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in FORBIDDEN_MODULES:
                    assert not node.module.startswith(forbidden), (
                        f"{path.name} imports from forbidden module {node.module!r}"
                    )


def test_no_broker_module_imported():
    """Additive to the generic forbidden list -- this package has no
    reason to import bujji.broker at all (it is fed already-decoded
    ticks by a caller); importing it would be a scope-creep signal even
    though bujji.broker.fyers_ws itself has no order-placement surface."""
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bujji.broker"), (
                    f"{path.name} imports bujji.broker ({node.module!r}) -- "
                    f"this package must remain a pure market-sensing layer, "
                    f"fed already-decoded ticks by a caller"
                )
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("bujji.broker"), (
                        f"{path.name} imports bujji.broker ({alias.name!r})"
                    )


def test_no_execution_method_names_present_anywhere():
    for path in _package_files():
        source = path.read_text()
        for method in FORBIDDEN_EXECUTION_METHODS:
            assert method not in source, f"{path.name} references forbidden execution method {method!r}"


def test_no_strategy_or_decision_vocabulary():
    """This package must remain observation-only -- no strategy
    selection, no decision, no signal-generation vocabulary. A hit here
    means scope has crept beyond what Phase 19.20.2 was scoped to build."""
    forbidden_terms = ("strategy_signal", "entry_signal", "exit_signal", "trade_decision")
    for path in _package_files():
        source = path.read_text().lower()
        for term in forbidden_terms:
            assert term not in source, f"{path.name} references forbidden vocabulary {term!r}"
