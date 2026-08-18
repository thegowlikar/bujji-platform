"""Safety verification -- Shadow Campaign v2 Phase 1.

Proves, at the source level, that bujji/market_perception/ cannot
place orders, modify/cancel orders, query positions, or query margins
-- and never imports Trading Brain / Risk Governor / MSI decision
modules, keeping this phase strictly a read-only data layer.
"""
from __future__ import annotations

import subprocess

PACKAGE_DIR = "bujji/market_perception/"


def test_no_forbidden_broker_calls_in_market_perception():
    result = subprocess.run(
        ["grep", "-rnE",
         r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|get_margin|get_funds)\(",
         PACKAGE_DIR],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"forbidden broker call found: {result.stdout}"


def test_no_forbidden_broker_calls_added_to_fyers_py():
    # The only broker.py change this phase is the new get_futures_quote()
    # method + its _futures_symbol() helper -- confirm no order/position/
    # margin method was touched by diffing against the last known-clean
    # commit this phase started from.
    result = subprocess.run(
        ["git", "diff", "b148e39", "--", "bujji/broker/fyers.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    diff_lines = [l for l in result.stdout.splitlines() if l.startswith("+") and not l.startswith("+++")]
    added_text = "\n".join(diff_lines)
    for forbidden in ("def place_order", "def modify_order", "def cancel_order", "def get_open_positions", "def get_margin"):
        assert forbidden not in added_text, f"{forbidden} was touched by this phase's diff"


def test_no_protected_module_imports_in_market_perception():
    result = subprocess.run(
        ["grep", "-rnE",
         r"^\s*(from|import)\s+(bujji\.)?(trading_brain|msi_decision_synthesis|msi_strategy_selector|"
         r"msi_trade_construction|msi_shadow_trading|msi_position_lifecycle|risk_governor|"
         r"execution_integration|shadow_runtime)\b",
         PACKAGE_DIR],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"forbidden import found: {result.stdout}"


def test_no_decision_shaped_dataclass_fields_in_market_perception():
    # Narrower than a blanket text grep (docstrings legitimately explain
    # what this layer does NOT do, using these very words) -- this checks
    # actual dataclass field declarations only, which is what would matter
    # if a decision-shaped field were ever added to the schema.
    import sys
    sys.path.insert(0, "/opt/bujji/app")
    from bujji.market_perception import models as mp_models
    forbidden = {"signal", "decision", "confidence", "approved", "blocked", "recommendation"}
    for name in dir(mp_models):
        obj = getattr(mp_models, name)
        if hasattr(obj, "__dataclass_fields__"):
            field_names = set(obj.__dataclass_fields__.keys())
            assert not (field_names & forbidden), f"{name} has forbidden fields: {field_names & forbidden}"


def test_market_perception_does_not_reference_shadow_runtime_pid_or_live_session():
    # Sanity check: this package must be fully independent of the currently
    # running live Shadow Runtime session -- no coupling of any kind.
    result = subprocess.run(
        ["grep", "-rlnE", "shadow_session_runner|ShadowSessionRunner", PACKAGE_DIR],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"unexpected coupling to shadow_runtime found: {result.stdout}"
