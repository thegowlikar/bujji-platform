"""Isolation verification for bujji.msi_counterfactual_replay (CRE) --
Series 102.

CRE is architecturally different from Series 100/101: it MUST invoke real
Production decision functions to do its job (replaying alternative
decision paths). The isolation contract here is therefore asymmetric:

  1. NO Production module anywhere under bujji/ imports CRE (same
     permanently-forbidden direction as Series 100/101).
  2. Every file in CRE EXCEPT replay.py stays Production-import-free,
     exactly like Series 100/101's full package (engine.py/models.py/
     taxonomy.py/config.py/serialization.py/journal.py/query.py never
     import a real decision function).
  3. replay.py -- the one, disclosed exception -- may import the real
     replay-calling-convention modules it needs, but its imports are
     checked against an explicit allowlist (nothing broader is silently
     permitted), and it (like every other file in this package) must
     NEVER reference an order-placing function.
  4. No file anywhere in this package ever references
     place_order/submit_and_confirm/modify_order/cancel_order.
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

CRE_PACKAGE = "bujji.msi_counterfactual_replay"
CRE_DIR = Path("bujji/msi_counterfactual_replay")
BUJJI_ROOT = Path("bujji")

_EXCLUDED_DIRS = {"msi_counterfactual_replay", "__pycache__"}

ORDER_PLACING_ATTRS = ("place_order", "submit_and_confirm", "modify_order", "cancel_order")

# The narrow, explicit allowlist for replay.py -- CRE's one disclosed
# exception. Anything importing outside this list from replay.py fails
# the test, so the exception cannot silently widen over time.
REPLAY_PY_ALLOWED_IMPORTS = (
    "bujji.live_pipeline_bridge", "bujji.live_shadow_validation", "bujji.market_episode",
    "bujji.market_observation", "bujji.live_market_events", "bujji.msi_portfolio_construction.models",
    "typing", "__future__",
)

# The rest of the package (everything except replay.py) must be as
# Production-import-free as Series 100/101.
FORBIDDEN_NON_REPLAY_IMPORTS = (
    "bujji.broker", "bujji.execution", "bujji.runtime_execution",
    "bujji.msi_strategy_selector", "bujji.msi_portfolio_construction",
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_expression",
    "bujji.msi_margin_bridge", "bujji.msi_execution_planning",
    "bujji.live_pipeline_bridge", "bujji.live_shadow_validation", "bujji.app",
    "fyers_apiv3", "bujji.market_observation", "bujji.market_episode", "bujji.live_market_events",
)


def _all_production_py_files():
    for py_file in BUJJI_ROOT.rglob("*.py"):
        if any(part in _EXCLUDED_DIRS for part in py_file.parts):
            continue
        yield py_file


def test_no_production_module_imports_cre():
    offenders = []
    for py_file in _all_production_py_files():
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name.startswith(CRE_PACKAGE):
                        offenders.append((str(py_file), alias.name))
            if isinstance(node, ast.ImportFrom) and node.module:
                if node.module.startswith(CRE_PACKAGE):
                    offenders.append((str(py_file), node.module))
    assert not offenders, f"Production module(s) import msi_counterfactual_replay -- forbidden path: {offenders}"


def test_non_replay_files_stay_production_import_free():
    offenders = []
    for py_file in CRE_DIR.glob("*.py"):
        if py_file.name == "replay.py":
            continue
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            module_name = None
            if isinstance(node, ast.Import):
                for alias in node.names:
                    module_name = alias.name
                    if any(module_name.startswith(f) for f in FORBIDDEN_NON_REPLAY_IMPORTS):
                        offenders.append((str(py_file), module_name))
            if isinstance(node, ast.ImportFrom) and node.module:
                module_name = node.module
                if any(module_name.startswith(f) for f in FORBIDDEN_NON_REPLAY_IMPORTS):
                    offenders.append((str(py_file), module_name))
    assert not offenders, f"a non-replay.py file imports a Production package: {offenders}"


def test_replay_py_imports_are_within_the_explicit_allowlist():
    tree = ast.parse((CRE_DIR / "replay.py").read_text())
    offenders = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not any(alias.name.startswith(f) for f in REPLAY_PY_ALLOWED_IMPORTS) and not alias.name.startswith("."):
                    offenders.append(alias.name)
        if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            # node.level > 0 means a relative import (e.g. `from . import config`,
            # `from .models import ExploredPath`) -- always allowed, it's this
            # package's own sibling modules, not a Production import.
            if not any(node.module.startswith(f) for f in REPLAY_PY_ALLOWED_IMPORTS):
                offenders.append(node.module)
    assert not offenders, f"replay.py imports outside its explicit, reviewed allowlist: {offenders}"


def test_no_file_in_cre_references_order_placing_functions():
    for py_file in CRE_DIR.glob("*.py"):
        tree = ast.parse(py_file.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute) and node.attr in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} references forbidden execution attribute {node.attr}")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id in ORDER_PLACING_ATTRS:
                pytest.fail(f"{py_file} calls forbidden execution function {node.func.id}")


def test_engine_module_has_no_io_side_effects():
    tree = ast.parse((CRE_DIR / "engine.py").read_text())
    forbidden_calls = {"open", "requests", "urlopen", "socket"}
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            assert node.func.id not in forbidden_calls, f"engine.py performs IO via {node.func.id}"
