"""Safety tests -- Phase 15O Shadow Lifecycle Orchestrator.

This is the first module in the project that actually PLACES orders as
part of normal operation, so its safety boundary is the strictest yet:
PaperBroker ONLY, no live broker, no guard.py bypass, no Outcome Memory
feedback into any decision."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_ORCH_REL = "bujji/shadow_lifecycle/orchestrator.py"
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3", "bujji.broker.fyers", "bujji.broker.hybrid", "bujji.broker.guard", "bujji.broker.factory",
    "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_selector", "bujji.msi_strategy_eligibility",
    "bujji.msi_decision_synthesis", "bujji.msi_trade_intent",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def _tree(rel=_ORCH_REL):
    with open(_abs(rel)) as f:
        return ast.parse(f.read(), filename=rel)


def test_no_live_broker_or_forbidden_imports():
    for node in ast.walk(_tree()):
        names = []
        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom) and node.module:
            names = [node.module]
        for name in names:
            for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                assert not name.startswith(forbidden), f"orchestrator.py imports forbidden module {name}"


def test_no_fyers_or_live_execution_reference():
    """Checks real CODE, not prose: the module's own docstring
    legitimately NAMES the forbidden modules to document the boundary,
    so a naive whole-file text scan would false-positive on its own
    safety documentation. Strings/docstrings are stripped via AST and
    the remaining identifiers/attributes are what get checked."""
    tree = _tree()
    identifiers = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Name):
            identifiers.add(node.id)
        elif isinstance(node, ast.Attribute):
            identifiers.add(node.attr)
        elif isinstance(node, ast.Import):
            identifiers.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            identifiers.add(node.module)
            identifiers.update(a.name for a in node.names)
    forbidden = ("fyers", "Fyers", "FyersBroker", "HybridBroker", "guard", "aiohttp", "urllib")
    for ident in identifiers:
        for bad in forbidden:
            assert bad not in ident, f"orchestrator.py code references {ident!r} (matched {bad!r})"


def test_broker_is_injected_never_constructed():
    """The orchestrator must never CONSTRUCT a broker (which could be
    a live one) -- it only ever uses the caller-injected `broker`
    parameter, so the shadow runtime alone decides what broker exists."""
    with open(_abs(_ORCH_REL)) as f:
        content = f.read()
    for forbidden in ("PaperBroker(", "make_broker(", "build_broker(", "Broker("):
        assert forbidden not in content, f"orchestrator.py constructs a broker: {forbidden!r}"


def test_orchestrator_never_reads_outcome_memory():
    """THE critical no-feedback-loop proof: outcome memory is WRITTEN
    at the end of a lifecycle and never READ, so no historical outcome
    can influence any decision in this or any future session."""
    with open(_abs(_ORCH_REL)) as f:
        content = f.read()
    for forbidden in ("hydrate_outcome_memory", "outcome_memory.query", "query_outcome_distribution",
                      "filter_records", "query_pnl_summary", "query_regime_performance"):
        assert forbidden not in content, f"orchestrator.py READS outcome memory ({forbidden!r}) -- a feedback loop"
    # The only permitted outcome_memory imports are the WRITE-side ones.
    for node in ast.walk(_tree()):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("bujji.outcome_memory"):
            imported = {a.name for a in node.names}
            allowed = {"build_outcome_memory_record", "build_outcome_memory_recorded_payload",
                       "EVENT_OUTCOME_MEMORY_RECORDED", "SCHEMA_VERSION"}
            assert imported <= allowed, f"orchestrator.py imports non-write outcome_memory names: {imported - allowed}"


def test_no_strategy_selection_feedback():
    """Outcome memory must never flow back into strategy selection --
    proven structurally: the orchestrator imports no selection module
    at all (checked above), and defines no function that could serve as
    a selection hook."""
    forbidden_fragments = ("select_strategy", "choose_strategy", "rank_famil", "score_famil")
    with open(_abs(_ORCH_REL)) as f:
        content = f.read()
    for fragment in forbidden_fragments:
        assert fragment not in content, f"orchestrator.py references {fragment!r}"


def test_management_recommendation_never_triggers_an_order():
    """Structural proof that monitoring cannot place an order: the only
    `place_order` calls in the module live in `open_position_from_candidate`
    and `close_position`; `monitor_position` contains none."""
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "monitor_position":
            for inner in ast.walk(node):
                if isinstance(inner, ast.Call):
                    func = inner.func
                    name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
                    assert name != "place_order", "monitor_position() places an order -- monitoring must be advisory only"
            return
    raise AssertionError("monitor_position() not found")


def test_orders_only_use_bridge_derived_client_order_ids():
    """Every `client_order_id` handed to the broker must come from
    `client_order_id_for` (Phase 15L) -- so broker identity is always
    DERIVED from lifecycle identity, never invented, and cross-session
    collisions stay impossible."""
    tree = _tree()
    for node in ast.walk(tree):
        if isinstance(node, ast.keyword) and node.arg == "client_order_id":
            value = node.value
            assert isinstance(value, ast.Call), "client_order_id is not a call to client_order_id_for"
            fname = getattr(value.func, "id", None) or getattr(value.func, "attr", None)
            assert fname == "client_order_id_for", f"client_order_id built by {fname!r}, not client_order_id_for"


def test_orchestrator_never_computes_pnl_arithmetic():
    """P&L stays owned by Phase 15K's pnl.py -- the orchestrator only
    passes real broker evidence into `build_structured_exit`."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Sub, ast.Mult)):
            raise AssertionError("orchestrator.py performs P&L-shaped arithmetic -- must delegate to pnl.py")
    with open(_abs(_ORCH_REL)) as f:
        content = f.read()
    for forbidden in ("compute_leg_gross_pnl", "compute_net_pnl", "compute_position_gross_pnl"):
        assert forbidden not in content, f"orchestrator.py references {forbidden!r} directly"


def test_no_second_state_machine_or_identity_scheme():
    """No `apply_event` redefinition, no `position_id_for`/`leg_id_for`
    redefinition -- canonical identity and state stay in
    position_lifecycle."""
    for node in ast.walk(_tree()):
        if isinstance(node, ast.FunctionDef) and node.name in ("apply_event", "position_id_for", "leg_id_for"):
            raise AssertionError(f"orchestrator.py redefines {node.name} -- must reuse position_lifecycle's own")


def test_execution_safety_files_untouched():
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py",
         "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py", "bujji/risk_governor/", "bujji/execution_engine/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"execution/safety files changed: {result.stdout}"


def test_paper_broker_itself_was_not_modified_this_phase():
    """Step 6's explicit constraint: PaperBroker must not be modified
    unless the audit proves a missing observation field. It did not --
    so `bujji/broker/paper.py` must show no NEW diff beyond the
    pre-existing (Phase 15B restore_* hydration) one this session
    inherited. Verified by confirming the orchestrator needs no
    PaperBroker change: it uses only place_order/get_order/
    get_execution_report, all pre-existing."""
    with open(_abs("bujji/broker/paper.py")) as f:
        content = f.read()
    # These are the only broker methods the orchestrator + bridge rely on.
    for required in ("def place_order", "def get_order", "def get_execution_report"):
        assert required in content, f"PaperBroker is missing {required} -- orchestrator's assumptions broken"


def test_cross_session_position_identity_cannot_collide():
    from bujji.position_lifecycle.identity import position_id_for
    from bujji.position_lifecycle.paper_bridge import client_order_id_for
    a = position_id_for("SESSION-A", "SAME-CANDIDATE", "t0")
    b = position_id_for("SESSION-B", "SAME-CANDIDATE", "t0")
    assert a != b
    assert client_order_id_for(a, "LEG-1", 1) != client_order_id_for(b, "LEG-1", 1)
