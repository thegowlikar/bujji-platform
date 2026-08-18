"""Safety tests -- Position Intelligence, Phase 15 gap #1.

Confirms this layer is structurally incapable of acting: no broker
import, no execution term, no HOLD/EXIT/ADJUST enforcement anywhere --
`recommendation` is a plain string field, never a call."""
from __future__ import annotations

import ast
import os

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_PACKAGE = "bujji/position_intelligence"

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "bujji.broker", "fyers_apiv3",
)
_FORBIDDEN_TERMS = ("place_order", "modify_order", "cancel_order", "connect(")


def _all_files():
    abs_path = os.path.join(_REPO_ROOT, _PACKAGE)
    return [os.path.join(abs_path, n) for n in os.listdir(abs_path) if n.endswith(".py")]


def test_no_forbidden_imports():
    for path in _all_files():
        with open(path) as f:
            tree = ast.parse(f.read(), filename=path)
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            for name in names:
                for forbidden in _FORBIDDEN_IMPORT_PREFIXES:
                    assert not name.startswith(forbidden), f"{path} imports forbidden module {name}"


def test_no_forbidden_terms():
    for path in _all_files():
        with open(path) as f:
            content = f.read()
        for term in _FORBIDDEN_TERMS:
            assert term not in content, f"{path} contains forbidden term {term!r}"


def test_never_imports_broker_at_all():
    for path in _all_files():
        with open(path) as f:
            content = f.read()
        assert "bujji.broker" not in content


def test_recommendation_field_is_plain_string_not_a_callable():
    from bujji.position_intelligence.engine import evaluate_thesis
    from bujji.position_intelligence.models import PositionEntrySnapshot
    entry = PositionEntrySnapshot("T", "LONG_DIRECTIONAL", "t0", "RANGING", "BULLISH", None, None, None)
    result = evaluate_thesis(entry, {"market_direction": {"overall_direction": "BULLISH"}})
    assert isinstance(result.recommendation, str)
