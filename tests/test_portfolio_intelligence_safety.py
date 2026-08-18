"""Safety tests -- Phase 15M Portfolio Intelligence Layer.

Confirms: READ-ONLY / ANALYTICAL only. No order placement/modify/
cancel, no FYERS calls, no broker execution methods, no strategy-
selection/TradeIntent/risk-decision mutation, no same-session feedback,
no management-action creation, no duplicated P&L arithmetic, no second
position state machine."""
from __future__ import annotations

import ast
import os
import subprocess

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_MODULE_RELS = ("bujji/portfolio_intelligence/models.py", "bujji/portfolio_intelligence/engine.py")
_FORBIDDEN_CALL_NAMES = ("place_order", "modify_order", "cancel_order")
_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain", "bujji.capital_brain",
    "fyers_apiv3", "bujji.msi_strategy_selection_foundation", "bujji.msi_strategy_eligibility",
    "bujji.msi_trade_intent", "bujji.msi_decision_synthesis", "bujji.position_management",
    "bujji.broker",
)


def _abs(rel):
    return os.path.join(_REPO_ROOT, rel)


def _tree(rel):
    with open(_abs(rel)) as f:
        return ast.parse(f.read(), filename=rel)


def test_no_forbidden_imports():
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{rel} imports forbidden module {name}"


def test_no_forbidden_order_calls():
    for rel in _MODULE_RELS:
        for node in ast.walk(_tree(rel)):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            assert name not in _FORBIDDEN_CALL_NAMES, f"{rel} calls forbidden method {name}()"


def test_no_broker_or_network_reference():
    for rel in _MODULE_RELS:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("fyers", "Fyers", "aiohttp", "requests.", "urllib", ".connect(", "PaperBroker("):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_engine_never_computes_position_level_pnl_arithmetic():
    """`_build_pnl` only SUMS already-computed `structured_exit`
    numbers (Phase 15K's own output) -- it must never subtract two
    price-shaped values itself (that would be re-deriving P&L, not
    aggregating it)."""
    for node in ast.walk(_tree("bujji/portfolio_intelligence/engine.py")):
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Sub):
            raise AssertionError("portfolio_intelligence/engine.py performs subtraction -- P&L delta arithmetic belongs exclusively in pnl.py")
    with open(_abs("bujji/portfolio_intelligence/engine.py")) as f:
        content = f.read()
    for forbidden in ("compute_leg_gross_pnl", "compute_net_pnl", "compute_position_gross_pnl"):
        assert forbidden not in content, f"engine.py references {forbidden!r} -- P&L stays exclusively in pnl.py"


def test_engine_never_calls_event_store_directly():
    """Portfolio Intelligence consumes an ALREADY-HYDRATED
    `Dict[position_id, PositionLifecycle]` -- it must never read
    `EventStore` itself (that would risk a second, competing replay
    path diverging from `hydrate_position_lifecycles`)."""
    with open(_abs("bujji/portfolio_intelligence/engine.py")) as f:
        content = f.read()
    for forbidden in ("EventStore(", "read_events", "PersistedEvent("):
        assert forbidden not in content, f"engine.py references {forbidden!r} -- must consume already-hydrated state only"


def test_engine_never_defines_apply_event_or_new_status_constants():
    """No second position state machine (Step 3's own explicit
    instruction) -- the module must not define its own `apply_event`,
    `STATUS_OPEN`, or `STATUS_CLOSED`."""
    tree = _tree("bujji/portfolio_intelligence/models.py")
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "apply_event":
            raise AssertionError("portfolio_intelligence/models.py defines apply_event -- a second state machine")
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in ("STATUS_OPEN", "STATUS_CLOSED"):
                    raise AssertionError(f"portfolio_intelligence/models.py redefines lifecycle status constant {target.id}")


def test_capital_overlay_never_fabricated_by_default():
    """`CapitalOverlay()` with no arguments must default to
    NOT_SUPPLIED/None -- proving the module never invents equity/margin
    figures when the caller doesn't provide a real broker read."""
    from bujji.portfolio_intelligence.models import CapitalOverlay
    overlay = CapitalOverlay()
    assert overlay.account_equity is None
    assert overlay.available_margin is None
    assert overlay.source == "NOT_SUPPLIED"


def test_no_management_action_creation():
    """The module must never reference position_management's own
    action-taking vocabulary as something IT creates -- only aggregates
    `latest_management_recommendation`, an already-recorded field."""
    with open(_abs("bujji/portfolio_intelligence/engine.py")) as f:
        content = f.read()
    for forbidden in ("assess_position_management", "build_management_assessed_payload", "TradeIntent"):
        assert forbidden not in content, f"engine.py unexpectedly references {forbidden!r}"


def test_no_trading_brain_or_execution_packages_touched():
    result = subprocess.run(
        ["git", "diff", "--stat", "b148e39", "--",
         "bujji/broker/guard.py", "bujji/broker/hybrid.py",
         "bujji/trading_brain/", ":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py", "bujji/risk_governor/", "bujji/execution_engine/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected changes: {result.stdout}"


def test_conflicts_never_produce_a_trading_action():
    """Step 5's own explicit instruction: 'Do not create trading
    actions yet.' Every conflict finding's `finding` value must be one
    of the documented, purely-descriptive vocabulary -- never an
    action verb like HEDGE/ROLL/CLOSE/ADJUST."""
    from bujji.portfolio_intelligence.models import ALL_CONFLICT_FINDINGS
    forbidden_action_words = ("HEDGE", "ROLL", "CLOSE", "ADJUST", "EXIT", "EXECUTE")
    for finding in ALL_CONFLICT_FINDINGS:
        for word in forbidden_action_words:
            assert word not in finding, f"conflict finding {finding!r} looks like a trading action, not a description"
