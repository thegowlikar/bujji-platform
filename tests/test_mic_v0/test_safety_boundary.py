"""Phase 20.1 -- mic_v0 safety boundary. Same AST pattern as
tests/test_market_observation_contract.py and the Phase 19.20 packages."""
from __future__ import annotations

import ast
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[2] / "bujji" / "mic_v0"

FORBIDDEN_MODULES = ("bujji.trading_brain", "bujji.production_runtime", "bujji.mic_replay")
FORBIDDEN_EXECUTION_METHODS = ("place_order", "modify_order", "cancel_order")
FORBIDDEN_STRATEGY_TERMS = ("strategy_signal", "entry_signal", "exit_signal", "trade_decision", "position_size")


def _package_files():
    return sorted(PACKAGE_ROOT.glob("*.py"))


def test_package_exists_and_is_non_empty():
    assert _package_files()


def test_no_forbidden_bujji_module_imports():
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


def test_no_broker_module_imported():
    for path in _package_files():
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module:
                assert not node.module.startswith("bujji.broker"), f"{path.name}: {node.module!r}"
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("bujji.broker"), f"{path.name}: {alias.name!r}"


def test_no_execution_method_names_present_anywhere():
    for path in _package_files():
        source = path.read_text()
        for method in FORBIDDEN_EXECUTION_METHODS:
            assert method not in source, f"{path.name} references {method!r}"


def test_no_strategy_or_decision_vocabulary():
    for path in _package_files():
        source = path.read_text().lower()
        for term in FORBIDDEN_STRATEGY_TERMS:
            assert term not in source, f"{path.name} references forbidden vocabulary {term!r}"


def test_no_options_data_dependency():
    """Cycle 1 data-scope rule: this package must never reference an
    options/premium/strike/IV data field -- confirms MIC v0 genuinely
    stayed within futures/spot/VIX scope, not just by convention."""
    forbidden_options_terms = ("strike", "option_chain", "premium_field", "iv_rank", "iv_percentile", "bid_ask")
    for path in _package_files():
        source = path.read_text().lower()
        for term in forbidden_options_terms:
            assert term not in source, f"{path.name} references options-scoped term {term!r}"
