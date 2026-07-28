"""Isolation verification for bujji.msi_market_learning (MLE) -- Series
100, Phase 1.0.

Enforces structurally, not by convention (mirrors
test_live_shadow_operator.py's own AST-based precedent for Shadow Safety):

  1. NO Production module anywhere under bujji/ imports msi_market_learning.
     ("There must never exist: MLE -> Production" -- this is the one path
     the Series 100 spec calls "permanently forbidden".)
  2. msi_market_learning itself imports NOTHING from any Production
     decision/execution/broker package -- it is deliberately import-blind
     to Production (mirrors msi_strategy_selector's sibling-isolation
     convention).
  3. msi_market_learning never references order-placing functions at all.

This test runs as part of the normal pytest suite -- i.e. the existing
regression gate IS the CI enforcement the spec asks for; no separate
tooling is needed since this project's discipline already treats a red
pytest run as a hard stop.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

MLE_PACKAGE = "bujji.msi_market_learning"
MLE_DIR = Path("bujji/msi_market_learning")
BUJJI_ROOT = Path("bujji")

# Directories that are not "Production" in the sense this test cares
# about (the package under test itself, and non-source directories).
_EXCLUDED_DIRS = {"msi_market_learning", "__pycache__"}

FORBIDDEN_PRODUCTION_IMPORT = MLE_PACKAGE

# The set of real, execution-capable functions this project already uses
# elsewhere (test_live_shadow_operator.py) as the concrete definition of
# "an order could be placed" -- reused verbatim, not redefined.
ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")

# Production packages MLE must never import from (decision/execution/
# broker surfaces) -- mirrors msi_strategy_selector's own sibling-
# isolation convention: MLE reads real DATA (ids/strings/timestamps) that
# a caller supplies, never imports another package's live decision types.
FORBIDDEN_MLE_IMPORTS = (
    "bujji.broker", "bujji.execution", "bujji.runtime_execution",
    "bujji.msi_strategy_selector", "bujji.msi_portfolio_construction",
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_expression",
    "bujji.msi_margin_bridge", "bujji.msi_execution_planning",
    "bujji.live_pipeline_bridge", "bujji.live_shadow_validation", "bujji.app",
    "fyers_apiv3",
)


def _all_production_py_files():
    for py_file in BUJJI_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in py_file.parts):
            continue
        yield py_file


def test_no_production_module_imports_mle():
    """The one permanently-forbidden path: MLE -> Production. Checked as
    the REVERSE direction here -- does any real Production file import
    FROM msi_market_learning."""
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(FORBIDDEN_PRODUCTION_IMPORT):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(FORBIDDEN_PRODUCTION_IMPORT):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_market_learning -- forbidden path: {offenders}"


def test_mle_never_imports_production_decision_or_execution_packages():
    """MLE is deliberately import-blind to Production -- it never reaches
    UP into a decision function, broker, or execution package."""
    offenders = []
    for py_file in MLE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            module_name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    if any(module_name.startswith(f) for f in FORBIDDEN_MLE_IMPORTS):
                        offenders.append((str(py_file), module_name))
            if isinstance(node, ast.ImportFrom) and node.module:
                module_name = node.module
                if any(module_name.startswith(f) for f in FORBIDDEN_MLE_IMPORTS):
                    offenders.append((str(py_file), module_name))
    assert not offenders, f"msi_market_learning imports a forbidden Production package: {offenders}"


def test_mle_never_references_order_placing_functions():
    """AST-based, per this project's own false-positive-safe convention
    (a raw string search would trip on this very test's own docstrings)."""
    for py_file in MLE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_mle_has_no_io_side_effects_in_engine_module():
    """engine.py must contain no filesystem/network calls -- only
    journal.py is permitted to touch disk (mirrors every other MSI
    package's engine.py/journal.py split)."""
    tree = ast.parse((MLE_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"
