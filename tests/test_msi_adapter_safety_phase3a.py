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

# AUTHORIZED CHANGE (2026-08-20, operator directive): open interest reaches
# the direction read. Direction was derived from exactly two lenses, price
# structure and market structure, and BOTH read the same evidence -- NIFTY
# spot price polled every 30 seconds. One instrument, one field. When they
# disagreed the answer was UNKNOWN, which is what the first live continuous
# session reported for most of 2026-08-20.
#
# MPPI was already computing five lenses over ~199,000 option rows a day and
# reaching the THESIS, invisible to direction. OPTIONS_POSITIONING_DIRECTION
# had been sitting in KNOWN_LENS_NAMES unfilled the whole time.
#
#   bujji/msi_market_direction/engine.py   + derive_participant_positioning_lens
#     and an OPTIONAL mppi parameter (default None -> UNKNOWN opinion), so every
#     existing caller and test keeps working. No existing lens is touched and
#     the reconciliation rule is unchanged: conflicting lenses still yield
#     MIXED/UNKNOWN rather than an average.
#   bujji/market_state/direction_bridge.py  passes the assessment through.
#
# NO INVERSION: MPPI's bias is already normalised to PRICE direction (verified
# in derive_writer_dominance_lens -- call writers dominant yields
# BEARISH_POSITIONING). Confidence is capped at MODERATE because positioning
# is intent, not a fact about price; HIGH stays reserved for MSSI's structural
# breakout/breakdown. MIXED_POSITIONING becomes UNKNOWN, never NEUTRAL.
#
# Coverage: tests/test_direction_positioning_lens.py.
_DIRECTION_POSITIONING_LENS_AUTHORIZED = (
    "bujji/msi_market_direction/engine.py",
    "bujji/market_state/direction_bridge.py",
)


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
        if ("/msi_" in l or l.startswith("msi_")) and l not in _phase9_liquidity_bridge_exception and l not in _LIVE_PREMIUM_FIX_AUTHORIZED
           and l not in _THREE_PART_SELECTION_AUTHORIZED
           and l not in _DIRECTION_POSITIONING_LENS_AUTHORIZED
    ]
    assert msi_changed == [], f"unexpected msi_* package changes: {msi_changed}"
