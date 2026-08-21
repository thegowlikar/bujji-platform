"""Isolation verification for bujji.msi_knowledge_validation (KVE) --
Series 105. Same strictest contract as tests/test_oae_isolation.py: KVE
imports NOTHING from any other bujji package, anywhere in this package."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

KVE_PACKAGE = "bujji.msi_knowledge_validation"
KVE_DIR = Path("bujji/msi_knowledge_validation")
BUJJI_ROOT = Path("bujji")

_EXCLUDED_DIRS = {"msi_knowledge_validation", "__pycache__"}

ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")


def _all_production_py_files():
    for py_file in BUJJI_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in py_file.parts):
            continue
        yield py_file


def test_no_production_module_imports_kve():
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(KVE_PACKAGE):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(KVE_PACKAGE):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_knowledge_validation -- forbidden path: {offenders}"


def test_kve_imports_nothing_from_any_other_bujji_package():
    offenders = []
    for py_file in KVE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("bujji"):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.startswith("bujji"):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"msi_knowledge_validation imports another bujji package -- forbidden: {offenders}"


def test_no_file_in_kve_references_order_placing_functions():
    for py_file in KVE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_engine_module_has_no_io_side_effects():
    tree = ast.parse((KVE_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"


def test_models_have_no_knowledge_candidate_or_engineering_proposal_type():
    import bujji.msi_knowledge_validation.models as models_module
    import dataclasses
    class_names = {
        name for name, obj in vars(models_module).items()
        if dataclasses.is_dataclass(obj)
    }
    forbidden = {"KnowledgeCandidate", "EngineeringProposal", "Recommendation", "StrategyChange", "ParameterChange"}
    assert class_names.isdisjoint(forbidden)
