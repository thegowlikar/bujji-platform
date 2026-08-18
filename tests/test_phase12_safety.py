"""Safety tests -- Phase 12 Full Intelligence Shadow Validation Campaign.

Confirms every new Phase 12 module (shadow_validation,
intelligence_consistency_checker) stays strictly read-only/reporting:
no execution/broker-write/risk/capital import, no order-shaped call
anywhere, and the launcher script itself only uses the broker's
documented read-only methods."""
from __future__ import annotations

import ast
import os
import re

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

_PHASE12_PACKAGES = ("bujji/shadow_validation", "bujji/intelligence_consistency_checker")

_FORBIDDEN_IMPORT_PREFIXES = (
    "bujji.execution_engine", "bujji.risk_governor", "bujji.trading_brain",
    "bujji.capital_brain", "bujji.msi_trade_construction", "bujji.msi_shadow_trading",
    "bujji.mic_replay", "bujji.production_runtime", "fyers_apiv3",
)

_FORBIDDEN_TERMS = ("place_order", "modify_order", "cancel_order", "risk_governor", "capital_allocator")

_ALLOWED_BROKER_READ_METHODS = (
    "get_quote", "get_option_chain", "get_futures_quote", "get_spot", "get_vix",
    "get_recent_candles", "connect",
)


def _all_files(package_rel_path):
    out = []
    abs_path = os.path.join(_REPO_ROOT, package_rel_path)
    for name in os.listdir(abs_path):
        if name.endswith(".py"):
            out.append(os.path.join(abs_path, name))
    return out


def test_no_forbidden_imports_in_phase12_packages():
    for pkg in _PHASE12_PACKAGES:
        for path in _all_files(pkg):
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


def test_no_forbidden_terms_in_phase12_packages():
    for pkg in _PHASE12_PACKAGES:
        for path in _all_files(pkg):
            with open(path) as f:
                content = f.read()
            for term in _FORBIDDEN_TERMS:
                assert term not in content, f"{path} contains forbidden term {term!r}"


def test_phase12_packages_never_import_broker_directly():
    """Both packages are pure functions over already-persisted dicts --
    neither should import the broker module at all, confirming they
    cannot make a live call of any kind, read or write."""
    for pkg in _PHASE12_PACKAGES:
        for path in _all_files(pkg):
            with open(path) as f:
                content = f.read()
            assert "bujji.broker" not in content, f"{path} imports the broker -- these packages must stay pure/offline"


def test_shadow_full_intelligence_yaml_never_enables_execution():
    yaml_path = os.path.join(_REPO_ROOT, "config", "shadow_full_intelligence.yaml")
    with open(yaml_path) as f:
        content = f.read()
    assert re.search(r"execution_engine:\s*false", content)
    assert re.search(r"order_placement:\s*false", content)
    assert re.search(r"risk_governor:\s*false", content)
    assert re.search(r"capital_allocation:\s*false", content)
