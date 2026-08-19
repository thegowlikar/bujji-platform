"""Safety verification -- Shadow Campaign v2 Phase 3D.

Proves, at the source level, that direction_bridge.py / the updated
MarketState schema (a) has no trade_bias/buy_signal/sell_signal/entry
field, (b) never imports trading_brain/strategy/execution/risk/order/
position/broker modules, (c) msi_market_direction itself was never
modified, and (d) ShadowSessionRunner is not coupled to any of this.
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
# Phase 3D's own file only -- see test_market_state_safety_phase3c.py's
# identical comment for why the whole directory is no longer the right
# scope for this forbidden-import check.
PHASE_3D_FILE = "bujji/market_state/direction_bridge.py"

FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_strategy_selector|msi_trade_intent|msi_trade_thesis|msi_decision_synthesis|"
    r"msi_shadow_trading|msi_strategy_eligibility|execution_integration|broker)\b"
)


def _grep(pattern, path, flags="-rnE"):
    return subprocess.run(
        ["grep", flags, pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


# AUTHORIZED CHANGE (2026-08-19, operator-approved): live-premium fix in
# bujji/msi_trade_construction/engine.py. The engine read `row.settlement`
# as the ONLY premium source. The bhavcopy replay provider populates
# settlement; the LIVE chain provider explicitly does not (settlement=None,
# traded price in `close`). On live data every strike therefore resolved to
# premium=None -> iv=None -> delta=None -> ZERO candidates, and every live
# entry attempt died with REJECT_STRIKE_UNAVAILABLE. Bujji could not
# construct a trade on live data at all -- a live-only failure invisible to
# this suite, which drives the bhavcopy path exclusively.
#
# The fix is `_premium_for(row)`: settlement FIRST (every bhavcopy decision,
# replay and test bit-for-bit unchanged -- agreement asserted by
# tests/test_live_chain_strike_selection.py), then the mid of a real
# two-sided quote, then the last trade; absence stays absence, and the basis
# used is recorded on the evidence. Selection logic, target deltas and
# rejection semantics are untouched.
_LIVE_PREMIUM_FIX_AUTHORIZED = ("bujji/msi_trade_construction/engine.py",)

# AUTHORIZED CHANGE (2026-08-19, operator directive): three-part regime
# strategy selection. The previous table had two tradeable outcomes, both
# neutral (IRON_CONDOR / IRON_FLY), so a TRENDING market always resolved to
# no-trade -- its own reasoning blamed the absence of "a Gate-B-approved
# defined-risk SELLING strategy", which was true only because no directional
# credit spread existed in the construction engine.
#
#   bujji/msi_trade_construction/engine.py    + BULL_PUT_SPREAD / BEAR_CALL_SPREAD,
#     built from the SAME helpers IRON_CONDOR uses (_nearest_by_delta,
#     _wing_width, _nearest_grid). No existing family's branch is touched.
#   bujji/msi_trade_construction/taxonomy.py  + the two families in
#     SUPPORTED_FAMILIES and DEFINED_RISK_FAMILIES (every short leg is paired
#     with a protective long -- a structural fact, not a P&L claim).
#   bujji/msi_trade_construction/config.py    + delta targets (0.20, the same
#     premium-selling distance the other selling families use).
#   .../trading_session_governor/strategy_selector.py  three-part rewrite.
#
# RISK POSTURE CHANGED, with explicit operator approval: the sideways branch
# now selects short straddle / short strangle, which carry NAKED short legs
# and appear in taxonomy.UNDEFINED_RISK_FAMILIES. Every previously selectable
# family was defined-risk. Gate B's real SPAN veto, the capital check,
# portfolio limits, the daily loss limit, the emergency brake and the
# mandatory exit all still apply; the SHAPE no longer bounds the loss.
#
# Every no-trade path is unchanged: unknown regime, volatility expansion
# (checked before direction, so a directional branch cannot reach around it)
# and any unmapped combination still fail closed. Coverage:
# tests/test_three_part_strategy_selection.py.
_THREE_PART_SELECTION_AUTHORIZED = (
    "bujji/msi_trade_construction/engine.py",
    "bujji/msi_trade_construction/taxonomy.py",
    "bujji/msi_trade_construction/config.py",
    "bujji/production_runtime/trading_session_governor/strategy_selector.py",
)


def test_market_direction_summary_has_only_allowed_fields():
    from bujji.market_state.models import MarketDirectionSummary
    field_names = set(MarketDirectionSummary.__dataclass_fields__.keys())
    assert field_names == {"direction", "confidence", "evidence", "uncertainties"}
    forbidden = {"trade_bias", "buy_signal", "sell_signal", "entry", "strategy", "order", "position"}
    assert not (field_names & forbidden)


def test_market_state_still_has_no_strategy_trade_position_risk_fields():
    from bujji.market_state.models import MarketState
    field_names = set(MarketState.__dataclass_fields__.keys())
    forbidden = {
        "strategy", "trade", "entry", "exit", "position", "order", "risk",
        "signal", "decision", "approved", "blocked", "recommendation",
        "trade_bias", "buy_signal", "sell_signal",
    }
    assert not (field_names & forbidden), f"forbidden fields found: {field_names & forbidden}"


def test_no_trading_decision_or_execution_module_imports():
    out = _grep(FORBIDDEN_IMPORTS, PHASE_3D_FILE)
    assert out == "", f"forbidden import found: {out}"


def test_no_broker_import_at_all():
    out = _grep(r"^\s*(from|import)\s+(bujji\.)?broker\b", PACKAGE_DIR)
    assert out == "", f"unexpected broker import in a pure synthesis layer: {out}"


def test_no_order_position_margin_calls():
    out = _grep(
        r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
        r"get_margin|get_funds|connect)\(",
        PACKAGE_DIR,
    )
    assert out == "", f"forbidden call found: {out}"


def test_msi_market_direction_itself_unmodified():
    result = subprocess.run(
        ["git", "diff", "--stat", "360c003", "--",
         "bujji/msi_market_direction/engine.py", "bujji/msi_market_direction/models.py"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    assert result.stdout.strip() == "", f"msi_market_direction was modified: {result.stdout}"


def test_shadow_session_runner_not_coupled_to_market_direction():
    out = _grep("market_direction", "bujji/shadow_runtime/shadow_session_runner.py")
    assert out == "", f"unexpected market_direction coupling in shadow_session_runner.py: {out}"


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
    # Phase 9 Liquidity Intelligence Bridge deliberately modified exactly
    # these two files (declarative taxonomy + pure-function engine, no
    # execution/order/risk logic) -- explicitly approved, unlike every
    # other change this phase's own package-isolation rule guards
    # against. See test_intelligence_cycle_recorder_extended_safety.py's
    # test_strategy_selection_foundation_change_is_scoped_to_liquidity_bridge
    # for the narrower guarantee that now applies to that package.
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
        and l not in _phase9_liquidity_bridge_exception and l not in _LIVE_PREMIUM_FIX_AUTHORIZED
           and l not in _THREE_PART_SELECTION_AUTHORIZED
    ]
    assert violations == [], f"unexpected protected-package changes: {violations}"
