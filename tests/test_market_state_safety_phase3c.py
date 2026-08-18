"""Safety verification -- Shadow Campaign v2 Phase 3C.

Proves, at the source level, that bujji/market_state/ (a) has no
strategy/trade/entry/exit/position/order/risk field on MarketState,
(b) never imports trading_brain/strategy/execution/risk/order/position
modules, (c) implements no scoring/prediction logic, and (d) is not
wired into ShadowSessionRunner.
"""
from __future__ import annotations

import subprocess

# PaperBroker v2: the ONE authorized change inside the protected trading
# brain -- portfolio_risk_aggregator now distinguishes an empty book's
# genuinely-zero concentration from unknown data, which previously made
# the first trade of every fresh journal unplaceable (RISK_INVALID). Every
# fail-closed path for a NON-empty book is unchanged; see
# tests/test_portfolio_risk_empty_book.py. This guard still fails on any
# OTHER change under the protected packages.
_PAPERBROKER_V2_AUTHORIZED = ("bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",)


PACKAGE_DIR = "bujji/market_state/"
# Excludes Phase 5B's domain_view_adapter.py -- it legitimately imports
# msi_consensus/msi_decision_synthesis (its own dedicated safety test,
# test_domain_view_adapter_safety_phase5b.py, covers it), which this
# older, broader forbidden-import list would otherwise flag.
GREP_EXCLUDE = "--exclude=domain_view_adapter.py"
# Also excludes Phase 5D's trade_thesis_bridge.py -- same reasoning,
# its own dedicated safety test
# (test_trade_thesis_bridge_safety_phase5d.py) covers it, and it
# legitimately imports msi_trade_thesis.
# Also excludes Phase 6B's strategy_eligibility_bridge.py -- same
# reasoning, its own dedicated safety test
# (test_strategy_eligibility_bridge_safety_phase6b.py) covers it.

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_strategy_selector|msi_trade_intent|msi_trade_thesis|msi_decision_synthesis|"
    r"msi_shadow_trading|msi_strategy_eligibility|execution_integration|broker)\b"
)


def _grep(pattern, path, flags="-rnE"):
    return subprocess.run(
        ["grep", flags, GREP_EXCLUDE, "--exclude=trade_thesis_bridge.py",
         "--exclude=strategy_eligibility_bridge.py", "--exclude=intelligence_cycle_recorder.py",
         pattern, path],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


def test_market_state_has_no_strategy_trade_position_risk_fields():
    from bujji.market_state.models import MarketState
    field_names = set(MarketState.__dataclass_fields__.keys())
    forbidden = {
        "strategy", "trade", "entry", "exit", "position", "order", "risk",
        "signal", "decision", "approved", "blocked", "recommendation",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_no_trading_decision_or_execution_module_imports():
    out = _grep(FORBIDDEN_IMPORTS, PACKAGE_DIR)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_import_at_all():
    out = _grep(r"^\s*(from|import)\s+(bujji\.)?broker\b", PACKAGE_DIR)
    assert out == "", f"unexpected broker import in a pure synthesis layer: {out}"


def test_no_scoring_or_probability_identifiers_as_actual_code():
    # Narrower than a blanket text grep (docstrings legitimately explain
    # what this layer does NOT implement, using these very words) --
    # checks actual dataclass field declarations and assignment/call
    # sites only.
    import sys
    sys.path.insert(0, "/opt/bujji/app")
    from bujji.market_state import models as ms_models
    forbidden = {"bullish_score", "bearish_score", "buy_probability", "sell_probability", "signal_strength"}
    for name in dir(ms_models):
        obj = getattr(ms_models, name)
        if hasattr(obj, "__dataclass_fields__"):
            field_names = set(obj.__dataclass_fields__.keys())
            assert not (field_names & forbidden), f"{name} has forbidden fields: {field_names & forbidden}"

    out = _grep(r"\.(place_order|order_id\s*=|position_id\s*=)", PACKAGE_DIR)
    assert out == "", f"order/position-shaped code found: {out}"


def test_no_order_position_margin_calls():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect)\(",
        PACKAGE_DIR,
    )
    assert out == "", f"forbidden call found: {out}"


def test_shadow_session_runner_not_coupled_to_market_state():
    out = _grep("market_state\\b", "bujji/shadow_runtime/shadow_session_runner.py")
    # Match only the market_state package name, not market_state_builder
    # (a legitimately separate, unrelated Phase-3B name substring), and
    # not intelligence_cycle_recorder -- a deliberate, approved wiring
    # (the Continuous Intelligence Observatory phase), covered by its
    # own dedicated safety test, test_intelligence_cycle_recorder_safety.py.
    lines = [
        l for l in out.splitlines()
        if "market_state_builder" not in l and "intelligence_cycle_recorder" not in l
    ]
    assert lines == [], f"unexpected market_state coupling in shadow_session_runner.py: {lines}"


def test_no_protected_package_was_modified_this_phase():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED]
    protected_prefixes = (
        "bujji/msi_", "bujji/trading_brain/", "bujji/execution_engine/",
        "bujji/risk_governor/", "bujji/msi_shadow_trading/",
    )
    # Phase 9 Liquidity Intelligence Bridge deliberately, explicitly
    # approved change -- see test_market_direction_safety_phase3d.py's
    # identical exception for the full rationale.
    _phase9_liquidity_bridge_exception = (
        "bujji/msi_strategy_selection_foundation/engine.py",
        "bujji/msi_strategy_selection_foundation/taxonomy.py",
        # Phase 14B: additive config.py extension + additive new function
        # in engine.py -- see docs/PHASE_14B_DECISION_PIPELINE_ARCHITECTURE.md
        # and tests/test_phase14b_safety.py.
        "bujji/msi_decision_synthesis/config.py",
        "bujji/msi_trade_intent/engine.py",
    )
    violations = [
        l for l in changed
        if any(l.startswith(p) for p in protected_prefixes)
        and "market_state_builder" not in l and "/market_state/" not in l
        and l not in _phase9_liquidity_bridge_exception
    ]
    assert violations == [], f"unexpected protected-package changes: {violations}"
