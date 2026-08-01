"""Tests — Gate D0 fail-closed wiring rehearsal.

The central claim under test is structural, not behavioral: this
module must have NO possible dispatch path, proven by inspecting its
actual import graph and call chain, not by trusting its docstring.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.production_runtime import d0_rehearsal_runtime
from bujji.production_runtime.d0_rehearsal_runtime import (
    D0RehearsalInput,
    D0RehearsalResult,
    run_d0_rehearsal,
)
from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment
from bujji.trading_brain.risk_governor.defined_risk import DefinedRiskAssessment
from bujji.trading_brain.risk_governor.engine import RiskVerdict
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimitAssessment
from bujji.trading_brain.risk_governor.position_group_fold import LIFECYCLE_OPEN, PositionGroupState

_DISPATCH_CAPABLE_MODULE_PREFIXES = (
    "bujji.runtime_execution", "bujji.broker", "bujji.broker_adapter", "bujji.execution",
)


def _clock(iso="2026-08-01T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _all_group():
    return PositionGroupState(position_group_id="PG-1", lifecycle_state=LIFECYCLE_OPEN)


def _all_allow_input():
    return D0RehearsalInput(
        group_state=_all_group(),
        defined_risk=DefinedRiskAssessment(
            position_group_id="PG-1", decision="ALLOW", blocking_reason=None,
            max_loss=1000.0, formula_used="X", evaluated_at=_clock()(),
        ),
        portfolio_limits=PortfolioLimitAssessment(
            decision="ALLOW", blocking_reason=None, active_position_count=1,
            concentration_by_underlying={}, evaluated_at=_clock()(),
        ),
        capital_check=CapitalCheckAssessment(decision="ALLOW", blocking_reason=None, evaluated_at=_clock()()),
    )


# --------------------------------------------------------------------- #
# Structural proof: no dispatch-capable import exists in this module at all
# --------------------------------------------------------------------- #

def test_no_dispatch_capable_imports():
    source_path = Path(inspect.getfile(d0_rehearsal_runtime))
    tree = ast.parse(source_path.read_text())
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)

    offenders = [
        m for m in imported_modules
        if any(m == prefix or m.startswith(prefix + ".") for prefix in _DISPATCH_CAPABLE_MODULE_PREFIXES)
    ]
    assert offenders == [], f"d0_rehearsal_runtime.py imports dispatch-capable module(s): {offenders}"


def test_module_source_never_mentions_dispatch_by_name():
    """A second, independent check -- not just imports, but the literal
    text of the module never references a dispatch/place_order/submit
    call, in case such a capability were somehow reached without an
    import (e.g. via a dynamically-resolved attribute)."""
    source_path = Path(inspect.getfile(d0_rehearsal_runtime))
    text = source_path.read_text()
    forbidden_substrings = ["dispatch(", "place_order(", "queue_for_dispatch(", ".connect()"]
    offenders = [s for s in forbidden_substrings if s in text]
    assert offenders == [], f"d0_rehearsal_runtime.py's source text contains: {offenders}"


def test_module_does_not_import_or_touch_composition_root():
    """D0 is entirely additive -- it must not IMPORT CompositionRoot or
    any of the existing production_runtime entry points, confirming
    this is a standalone rehearsal, not a modification of the real
    startup/composition path. Checked at the import-statement level
    (AST), not by scanning raw text -- the module's own docstring
    legitimately mentions these names in prose explaining what it does
    NOT do, which a naive substring check would misfire on."""
    source_path = Path(inspect.getfile(d0_rehearsal_runtime))
    tree = ast.parse(source_path.read_text())
    imported_names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported_names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported_names.append(node.module)
            imported_names.extend(alias.name for alias in node.names)

    forbidden = ("composition_root", "CompositionRoot", "startup")
    offenders = [n for n in imported_names if any(f in n for f in forbidden)]
    assert offenders == [], f"d0_rehearsal_runtime.py imports: {offenders}"


# --------------------------------------------------------------------- #
# Adversarial: even a hand-forced ALLOW verdict cannot reach a dispatch
# call, because no such call exists anywhere reachable from this module.
# --------------------------------------------------------------------- #

def test_all_allow_input_produces_allow_verdict_and_nothing_more():
    result = run_d0_rehearsal(_all_allow_input(), clock=_clock())
    assert isinstance(result, D0RehearsalResult)
    assert result.verdict.decision == "ALLOW"
    # The result object itself has no method/attribute that could act on
    # the verdict -- confirmed by enumerating its own public surface.
    public_attrs = [a for a in dir(result) if not a.startswith("_")]
    assert set(public_attrs) <= {"verdict", "rehearsal_note"}


def test_hand_forced_allow_verdict_has_no_consumer_in_this_module():
    """Simulates a hypothetical future bug: construct a RiskVerdict
    directly (bypassing assess() entirely) and confirm this module
    contains no function that accepts a RiskVerdict and does anything
    beyond wrapping it in D0RehearsalResult."""
    forged_allow = RiskVerdict(
        decision="ALLOW", blocking_reason="", failed_checks=(), passed_checks=("FORGED",),
        decision_trace="forged for adversarial test", evaluated_at=_clock()(),
    )
    result = D0RehearsalResult(verdict=forged_allow)
    # The only thing that can be done with this result in this module's
    # own namespace is read its two fields -- there is no dispatch(),
    # execute(), or submit() function defined anywhere in this module
    # to hand it to.
    module_functions = [
        name for name, obj in vars(d0_rehearsal_runtime).items()
        if inspect.isfunction(obj) and obj.__module__ == d0_rehearsal_runtime.__name__
    ]
    assert module_functions == ["run_d0_rehearsal"]


# --------------------------------------------------------------------- #
# Functional coverage: the rehearsal correctly reflects real veto paths
# --------------------------------------------------------------------- #

def test_veto_path_reflected_correctly():
    rehearsal_input = _all_allow_input()
    vetoed_capital = CapitalCheckAssessment(
        decision="VETO", blocking_reason="MARGIN_NOT_CERTIFIED", evaluated_at=_clock()(),
    )
    rehearsal_input = D0RehearsalInput(
        group_state=rehearsal_input.group_state, defined_risk=rehearsal_input.defined_risk,
        portfolio_limits=rehearsal_input.portfolio_limits, capital_check=vetoed_capital,
    )
    result = run_d0_rehearsal(rehearsal_input, clock=_clock())
    assert result.verdict.decision == "VETO"
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks
