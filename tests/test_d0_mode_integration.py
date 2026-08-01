"""Tests — Gate D0 production_runtime wiring (d0_mode_integration.py).

Uses a REAL CompositionRoot, built by the real, untouched
build_composition_root(), not a mock -- proving the wiring works
end-to-end against the actual production object graph.
"""
from __future__ import annotations

import ast
import inspect
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bujji.production_runtime import d0_mode_integration
from bujji.production_runtime.composition_root import build_composition_root
from bujji.production_runtime.config import (
    RUNTIME_MODE_D0_REHEARSAL,
    RUNTIME_MODE_PRODUCTION_READY,
    RUNTIME_MODE_SHADOW,
    ALL_RUNTIME_MODES,
    InvalidRuntimeConfig,
    RuntimeConfig,
)
from bujji.production_runtime.d0_mode_integration import WrongRuntimeModeError, run_d0_rehearsal_mode
from bujji.production_runtime.d0_rehearsal_runtime import D0RehearsalInput
from bujji.trading_brain.risk_governor.capital_check import CapitalCheckAssessment
from bujji.trading_brain.risk_governor.defined_risk import DefinedRiskAssessment
from bujji.trading_brain.risk_governor.portfolio_limits import PortfolioLimitAssessment
from bujji.trading_brain.risk_governor.position_group_fold import LIFECYCLE_OPEN, PositionGroupState

_DISPATCH_CAPABLE_MODULE_PREFIXES = (
    "bujji.runtime_execution", "bujji.broker", "bujji.broker_adapter", "bujji.execution",
)


def _clock(iso="2026-08-05T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _all_allow_input():
    return D0RehearsalInput(
        group_state=PositionGroupState(position_group_id="PG-1", lifecycle_state=LIFECYCLE_OPEN),
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
# Config vocabulary
# --------------------------------------------------------------------- #

def test_d0_rehearsal_is_a_valid_mode():
    assert RUNTIME_MODE_D0_REHEARSAL in ALL_RUNTIME_MODES
    assert len(ALL_RUNTIME_MODES) == 4


def test_fyers_broker_still_rejected_in_d0_rehearsal_mode():
    """The existing P1-1 guard (mode != PRODUCTION_READY -> fyers
    rejected) must cover the new mode automatically -- no separate
    check was added, and none should have been needed."""
    with pytest.raises(InvalidRuntimeConfig):
        RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="fyers")


def test_paper_broker_allowed_in_d0_rehearsal_mode():
    config = RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="paper")
    assert config.mode == RUNTIME_MODE_D0_REHEARSAL


# --------------------------------------------------------------------- #
# Structural: no dispatch-capable import in this integration file either
# --------------------------------------------------------------------- #

def test_no_dispatch_capable_imports_in_integration_module():
    source_path = Path(inspect.getfile(d0_mode_integration))
    tree = ast.parse(source_path.read_text())
    imported = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.extend(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.append(node.module)
    offenders = [
        m for m in imported
        if any(m == p or m.startswith(p + ".") for p in _DISPATCH_CAPABLE_MODULE_PREFIXES)
    ]
    assert offenders == [], f"d0_mode_integration.py imports dispatch-capable module(s): {offenders}"


def test_integration_module_never_reads_broker_or_execution_fields_from_root():
    """A second, independent check -- the function body itself must
    never attribute-access root.broker/root.execution_engine/
    root.execution_adapter, even though CompositionRoot carries them.
    AST-based on Attribute nodes specifically (not a raw text scan),
    so the module's own docstring mentioning these names in prose
    explaining what it does NOT do can't cause a false positive --
    the same convention already used elsewhere in this codebase
    (e.g. test_live_shadow_operator.py's own forbidden-attribute check)."""
    source_path = Path(inspect.getfile(d0_mode_integration))
    tree = ast.parse(source_path.read_text())
    forbidden_attrs = {"broker", "execution_engine", "execution_adapter"}
    offenders = []
    for node in ast.walk(tree):
        if (isinstance(node, ast.Attribute) and node.attr in forbidden_attrs
                and isinstance(node.value, ast.Name) and node.value.id == "root"):
            offenders.append(f"root.{node.attr}")
    assert offenders == [], f"forbidden attribute access found: {offenders}"


# --------------------------------------------------------------------- #
# Wrong-mode rejection
# --------------------------------------------------------------------- #

def test_rejects_composition_root_built_under_a_different_mode():
    config = RuntimeConfig(mode=RUNTIME_MODE_SHADOW, broker_name="paper")
    root = build_composition_root(config)
    with pytest.raises(WrongRuntimeModeError):
        run_d0_rehearsal_mode(root, _all_allow_input(), clock=_clock())


# --------------------------------------------------------------------- #
# End-to-end: real CompositionRoot, real delegation
# --------------------------------------------------------------------- #

def test_real_composition_root_under_d0_mode_delegates_correctly():
    config = RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="paper")
    root = build_composition_root(config)
    assert root.config.mode == RUNTIME_MODE_D0_REHEARSAL

    result = run_d0_rehearsal_mode(root, _all_allow_input(), clock=_clock())
    assert result.verdict.decision == "ALLOW"


def test_real_composition_root_under_d0_mode_reflects_veto_correctly():
    config = RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="paper")
    root = build_composition_root(config)

    vetoed_input = D0RehearsalInput(
        group_state=PositionGroupState(position_group_id="PG-2", lifecycle_state=LIFECYCLE_OPEN),
        defined_risk=DefinedRiskAssessment(
            position_group_id="PG-2", decision="ALLOW", blocking_reason=None,
            max_loss=1000.0, formula_used="X", evaluated_at=_clock()(),
        ),
        portfolio_limits=PortfolioLimitAssessment(
            decision="ALLOW", blocking_reason=None, active_position_count=1,
            concentration_by_underlying={}, evaluated_at=_clock()(),
        ),
        capital_check=CapitalCheckAssessment(
            decision="VETO", blocking_reason="MARGIN_NOT_CERTIFIED", evaluated_at=_clock()(),
        ),
    )
    result = run_d0_rehearsal_mode(root, vetoed_input, clock=_clock())
    assert result.verdict.decision == "VETO"
    assert "CAPITAL_MARGIN_NOT_CERTIFIED" in result.verdict.failed_checks


# --------------------------------------------------------------------- #
# Audit: adversarial synthetic proofs
# --------------------------------------------------------------------- #

def test_d0_mode_can_only_ever_construct_a_paper_broker_never_a_live_one():
    """Corrected claim, after an audit false-start: `disable_live_execution`
    only ever wraps the 'fyers' branch of _build_broker (a real broker
    being neutered) -- PaperBroker is never wrapped by it because it has
    no live-order capability to disable in the first place. The real,
    correct guarantee for D0 mode is one level up: RuntimeConfig's own
    guard makes broker_name='fyers' unconstructible outside
    PRODUCTION_READY, so D0 mode can only ever produce a PaperBroker --
    confirmed here directly on the real constructed object, not assumed."""
    config = RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="paper")
    root = build_composition_root(config)
    assert type(root.broker).__name__ == "PaperBroker"
    with pytest.raises(InvalidRuntimeConfig):
        RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="fyers")


def test_rejects_a_real_production_ready_fyers_backed_root():
    """The strongest adversarial case: a fully real, PRODUCTION_READY,
    fyers-backed CompositionRoot (constructs successfully -- construction
    never touches the network) must still be unconditionally rejected by
    the D0 wiring. This is a stronger proof than rejecting a merely-
    different-mode paper-backed root."""
    fyers_config = RuntimeConfig(mode=RUNTIME_MODE_PRODUCTION_READY, broker_name="fyers")
    fyers_root = build_composition_root(fyers_config)
    with pytest.raises(WrongRuntimeModeError):
        run_d0_rehearsal_mode(fyers_root, _all_allow_input(), clock=_clock())


def test_mismatched_assessment_error_propagates_through_the_wiring(tmp_path):
    """MismatchedAssessmentError (engine.py's own audited cross-ID guard)
    must propagate cleanly through run_d0_rehearsal_mode, never swallowed
    or masked by the wiring layer."""
    from bujji.trading_brain.risk_governor.engine import MismatchedAssessmentError

    config = RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="paper")
    root = build_composition_root(config)
    mismatched = D0RehearsalInput(
        group_state=PositionGroupState(position_group_id="PG-A", lifecycle_state=LIFECYCLE_OPEN),
        defined_risk=DefinedRiskAssessment(
            position_group_id="PG-B", decision="ALLOW", blocking_reason=None,
            max_loss=1.0, formula_used="X", evaluated_at=_clock()(),
        ),
        portfolio_limits=PortfolioLimitAssessment(
            decision="ALLOW", blocking_reason=None, active_position_count=1,
            concentration_by_underlying={}, evaluated_at=_clock()(),
        ),
        capital_check=CapitalCheckAssessment(decision="ALLOW", blocking_reason=None, evaluated_at=_clock()()),
    )
    with pytest.raises(MismatchedAssessmentError):
        run_d0_rehearsal_mode(root, mismatched, clock=_clock())


def test_two_separately_built_d0_roots_are_fully_independent():
    """No shared/singleton broker state across separately-constructed
    CompositionRoots under D0 mode."""
    config = RuntimeConfig(mode=RUNTIME_MODE_D0_REHEARSAL, broker_name="paper")
    root1 = build_composition_root(config)
    root2 = build_composition_root(config)
    assert root1.broker is not root2.broker


def test_read_only_mode_root_also_rejected_not_just_shadow():
    """Regression breadth: the wiring must reject EVERY non-D0 mode, not
    just the one (SHADOW) already covered elsewhere."""
    from bujji.production_runtime.config import RUNTIME_MODE_READ_ONLY

    config = RuntimeConfig(mode=RUNTIME_MODE_READ_ONLY, broker_name="paper")
    root = build_composition_root(config)
    with pytest.raises(WrongRuntimeModeError):
        run_d0_rehearsal_mode(root, _all_allow_input(), clock=_clock())
