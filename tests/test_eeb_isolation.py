"""Isolation verification for bujji.msi_engineering_evidence_board (EEB)
-- Series 106. Same strictest contract as tests/test_oae_isolation.py
and tests/test_kve_isolation.py: EEB imports NOTHING from any other
bujji package, anywhere in this package."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

EEB_PACKAGE = "bujji.msi_engineering_evidence_board"
EEB_DIR = Path("bujji/msi_engineering_evidence_board")
BUJJI_ROOT = Path("bujji")

_EXCLUDED_DIRS = {"msi_engineering_evidence_board", "__pycache__"}

ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")

# Deliverable: "No code generation" -- a structural, syntactic check that
# no file in this package contains a call to compile/exec/eval, which
# would be the concrete mechanism any code-generation capability would
# require.
CODE_GENERATION_CALLS = ("compile", "exec", "eval")


def _all_production_py_files():
    for py_file in BUJJI_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in py_file.parts):
            continue
        yield py_file


def test_no_production_module_imports_eeb():
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(EEB_PACKAGE):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(EEB_PACKAGE):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_engineering_evidence_board -- forbidden path: {offenders}"


def test_eeb_imports_nothing_from_any_other_bujji_package():
    offenders = []
    for py_file in EEB_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("bujji"):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.startswith("bujji"):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"msi_engineering_evidence_board imports another bujji package -- forbidden: {offenders}"


def test_no_file_in_eeb_references_order_placing_functions():
    for py_file in EEB_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_no_file_in_eeb_can_generate_or_execute_code():
    for py_file in EEB_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in CODE_GENERATION_CALLS:
                pytest.fail(f"{py_file} calls {node.func.id} -- code generation is prohibited")


def test_engine_module_has_no_io_side_effects():
    tree = ast.parse((EEB_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"


def test_models_have_no_code_or_recommendation_type():
    import bujji.msi_engineering_evidence_board.models as models_module
    import dataclasses
    class_names = {
        name for name, obj in vars(models_module).items()
        if dataclasses.is_dataclass(obj)
    }
    forbidden = {"KnowledgeCandidate", "EngineeringProposal", "Recommendation", "CodeChange", "ParameterChange", "Implementation"}
    assert class_names.isdisjoint(forbidden)
