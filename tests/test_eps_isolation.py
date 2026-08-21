"""Isolation verification for bujji.msi_evidence_packet (EPS) -- Series
101. Mirrors tests/test_mle_isolation.py's AST-based approach exactly:
same two-directional check (no Production module imports EPS; EPS never
imports Production), same order-placing-function check."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

EPS_PACKAGE = "bujji.msi_evidence_packet"
EPS_DIR = Path("bujji/msi_evidence_packet")
BUJJI_ROOT = Path("bujji")

_EXCLUDED_DIRS = {"msi_evidence_packet", "__pycache__"}

ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")

FORBIDDEN_EPS_IMPORTS = (
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


def test_no_production_module_imports_eps():
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(EPS_PACKAGE):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(EPS_PACKAGE):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_evidence_packet -- forbidden path: {offenders}"


def test_eps_never_imports_production_decision_or_execution_packages():
    offenders = []
    for py_file in EPS_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if any(alias.name.startswith(f) for f in FORBIDDEN_EPS_IMPORTS):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if any(node.module.startswith(f) for f in FORBIDDEN_EPS_IMPORTS):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"msi_evidence_packet imports a forbidden Production package: {offenders}"


def test_eps_never_references_order_placing_functions():
    for py_file in EPS_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_eps_has_no_io_side_effects_in_engine_module():
    tree = ast.parse((EPS_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"
