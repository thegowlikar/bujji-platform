"""Isolation verification for bujji.msi_opportunity_assessment (OAE) --
Series 104. The simplest isolation contract in the whole learning stack:
OAE imports NOTHING from any other bujji package, anywhere in this
package -- no isolated exception file needed at all (unlike CRE's
replay.py or MPC's translate.py), since OAE only synthesizes over
caller-supplied, local `*View` translations."""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

OAE_PACKAGE = "bujji.msi_opportunity_assessment"
OAE_DIR = Path("bujji/msi_opportunity_assessment")
BUJJI_ROOT = Path("bujji")

_EXCLUDED_DIRS = {"msi_opportunity_assessment", "__pycache__"}

ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")


def _all_production_py_files():
    for py_file in BUJJI_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in py_file.parts):
            continue
        yield py_file


def test_no_production_module_imports_oae():
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(OAE_PACKAGE):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(OAE_PACKAGE):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_opportunity_assessment -- forbidden path: {offenders}"


def test_oae_imports_nothing_from_any_other_bujji_package():
    """The strictest isolation test in the whole learning stack: every
    real import anywhere in this package must be either a standard
    library module or a sibling module within this same package
    (relative import) -- zero exceptions, unlike CRE/MPC."""
    offenders = []
    for py_file in OAE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith("bujji"):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                if node.module.startswith("bujji"):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"msi_opportunity_assessment imports another bujji package -- forbidden: {offenders}"


def test_no_file_in_oae_references_order_placing_functions():
    for py_file in OAE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_engine_module_has_no_io_side_effects():
    tree = ast.parse((OAE_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"


def test_models_have_no_knowledge_candidate_or_engineering_proposal_type():
    """Structural proof of the mission's own 'No Engineering' section --
    there is no dataclass in this package that could represent a
    recommendation, Knowledge Candidate, or Engineering Proposal."""
    import bujji.msi_opportunity_assessment.models as models_module
    import dataclasses
    class_names = {
        name for name, obj in vars(models_module).items()
        if dataclasses.is_dataclass(obj)
    }
    forbidden = {"KnowledgeCandidate", "EngineeringProposal", "Recommendation", "StrategyChange", "ParameterChange"}
    assert class_names.isdisjoint(forbidden)
