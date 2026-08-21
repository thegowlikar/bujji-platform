"""Phase 20.1B -- mic_v0_validation safety boundary."""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "bujji" / "mic_v0_validation"

FORBIDDEN_MODULES = ("bujji.trading_brain", "bujji.production_runtime", "bujji.mic_replay", "bujji.broker")
FORBIDDEN_EXECUTION_METHODS = ("place_order", "modify_order", "cancel_order")
FORBIDDEN_PNL_TERMS = ("pnl", "profit_loss", "trade_result", "win_loss")


def _package_files():
    return sorted(PACKAGE_ROOT.glob("*.py"))


def test_package_exists_and_is_non_empty():
    assert _package_files()


def test_no_forbidden_module_imports():
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    for forbidden in FORBIDDEN_MODULES:
                        assert not alias.name.startswith(forbidden), f"{path.name}: {alias.name!r}"
            if isinstance(node, ast.ImportFrom) and node.module:
                for forbidden in FORBIDDEN_MODULES:
                    assert not node.module.startswith(forbidden), f"{path.name}: {node.module!r}"


def test_no_execution_method_names():
    for path in _package_files():
        source = path.read_text()
        for method in FORBIDDEN_EXECUTION_METHODS:
            assert method not in source, f"{path.name} references {method!r}"


def test_never_uses_pnl_or_trade_outcome_vocabulary():
    """The charter's own explicit rule: MIC validation uses ONLY market
    data, never strategy P&L -- otherwise "classifier wrong" and
    "strategy failed" become confounded and unanswerable."""
    for path in _package_files():
        source = path.read_text().lower()
        for term in FORBIDDEN_PNL_TERMS:
            assert term not in source, f"{path.name} references forbidden P&L vocabulary {term!r}"
