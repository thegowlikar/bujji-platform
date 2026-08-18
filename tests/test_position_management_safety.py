"""Safety tests -- Phase 15I Adaptive Position Management.

Confirms: no broker imports, no place_order/modify_order/cancel_order,
no PaperBroker activation, no live execution, no mutation of canonical
position state merely because a recommendation was generated,
recommendations cannot masquerade as executed actions."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "fyers_apiv3",
)
_CHECKED_FILES = ("bujji/position_management/models.py", "bujji/position_management/engine.py")
_LIFECYCLE_MGMT_FILES = ("bujji/position_lifecycle/models.py", "bujji/position_lifecycle/engine.py")


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def test_no_forbidden_imports():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel} imports forbidden module {name}"


def test_no_forbidden_order_calls_ast_based():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_no_broker_paperbroker_or_capital_reference():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in (".connect(", "fyers", "Fyers", "aiohttp", "requests.", "urllib",
                          "PaperBroker(", "capital_brain", "risk_governor", "margin"):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_recommendation_never_mutates_canonical_status():
    """Real regression proof: recording a MANAGEMENT_ASSESSED event
    (even a strong EXIT recommendation) must never change
    PositionLifecycle.status or its legs."""
    from bujji.position_lifecycle.engine import (
        apply_event, build_entry_snapshot_for_position, build_management_assessed_payload,
        build_position_opened_payload,
    )
    from bujji.position_lifecycle.models import STATUS_OPEN
    from bujji.position_management.engine import assess_position_management

    class _Leg:
        role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
        side, ratio, entry_mid, delta = "BUY", 1, 130.0, 0.5

    class _Candidate:
        candidate_id, source_cycle_id, strategy_family = "STC-X", "t0", "LONG_DIRECTIONAL"
        timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
        selection_confidence, underlying_price, legs = "HIGH", 24450.0, (_Leg(),)

    class _FakeEval:
        thesis_status, evidence_confidence = "THESIS_INVALIDATED", "HIGH"

    candidate = _Candidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, r1 = apply_event({}, "S1", "POSITION_OPENED", "S1", open_payload)
    pid = r1.position_id
    legs_before = states[pid].legs

    assessment = assess_position_management(pid, "c1", _FakeEval(), None, None)
    assert assessment.recommendation == "EXIT"
    payload = build_management_assessed_payload(pid, "c1", assessment)
    states, r2 = apply_event(states, "S1", "MANAGEMENT_ASSESSED", "S1", payload)

    assert states[pid].status == STATUS_OPEN  # a strong EXIT recommendation never closed the position.
    assert states[pid].legs == legs_before


def test_recommendation_field_is_advisory_text_only():
    """The recommendation is a plain string label -- confirm no code
    path in the engine calls anything beyond returning a dataclass."""
    with open(_abs("bujji/position_management/engine.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in ("execute", "submit", "send", "post", "write", "adjust", "hedge", "roll", "close"), \
                f"unexpected side-effecting-looking call: {name}"


def test_no_management_event_type_implies_action_taken():
    """EVENT_MANAGEMENT_ASSESSED must be the only new event type this
    phase added -- confirm no ADJUSTED/HEDGED/ROLLED event CONSTANT is
    actually DEFINED (a docstring merely discussing why they don't
    exist yet is not a violation of that -- checked via AST assignment
    targets, not a raw text search)."""
    with open(_abs("bujji/position_lifecycle/models.py")) as f:
        tree = ast.parse(f.read())
    defined_names = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    defined_names.add(target.id)
    for forbidden in ("EVENT_POSITION_ADJUSTED", "EVENT_POSITION_HEDGED", "EVENT_POSITION_ROLLED"):
        assert forbidden not in defined_names, f"an action-implying event type was defined prematurely: {forbidden}"


def test_no_capital_or_risk_packages_touched():
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py", "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py", "bujji/risk_governor/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected changes: {result.stdout}"
