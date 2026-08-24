"""Safety tests -- Phase 15J Outcome Attribution.

Confirms: no broker-write imports, no place_order/modify_order/
cancel_order, no risk-governor/strategy-selection/TradeIntent/Position
Management mutation, no same-session feedback path, no hidden state,
no wall-clock dependence, no randomness, no fabricated evidence."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3",
    # Step: HARD BOUNDARY -- outcome attribution must not import anything
    # that could ALTER a decision, only things that describe what already happened.
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_eligibility",
    "bujji.msi_trade_intent", "bujji.msi_decision_synthesis", "bujji.position_management",
)
_CHECKED_FILES = ("bujji/outcome_attribution/models.py", "bujji/outcome_attribution/engine.py")
_LIFECYCLE_FILES = ("bujji/position_lifecycle/models.py", "bujji/position_lifecycle/engine.py")


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


def test_no_wall_clock_or_random_dependence():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("now_ist(", "datetime.now(", "utcnow(", "import random", "random."):
            assert forbidden not in content, f"{rel} depends on wall clock or randomness: {forbidden!r}"


def test_engine_only_reads_lifecycle_never_calls_anything_side_effecting():
    with open(_abs("bujji/outcome_attribution/engine.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in ("execute", "submit", "send", "post", "write", "adjust", "hedge", "roll", "close", "select"), \
                f"unexpected side-effecting-looking call: {name}"


def test_attribution_never_mutates_lifecycle_status_or_legs():
    """Real regression proof: recording OUTCOME_ATTRIBUTED must never
    change status, legs, or any decision field on PositionLifecycle."""
    from bujji.outcome_attribution.engine import attribute_position_outcome
    from bujji.position_lifecycle.engine import (
        apply_event, build_entry_snapshot_for_position, build_outcome_attributed_payload,
        build_position_closed_payload, build_position_opened_payload,
    )
    from bujji.position_lifecycle.models import STATUS_CLOSED

    class _Leg:
        role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
        side, ratio, entry_mid, delta = "BUY", 1, 130.0, 0.5

    class _Candidate:
        candidate_id, source_cycle_id, strategy_family = "STC-X", "t0", "LONG_DIRECTIONAL"
        timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
        selection_confidence, underlying_price, legs = "HIGH", 24450.0, (_Leg(),)

    candidate = _Candidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, r1 = apply_event({}, "S1", "POSITION_OPENED", "S1", open_payload)
    pid = r1.position_id
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1", build_position_closed_payload(pid, "t5", "session_end"))
    legs_before, status_before = states[pid].legs, states[pid].status

    attribution = attribute_position_outcome(states[pid])
    payload = build_outcome_attributed_payload(pid, attribution)
    states, r3 = apply_event(states, "S1", "OUTCOME_ATTRIBUTED", "S1", payload)

    assert states[pid].status == status_before == STATUS_CLOSED
    assert states[pid].legs == legs_before


def test_missing_evidence_is_unknown_never_fabricated_across_all_dimensions():
    from bujji.outcome_attribution.engine import attribute_position_outcome
    from bujji.position_lifecycle.models import EntrySnapshot, PositionLifecycle, STATUS_CLOSED

    entry = EntrySnapshot(
        candidate_id="STC-x", source_cycle_id="t0", strategy_family="LONG_DIRECTIONAL", entry_timestamp="t0",
        underlying_price=None, entry_direction=None, entry_regime=None, entry_thesis=None,
        entry_confidence=None, entry_greeks=None, entry_premium_behaviour=None,
    )
    lifecycle = PositionLifecycle(
        position_id="POS-1", session_id="S1", status=STATUS_CLOSED, entry=entry, legs=(), opened_at="t0",
        closed_at="t9", exit_reason=None,
    )
    result = attribute_position_outcome(lifecycle)
    assert result.outcome_direction == "UNKNOWN"
    # No evidence item claims a resolved POSITIVE/NEGATIVE conclusion from nothing.
    for e in result.evidence:
        if e.role == "UNKNOWN":
            assert e.impact_direction == "UNKNOWN"


def test_lifecycle_diff_scoped_to_additive_content_only():
    """The cumulative diff across Phases 15G/15I/15J to this file must
    contain zero removed lines -- purely additive."""
    for rel in _LIFECYCLE_FILES:
        result = subprocess.run(
            ["git", "diff", "360c003", "--", rel],
            cwd=_REPO_ROOT, capture_output=True, text=True,
        )
        removed_code_lines = [
            l for l in result.stdout.splitlines()
            if l.startswith("-") and not l.startswith("---") and l.strip() != "-"
        ]
        assert removed_code_lines == [], f"unexpected removed lines in {rel}: {removed_code_lines}"


def test_no_capital_or_risk_packages_touched():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py", "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",
         # M4 (2026-08-23): a NARROW, authorised extension -- the
         # SESSION_TRANSITION event type, so session lifecycle lives in the
         # same durable journal as position lifecycle rather than a second
         # store. The authorisation is on the CHANGE, not the file:
         # tests/test_frozen_vocabulary_extension.py asserts every added line
         # belongs to that block and that nothing was removed, so an
         # unrelated edit to this same file still fails the build.
         ":(exclude)bujji/trading_brain/risk_governor/position_group_validation.py", "bujji/risk_governor/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected changes: {result.stdout}"
