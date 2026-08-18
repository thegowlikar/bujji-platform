"""Safety verification -- Shadow Campaign v2 Phase 3A.

Proves, at the source level, that bujji/market_perception/msi_adapter.py
cannot place orders, query positions/margins, and never imports
Trading Brain / Execution / Risk Governor / any msi_* package this
phase did not authorize touching -- confirming the bridge stayed a pure
data-translation layer, never a strategy or decision layer.
"""
from __future__ import annotations

import subprocess

ADAPTER_FILE = "bujji/market_perception/msi_adapter.py"
MARKET_PERCEPTION_DIR = "bujji/market_perception/"


def _grep(pattern, path, extra_flags="-rnE"):
    return subprocess.run(
        ["grep", extra_flags, pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


def test_no_forbidden_broker_calls_in_msi_adapter():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|get_margin|get_funds|connect)\(",
        ADAPTER_FILE,
    )
    assert out == "", f"forbidden broker call found: {out}"


def test_no_broker_import_at_all_in_msi_adapter():
    out = _grep(r"^\s*(from|import)\s+(bujji\.)?broker\b", ADAPTER_FILE)
    assert out == "", f"unexpected broker import in a pure MSI translation layer: {out}"


def test_no_execution_or_decision_module_imports_in_msi_adapter():
    out = _grep(
        r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
        r"execution_integration|msi_strategy_selector|msi_shadow_trading)\b",
        ADAPTER_FILE,
    )
    assert out == "", f"forbidden import found: {out}"


def test_no_strategy_hardcoding_identifiers_in_msi_adapter():
    # No strategy-family name, order side, or hardcoded trade type should
    # ever appear in a pure translation layer.
    out = _grep(
        r"\b(SHORT_STRADDLE|IRON_CONDOR|BUY|SELL|LONG|SHORT_STRANGLE|place_order)\b",
        ADAPTER_FILE, extra_flags="-nE",
    )
    assert out == "", f"strategy-shaped hardcoding found: {out}"


def test_only_msi_volatility_structure_is_imported_from_the_msi_family():
    # This phase deliberately bridges exactly one MSI module -- confirm
    # no other msi_* package was imported (would imply an unauthorized,
    # possibly-fabricated bridge to a blocked module).
    out = _grep(r"^\s*(from|import)\s+(bujji\.)?msi_\w+", ADAPTER_FILE)
    lines = [l for l in out.splitlines() if l]
    assert all("msi_volatility_structure" in l for l in lines), f"unexpected msi_* import: {out}"
    assert len(lines) >= 1, "expected at least one msi_volatility_structure import"


def test_msi_volatility_structure_module_itself_is_unmodified():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/msi_volatility_structure/engine.py", "bujji/msi_volatility_structure/models.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"msi_volatility_structure was modified: {result.stdout}"


def test_no_msi_package_anywhere_was_modified_this_phase():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    # Phase 9 Liquidity Intelligence Bridge deliberately, explicitly
    # approved change -- see test_market_direction_safety_phase3d.py's
    # identical exception for the full rationale.
    _phase9_liquidity_bridge_exception = (
        "bujji/msi_strategy_selection_foundation/engine.py",
        "bujji/msi_strategy_selection_foundation/taxonomy.py",
        "bujji/msi_decision_synthesis/config.py",
        "bujji/msi_trade_intent/engine.py",
    )
    msi_changed = [
        l for l in changed
        if ("/msi_" in l or l.startswith("msi_")) and l not in _phase9_liquidity_bridge_exception
    ]
    assert msi_changed == [], f"unexpected msi_* package changes: {msi_changed}"
