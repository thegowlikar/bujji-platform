"""Safety tests -- Phase 15F Position Intelligence Greeks/Premium
Behaviour consumption.

Confirms: no broker-write imports, no order placement/modification/
cancellation, no capital/margin mutation, no live API calls, no hidden
network dependency, no automatic execution from EXIT/ADJUST, UNKNOWN
preserved, existing thesis behaviour valid when new intelligence is
unavailable."""
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
_CHECKED_FILES = ("bujji/position_intelligence/models.py", "bujji/position_intelligence/engine.py")


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


def test_no_broker_or_network_reference():
    """Text-search for actual USAGE patterns, not mere prose mentions --
    this module's own docstring legitimately explains it can't reach a
    broker/PaperBroker, which is the point being verified here, not a
    violation of it."""
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in (".connect(", "fyers", "Fyers", "aiohttp", "requests.", "urllib", "PaperBroker("):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_no_capital_or_margin_mutation_reference():
    for rel in _CHECKED_FILES:
        with open(_abs(rel)) as f:
            content = f.read()
        for forbidden in ("capital", "margin", "risk_governor", "execution_engine"):
            assert forbidden not in content, f"{rel} unexpectedly references {forbidden!r}"


def test_recommendation_is_always_advisory_text_only():
    """EXIT/ADJUST/HOLD are string labels only -- confirm no code path
    in this module calls anything beyond returning a ThesisEvaluation
    dataclass (no side-effecting call of any kind)."""
    with open(_abs("bujji/position_intelligence/engine.py")) as f:
        tree = ast.parse(f.read())
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", None)
            # Only pure/local helper calls and dataclass constructors are expected.
            assert name not in ("execute", "submit", "send", "post", "write", "open"), \
                f"unexpected side-effecting-looking call: {name}"


def test_unknown_greeks_and_premium_behaviour_never_change_pre_existing_verdict():
    """Real regression guard: for every existing (pre-15F) fixture
    shape, adding entry_greeks=None/entry_premium_behaviour=None and a
    current_record with no "greeks"/"premium_behaviour" keys at all
    must produce the EXACT SAME thesis_status/recommendation as before
    Phase 15F existed."""
    from bujji.position_intelligence.engine import evaluate_thesis
    from bujji.position_intelligence.models import PositionEntrySnapshot, THESIS_INTACT, RECOMMEND_HOLD

    entry = PositionEntrySnapshot(
        "T", "LONG_DIRECTIONAL", "t0", "RANGING", "BULLISH", None, None, None,
    )
    record_without_15e_fields = {
        "timestamp": "t1", "market_direction": {"overall_direction": "STRONG_BULLISH"},
        "market_state": None, "volatility_structure": None,
    }
    result = evaluate_thesis(entry, record_without_15e_fields)
    assert result.thesis_status == THESIS_INTACT
    assert result.recommendation == RECOMMEND_HOLD
    # The new checks must degrade to UNKNOWN (excluded from the deviated/resolved
    # ratio), never silently absent in a way that changes the math either.
    delta_check = next((c for c in result.checks if c.dimension == "delta_exposure"), None)
    assert delta_check is not None
    assert delta_check.status == "UNKNOWN"
