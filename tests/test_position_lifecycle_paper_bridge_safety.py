"""Safety tests -- Phase 15L PaperBroker Lifecycle Bridge.

Confirms: no FYERS write calls, no live broker activation, no order
placement/modify/cancel path from THIS module, no strategy-selection
feedback, no TradeIntent mutation, no risk-governor bypass, no
duplicated P&L arithmetic, no alternate lifecycle identity, no
automatic management execution."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_BRIDGE_REL = "bujji/position_lifecycle/paper_bridge.py"
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3", "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_eligibility",
    "bujji.msi_trade_intent", "bujji.msi_decision_synthesis", "bujji.position_management",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def _tree():
    with open(_abs(_BRIDGE_REL)) as f:
        return ast.parse(f.read(), filename=_BRIDGE_REL)


def test_no_forbidden_imports():
    for node in ast.walk(_tree()):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                assert not name.startswith(forbidden), f"paper_bridge.py imports forbidden module {name}"


def test_no_forbidden_order_calls_ast_based():
    """The bridge must never CALL place_order/modify_order/cancel_order
    itself -- it only READS broker state via get_order/get_execution_report.
    (Test fixtures that use place_order to SET UP realistic broker state
    live in the test files, never in paper_bridge.py itself.)"""
    for node in ast.walk(_tree()):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
        assert name not in _FORBIDDEN_CALL_NAMES, f"paper_bridge.py calls forbidden method {name}()"


def test_bridge_only_calls_read_only_broker_methods():
    """Explicit allow-list check: every `broker.<attr>` access in the
    bridge must be one of the documented read-only methods -- proves
    the module never gains a new broker-write surface as it evolves."""
    allowed_broker_methods = {"get_order", "get_execution_report"}
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "broker":
            assert node.attr in allowed_broker_methods, f"paper_bridge.py calls broker.{node.attr}() -- not on the read-only allow-list"


def test_bridge_never_reads_symbol_scoped_ledger():
    """Forensic finding (Section 6 of the report): `get_realized_pnl()`
    and `get_open_positions()` are symbol-netted and therefore AMBIGUOUS
    across concurrent lifecycle positions sharing a symbol -- the bridge
    must never call them."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "broker":
            assert node.attr not in ("get_realized_pnl", "get_open_positions"), (
                f"paper_bridge.py calls broker.{node.attr}() -- symbol-ambiguous, must not be used"
            )


def test_no_broker_or_network_activation_reference():
    with open(_abs(_BRIDGE_REL)) as f:
        content = f.read()
    for forbidden in ("fyers", "Fyers", "aiohttp", "requests.", "urllib", ".connect("):
        assert forbidden not in content, f"paper_bridge.py unexpectedly references {forbidden!r}"


def test_bridge_never_computes_pnl_arithmetic():
    """Canonical ownership: PaperBroker -> fill facts, pnl.py ->
    economic calculation, bridge -> translation only. P&L math's
    unambiguous signature is subtracting two prices (entry vs exit) --
    the bridge legitimately MULTIPLIES fill price by quantity for VWAP
    aggregation (not P&L), but must never SUBTRACT two price-shaped
    values (that would be computing a delta/profit itself, competing
    with pnl.py). No `ast.Sub` at all appears in this module."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            raise AssertionError("paper_bridge.py performs subtraction -- P&L delta arithmetic belongs exclusively in pnl.py")
    with open(_abs(_BRIDGE_REL)) as f:
        content = f.read()
    for forbidden in ("entry_premium", "gross_leg_pnl", "net_realized_pnl", "compute_leg_gross_pnl", "compute_net_pnl"):
        assert forbidden not in content, f"paper_bridge.py references {forbidden!r} -- P&L stays exclusively in pnl.py/engine.py"


def test_client_order_id_never_becomes_canonical_identity():
    """The bridge derives client_order_id FROM position_id/leg_id, never
    the reverse -- proven by construction: feeding two DIFFERENT
    position_ids for the SAME leg shape produces different ids, and the
    function's only inputs are lifecycle identity, never a broker value."""
    from bujji.position_lifecycle.paper_bridge import client_order_id_for
    a = client_order_id_for("POS-A", "LEG-1", 1)
    b = client_order_id_for("POS-B", "LEG-1", 1)
    assert a != b
    assert a == client_order_id_for("POS-A", "LEG-1", 1)  # deterministic, not broker-assigned.


def test_no_automatic_management_execution_path():
    """Reconciling a paper fill NEVER triggers a management
    recommendation or a new order -- `reconcile_position_exit` returns
    a plain, inert dataclass; nothing in this module calls
    `bujji.position_management` or issues any broker instruction."""
    with open(_abs(_BRIDGE_REL)) as f:
        content = f.read()
    for forbidden in ("position_management", "assess_position_management", "TradeIntent"):
        assert forbidden not in content, f"paper_bridge.py unexpectedly references {forbidden!r}"


def test_no_capital_or_execution_packages_touched():
    """`bujji/broker/fyers.py` is deliberately excluded here -- its
    existing diff (a read-only `get_futures_quote`/`_futures_symbol`
    addition, no order-placement change) predates this session and is
    unrelated to Phase 15L; verified by direct inspection, not modified
    by this phase's own work."""
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py",
         "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",
         # M4 (2026-08-23): a NARROW, authorised extension -- the
         # SESSION_TRANSITION event type, so session lifecycle lives in the
         # same durable journal as position lifecycle rather than a second
         # store. The authorisation is on the CHANGE, not the file:
         # tests/test_frozen_vocabulary_extension.py asserts every added line
         # belongs to that block and that nothing was removed, so an
         # unrelated edit to this same file still fails the build.
         ":(exclude)bujji/trading_brain/risk_governor/position_group_validation.py", "bujji/risk_governor/", "bujji/execution_engine/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected changes: {result.stdout}"


def test_position_lifecycle_identity_module_untouched_by_bridge():
    """The bridge must not redefine or shadow position_id_for/leg_id_for
    -- it only IMPORTS/uses them, canonical identity stays in identity.py."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name in ("position_id_for", "leg_id_for"):
            raise AssertionError("paper_bridge.py redefines canonical identity functions -- must only import them")
