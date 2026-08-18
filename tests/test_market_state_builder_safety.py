"""Safety verification -- Shadow Campaign v2 Phase 3B.

Proves, at the source level, that bujji/market_state_builder/ cannot
place orders, query positions/margins, and never imports
trading_brain / execution_engine / risk_governor / strategy_selector /
msi_trade_intent / msi_trade_thesis / msi_decision_synthesis /
msi_shadow_trading -- keeping this phase strictly a market-UNDERSTANDING
layer, never a decision layer.
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


PACKAGE_DIR = "bujji/market_state_builder/"

FORBIDDEN_CALLS = (
    r"\.(place_order|modify_order|cancel_order|get_open_positions|get_positions|"
    r"get_margin|get_funds|connect)\("
)
FORBIDDEN_IMPORTS = (
    r"^\s*(from|import)\s+(bujji\.)?(trading_brain|execution_engine|risk_governor|"
    r"msi_strategy_selector|msi_trade_intent|msi_trade_thesis|msi_decision_synthesis|"
    r"msi_shadow_trading|msi_strategy_eligibility|execution_integration|broker)\b"
)


def _grep(pattern, path):
    return subprocess.run(
        ["grep", "-rnE", pattern, path], cwd="/opt/bujji/app", capture_output=True, text=True,
    ).stdout.strip()


def test_no_forbidden_broker_or_order_calls():
    out = _grep(FORBIDDEN_CALLS, PACKAGE_DIR)
    assert out == "", f"forbidden call found: {out}"


def test_no_broker_import_at_all():
    out = _grep(r"^\s*(from|import)\s+(bujji\.)?broker\b", PACKAGE_DIR)
    assert out == "", f"unexpected broker import in a pure market-understanding layer: {out}"


def test_no_trading_decision_or_execution_module_imports():
    out = _grep(FORBIDDEN_IMPORTS, PACKAGE_DIR)
    assert out == "", f"forbidden import found: {out}"


def test_no_order_position_margin_identifiers_hardcoded():
    out = _grep(r"\b(place_order|order_id|position_id|margin_required|BUY|SELL)\b", PACKAGE_DIR)
    assert out == "", f"order/position/margin-shaped identifiers found: {out}"


def test_only_authorized_msi_packages_imported():
    out = _grep(r"^\s*(from|import)\s+(bujji\.)?msi_\w+", PACKAGE_DIR)
    lines = [l for l in out.splitlines() if l]
    authorized = ("msi_market_structure", "msi_price_structure", "msi_participant_positioning")
    for line in lines:
        assert any(pkg in line for pkg in authorized), f"unauthorized msi_* import: {line}"


def test_no_msi_or_protected_package_was_modified_this_phase():
    result = subprocess.run(
        ["git", "diff", "--name-only", "b148e39"],
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
        if any(l.startswith(p) for p in protected_prefixes) and "market_state_builder" not in l
        and l not in _phase9_liquidity_bridge_exception
    ]
    assert violations == [], f"unexpected protected-package changes: {violations}"


# Phase 17E (Layer 0 Raw Observation Store) deliberately, explicitly
# approved change -- the same documented-exception pattern this file
# already uses for the Phase 9 Liquidity Bridge above.
#
# `bujji/market_observation/taxonomy.py` gained exactly two additive
# things: the MARKET_DEPTH observation domain (the order-book type Layer 0
# must record -- confirmed live 2026-08-12 as the only FYERS endpoint
# carrying futures OI), and a MARKET_OBSERVATION_VERSION bump to 1.1.0 so
# consumers must explicitly opt into understanding that new type. 1.0.0
# REMAINS in RECOGNIZED_SCHEMA_VERSIONS, so no previously-written record
# is invalidated. The taxonomy's own module docstring sanctions this
# shape of change: "a new domain requires a deliberate addition here,
# never an inferred string."
#
# Nothing else in that package -- models, engine, journal, serialization,
# query, runner, config -- may change, and every other package below
# remains fully protected. See docs/PHASE_17E_LAYER0_IMPLEMENTATION_PLAN.md
# and tests/test_market_reality_safety.py.
_phase17e_layer0_exception = ("bujji/market_observation/taxonomy.py",)

# Authorised 2026-08-18: a FABRICATION fix in live_market_events/engine.py's
# `_numeric_field`. The SCALAR branch ignored `field_name` entirely and
# returned the bare payload to every caller, so a spot PRICE observation
# answered "open_interest" and "volume" with the NIFTY level. Two real spot
# ticks therefore emitted OI_CHANGED and VOLUME_CHANGED carrying a price as
# open interest and as traded volume -- verified on the production bridge
# (24601.05 -> 24608.30 emitted both; after the fix, neither).
#
# This is exactly what the freeze exists to protect against, not an
# exception to it: the function's own docstring already promised "Never
# fabricates a field: returns None when the payload does not carry it",
# and the scalar branch broke that promise. The fix makes the code match
# the contract it always claimed.
#
# It also has downstream weight beyond tidiness -- those phantom events
# inflate the episode and event counts that PSI confidence and the thesis
# evidence gates are computed from, so the regime read was being scored on
# partly invented evidence. Coverage: tests/test_scalar_field_fabrication.py.
_scalar_fabrication_fix_exception = ("bujji/live_market_events/engine.py",)


def test_market_observation_live_market_events_market_episode_unmodified():
    result = subprocess.run(
        ["git", "diff", "--name-only", "b148e39", "--",
         "bujji/market_observation/", "bujji/live_market_events/", "bujji/market_episode/",
         "bujji/options_observation/", "bujji/msi_market_structure/", "bujji/msi_price_structure/",
         "bujji/msi_participant_positioning/"],
        cwd="/opt/bujji/app", capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _PAPERBROKER_V2_AUTHORIZED]
    _authorised = _phase17e_layer0_exception + _scalar_fabrication_fix_exception
    violations = [l for l in changed if l not in _authorised]
    assert violations == [], f"a reused engine package was modified: {violations}"


def test_shadow_session_runner_still_unmodified_by_this_phase():
    # Phase 3B is explicitly standalone -- confirm ShadowSessionRunner has
    # zero coupling to market_state_builder (this phase touches nothing in
    # it, per "Do NOT modify ShadowSessionRunner initially"). The file's
    # Phase 2 diff against baseline is expected and already covered by
    # tests/test_intelligence_safety_phase2.py; this test only guards
    # against THIS (Phase 3B) phase adding anything new to it.
    #
    # Phase 15D documented exception: ShadowSessionRunner now imports
    # hydrate_observation_memory from bujji.market_state_builder.recovery
    # -- a single, additive, opt-in (default-off) call inside __init__
    # that replays this session's own already-persisted
    # market_snapshots.jsonl through a fresh MarketStateBuilder on
    # startup. This is a real, approved coupling (Phase 15D's entire
    # purpose), not an accidental one -- verified additive-only by
    # test_observation_memory_recovery_safety.py's diff-based checks.
    # This test still forbids any OTHER market_state_builder coupling
    # beyond that one documented import line.
    grep_out = _grep("market_state_builder", "bujji/shadow_runtime/shadow_session_runner.py")
    phase15d_exception = "from bujji.market_state_builder.recovery import hydrate_observation_memory"
    lines = [l for l in grep_out.splitlines() if phase15d_exception not in l]
    assert lines == [], f"unexpected market_state_builder coupling in shadow_session_runner.py: {lines}"
