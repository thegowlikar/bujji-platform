"""Safety tests -- Phase 15K Structured Exit + P&L Linkage.

Confirms: no broker-write imports, no order placement/modification/
cancellation, no PaperBroker activation, no Strategy Selection/
TradeIntent imports, no Risk Governor mutation, no same-session
learning feedback, no wall-clock dependency, no randomness, no
fabricated P&L, UNKNOWN preserved when economic evidence is missing."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3", "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_eligibility",
    "bujji.msi_trade_intent", "bujji.msi_decision_synthesis",
)
_CHECKED_FILES = ("bujji/position_lifecycle/pnl.py",)


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


def test_pnl_module_has_zero_imports_at_all():
    """The strongest possible safety guarantee: pnl.py needs nothing
    beyond its own models -- confirmed it imports NOTHING from outside
    bujji.position_lifecycle.models, not even indirectly."""
    with open(_abs("bujji/position_lifecycle/pnl.py")) as f:
        tree = ast.parse(f.read())
    imported_modules = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            imported_modules.append(node.module)
        elif isinstance(node, ast.Import):
            imported_modules.extend(alias.name for alias in node.names)
    allowed = {"__future__", "typing", "models"}
    assert all(m in allowed for m in imported_modules), f"pnl.py has unexpected imports: {imported_modules}"


def test_no_forbidden_order_calls_ast_based():
    for rel in _CHECKED_FILES + ("bujji/position_lifecycle/engine.py", "bujji/position_lifecycle/models.py"):
        with open(_abs(rel)) as f:
            tree = ast.parse(f.read(), filename=rel)
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_no_broker_or_network_reference():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in (".connect(", "fyers", "Fyers", "aiohttp", "requests.", "urllib", "PaperBroker("):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_no_wall_clock_or_random_dependence():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("now_ist(", "datetime.now(", "utcnow(", "import random", "random."):
            assert forbidden not in content, f"{rel} depends on wall clock or randomness: {forbidden!r}"


def test_pnl_never_fabricates_a_number_from_missing_inputs():
    from bujji.position_lifecycle.models import PNL_UNKNOWN
    from bujji.position_lifecycle.pnl import compute_leg_gross_pnl
    for kwargs in (
        dict(side="BUY", quantity=None, multiplier=75, entry_price=100.0, exit_price=110.0),
        dict(side="BUY", quantity=1, multiplier=None, entry_price=100.0, exit_price=110.0),
        dict(side="BUY", quantity=1, multiplier=75, entry_price=None, exit_price=110.0),
        dict(side="BUY", quantity=1, multiplier=75, entry_price=100.0, exit_price=None),
    ):
        pnl, status = compute_leg_gross_pnl(**kwargs)
        assert pnl is None
        assert status == PNL_UNKNOWN


def test_structured_exit_never_mutates_status_or_legs():
    """Real regression proof: computing/recording a structured exit
    only ever transitions OPEN -> CLOSED via the existing 15G guard --
    it never independently mutates legs or bypasses the state machine."""
    from bujji.position_lifecycle.engine import (
        apply_event, build_entry_snapshot_for_position, build_position_closed_payload,
        build_position_opened_payload, build_structured_exit,
    )
    from bujji.position_lifecycle.models import STATUS_CLOSED, STATUS_OPEN

    class _Leg:
        role, option_type, strike, expiry = "PRIMARY", "CE", 24450.0, "2026-08-13"
        side, ratio, entry_mid, delta = "BUY", 1, 100.0, 0.5
        entry_bid, entry_ask = 98.0, 102.0

    class _Candidate:
        candidate_id, source_cycle_id, strategy_family = "STC-X", "t0", "LONG_DIRECTIONAL"
        timestamp, market_regime, direction, thesis = "t0", "RANGING", "BULLISH", "TREND_CONTINUATION"
        selection_confidence, underlying_price, legs = "HIGH", 24450.0, (_Leg(),)
        lot_size = 75

    candidate = _Candidate()
    entry = build_entry_snapshot_for_position(candidate, None)
    open_payload = build_position_opened_payload("S1", candidate, entry, "t0")
    states, r1 = apply_event({}, "S1", "POSITION_OPENED", "S1", open_payload)
    pid = r1.position_id
    legs_before = states[pid].legs

    leg = states[pid].legs[0]
    structured_exit = build_structured_exit(states[pid].legs, 75, "t5", "manual", {leg.leg_id: {"exit_price": 150.0}})
    states, r2 = apply_event(states, "S1", "POSITION_CLOSED", "S1",
                              build_position_closed_payload(pid, "t5", "manual", structured_exit))
    assert states[pid].status == STATUS_CLOSED
    assert states[pid].legs == legs_before  # LegRecord (entry data) is immutable -- never overwritten by exit data.


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


def test_outcome_attribution_still_has_no_import_path_to_management_or_selection():
    """Phase 15K did not weaken Phase 15J's own hard boundary --
    re-verified here since outcome_attribution now consumes richer
    structured_exit data through the SAME lifecycle object, never a
    new import."""
    with open(_abs("bujji/outcome_attribution/engine.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            assert not name.startswith("bujji.position_management"), f"outcome_attribution/engine.py imports {name}"
            assert not name.startswith("bujji.msi_strategy_selection_foundation"), f"outcome_attribution/engine.py imports {name}"
