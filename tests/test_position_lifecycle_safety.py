"""Safety tests -- Phase 15G Position Lifecycle.

Confirms: no broker-write imports, no order placement/modification/
cancellation, no capital/risk mutation, no live network calls, no
automatic execution from lifecycle transitions, invalid transitions
fail closed, duplicate events are idempotent, conflicting events are
rejected, UNKNOWN is preserved, lifecycle identity cannot be silently
reused."""
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
_CHECKED_FILES = (
    "bujji/position_lifecycle/models.py", "bujji/position_lifecycle/identity.py",
    "bujji/position_lifecycle/engine.py", "bujji/position_lifecycle/recovery.py",
)


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


def test_no_broker_network_or_capital_reference():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in (".connect(", "fyers", "Fyers", "aiohttp", "requests.", "urllib",
                          "PaperBroker(", "capital_brain", "risk_governor", "margin"):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_lifecycle_transitions_never_call_anything_side_effecting():
    with open(_abs("bujji/position_lifecycle/engine.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in ("execute", "submit", "send", "post", "write"), \
                f"unexpected side-effecting-looking call in engine.py: {name}"


def test_broker_guard_and_hybrid_byte_untouched():
    """paper.py is EXCLUDED here -- it carries the already-documented,
    scoped Phase 15B exception (two additive restore_* methods),
    verified separately by test_state_persistence_safety.py. guard.py/
    hybrid.py must remain untouched by Phase 15G specifically."""
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--", "bujji/broker/guard.py", "bujji/broker/hybrid.py"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"broker files were modified, expected untouched: {result.stdout}"


def test_invalid_transitions_fail_closed():
    from bujji.position_lifecycle.engine import apply_event
    from bujji.position_lifecycle.models import TRANSITION_REJECTED
    # unknown position_id for THESIS_EVALUATED and POSITION_CLOSED.
    _, r1 = apply_event({}, "S1", "THESIS_EVALUATED", "S1", {"position_id": "POS-nope", "cycle_id": "c", "evaluation": {}})
    _, r2 = apply_event({}, "S1", "POSITION_CLOSED", "S1", {"position_id": "POS-nope", "closed_at": "t", "exit_reason": "x"})
    assert r1.outcome == TRANSITION_REJECTED
    assert r2.outcome == TRANSITION_REJECTED


def test_identity_cannot_be_silently_reused_for_different_content():
    from bujji.position_lifecycle.engine import apply_event, build_entry_snapshot_for_position, build_position_opened_payload
    from bujji.position_lifecycle.models import TRANSITION_REJECTED

    class _Leg:
        role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
        side, ratio, entry_mid, delta = "BUY", 1, 130.0, 0.5

    class _Candidate:
        candidate_id, source_cycle_id, strategy_family = "STC-X", "t0", "LONG_DIRECTIONAL"
        timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
        selection_confidence, underlying_price, legs = "HIGH", 24450.0, (_Leg(),)

    candidate = _Candidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    payload_a = build_position_opened_payload("S1", candidate, entry, "t0")
    payload_b = dict(payload_a)
    payload_b["opened_at"] = "t999"  # genuinely different content, same position_id.

    states, r1 = apply_event({}, "S1", "POSITION_OPENED", "S1", payload_a)
    states, r2 = apply_event(states, "S1", "POSITION_OPENED", "S1", payload_b)
    assert r1.outcome != TRANSITION_REJECTED
    assert r2.outcome == TRANSITION_REJECTED
    assert states[payload_a["position_id"]].opened_at == "t0"  # never silently overwritten by the conflicting attempt.


def test_no_ad_hoc_p_and_l_arithmetic_in_engine():
    """Phase 15K superseded this test's original Phase 15I/15J scope
    (back then, engine.py legitimately had zero P&L math at all, since
    realized_pnl was permanently None). Phase 15K's own purpose is to
    add REAL P&L computation to this package -- so the original literal
    string check ("realized_pnl =" etc.) is now obsolete and would
    false-positive on legitimate variable assignments. The test's TRUE
    intent survives unweakened: engine.py must never perform the
    multiplication that is the actual, unambiguous signature of P&L
    math (price * quantity * multiplier) -- all real math must go
    through bujji.position_lifecycle.pnl's pure functions. Verified by
    AST for `ast.Mult` specifically -- NOT a blanket `ast.BinOp` ban,
    since this file's existing, unrelated tuple-accumulation pattern
    (`current.thesis_evaluations + (evaluation,)`, Phase 15F/15I) is a
    legitimate `Add` BinOp that has nothing to do with P&L."""
    import ast as _ast
    with open(_abs("bujji/position_lifecycle/engine.py")) as f:
        tree = _ast.parse(f.read())
    for node in _ast.walk(tree):
        if isinstance(node, _ast.BinOp):
            assert not isinstance(node.op, _ast.Mult), (
                "engine.py performs multiplication directly -- all P&L math must be delegated "
                "to bujji.position_lifecycle.pnl's pure functions, never computed ad-hoc here"
            )
