"""Phase 19.20.4 -- market_data_integrity safety boundary. Same pattern
as tests/test_market_observation_contract.py and
tests/test_market_microstructure/test_safety_boundary.py."""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "bujji" / "market_data_integrity"

FORBIDDEN_MODULES = ("bujji.trading_brain", "bujji.production_runtime", "bujji.mic_replay")
FORBIDDEN_EXECUTION_METHODS = ("place_order", "modify_order", "cancel_order")


def _package_files():
    return sorted(PACKAGE_ROOT.glob("*.py"))


def test_package_exists_and_is_non_empty():
    assert _package_files(), f"expected .py files under {PACKAGE_ROOT}"


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
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bujji.broker"), (
                    f"{path.name} imports bujji.broker ({node.module!r}) -- this package must "
                    f"remain read-only market-data verification, never a broker consumer"
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
    forbidden_terms = ("strategy_signal", "entry_signal", "exit_signal", "trade_decision")
    for path in _package_files():
        source = path.read_text().lower()
        for term in forbidden_terms:
            assert term not in source, f"{path.name} references forbidden vocabulary {term!r}"
