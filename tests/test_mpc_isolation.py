"""Isolation verification for bujji.msi_market_phenomena (MPC) -- Series
103. Same asymmetric pattern as tests/test_cre_isolation.py: MPC must
read real Production Intelligence-layer types, isolated to one file
(translate.py), checked against an explicit allowlist; every other file
stays Production-import-free; no file ever references an order-placing
function."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

MPC_PACKAGE = "bujji.msi_market_phenomena"
MPC_DIR = Path("bujji/msi_market_phenomena")
BUJJI_ROOT = Path("bujji")

_EXCLUDED_DIRS = {"msi_market_phenomena", "__pycache__"}

ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")

TRANSLATE_PY_ALLOWED_IMPORTS = (
    "bujji.msi_market_direction.models", "bujji.msi_market_structure.models",
    "bujji.msi_price_structure.models", "bujji.msi_volatility_structure.models",
    "typing", "__future__",
)

FORBIDDEN_NON_TRANSLATE_IMPORTS = (
    "bujji.broker", "bujji.execution", "bujji.runtime_execution",
    "bujji.msi_strategy_selector", "bujji.msi_portfolio_construction",
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_expression",
    "bujji.msi_margin_bridge", "bujji.msi_execution_planning",
    "bujji.live_pipeline_bridge", "bujji.live_shadow_validation", "bujji.app",
    "bujji.msi_market_direction", "bujji.msi_market_structure",
    "bujji.msi_price_structure", "bujji.msi_volatility_structure",
    "fyers_apiv3",
)


def _all_production_py_files():
    for py_file in BUJJI_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in py_file.parts):
            continue
        yield py_file


def test_no_production_module_imports_mpc():
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(MPC_PACKAGE):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(MPC_PACKAGE):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_market_phenomena -- forbidden path: {offenders}"


def test_non_translate_files_stay_production_import_free():
    offenders = []
    for py_file in MPC_DIR.glob("*.py"):
        if py_file.name == "translate.py":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            module_name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    if any(module_name.startswith(f) for f in FORBIDDEN_NON_TRANSLATE_IMPORTS):
                        offenders.append((str(py_file), module_name))
            if isinstance(node, ast.ImportFrom) and node.module:
                module_name = node.module
                if any(module_name.startswith(f) for f in FORBIDDEN_NON_TRANSLATE_IMPORTS):
                    offenders.append((str(py_file), module_name))
    assert not offenders, f"a non-translate.py file imports a Production package: {offenders}"


def test_translate_py_imports_are_within_the_explicit_allowlist():
    tree = ast.parse((MPC_DIR / "translate.py").read_text())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not any(alias.name.startswith(f) for f in TRANSLATE_PY_ALLOWED_IMPORTS) and not alias.name.startswith("."):
                    offenders.append(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            if not any(node.module.startswith(f) for f in TRANSLATE_PY_ALLOWED_IMPORTS):
                offenders.append(node.module)
    assert not offenders, f"translate.py imports outside its explicit, reviewed allowlist: {offenders}"


def test_no_file_in_mpc_references_order_placing_functions():
    for py_file in MPC_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_engine_module_has_no_io_side_effects():
    tree = ast.parse((MPC_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"


def test_translate_py_never_imports_a_decision_function_only_model_types():
    """translate.py reads real assessment TYPES (models), never a real
    ENGINE module -- it must never be able to invoke a decision
    function, only read already-computed field values."""
    tree = ast.parse((MPC_DIR / "translate.py").read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            assert not node.module.endswith(".engine"), f"translate.py imports an engine module: {node.module}"
