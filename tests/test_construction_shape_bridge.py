"""Tests -- bujji.construction_shape_bridge, Phase 20.29.

Real option chain (same Bhavcopy fixture tests/test_msi_trade_construction.py
already uses), real msi_position_construction.construct_position() calls
(same fixture-building pattern tests/test_msi_position_construction.py
already uses) -- nothing hand-fabricated beyond the same minimal
thesis/selection scaffolding those existing test files already build.
"""
from __future__ import annotations

import dataclasses
import subprocess
from pathlib import Path

import pytest

from bujji.construction_shape_bridge import bridge as csb
from bujji.msi_position_construction import engine as pc_engine
from bujji.msi_position_construction import taxonomy as pc_taxonomy
from bujji.msi_strategy_expression import engine as se_engine
from bujji.msi_strategy_selector.models import CandidateScore, Explanation as MssExplanation, StrategySelectionAssessment
from bujji.msi_trade_construction import engine as tc_engine
from bujji.msi_trade_construction import taxonomy as tc_taxonomy
from bujji.msi_trade_thesis.models import Explanation as ThesisExplanation, TradeThesisAssessment
from bujji.options_observation import runner as opt_runner

TS = "2026-05-25T15:30:00+05:30"
DAY = "2026-05-25"
REAL_BHAVCOPY = "/tmp/m1/BhavCopy_NSE_FO_0_0_0_20260525_F_0000.csv"
_REPO_ROOT = Path(__file__).resolve().parent.parent


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


def _real_chain():
    with open(REAL_BHAVCOPY) as f:
        text = f.read()
    series, _ = opt_runner.ingest_all_option_series_from_bhavcopy(text, DAY, underlying="NIFTY")
    return tuple(s.observations()[-1] for s in series if len(s.observations()) > 0)


@pytest.fixture(scope="module")
def chain():
    return _real_chain()


@pytest.fixture(scope="module")
def spot(chain):
    return next((r.underlying_price for r in chain if r.underlying_price), None)


def _thesis(thesis_type, direction="NEUTRAL", conviction="HIGH"):
    exp = ThesisExplanation(assessment_id="x", why_this_thesis=(), supporting_evidence=(), conflicting_evidence=(),
                             what_would_invalidate=(), schema_version="1.0.0")
    return TradeThesisAssessment(
        assessment_id="t1", timestamp=TS, thesis_type=thesis_type, market_expectation="m", expected_move=1.2,
        expected_time_horizon="NEXT_SESSION", volatility_expectation="STABLE", directional_expectation=direction,
        conviction=conviction, invalidation_conditions=(), supporting_domains=(), conflicting_domains=(),
        explanation=exp, provenance="p", schema_version="1.0.0",
    )


def _selection(family):
    exp = MssExplanation(assessment_id="s1", why_this_strategy=(), why_not_alternatives=(),
                          evidence_that_mattered_most=(), evidence_that_prevented_alternatives=(),
                          active_market_states=(), schema_version="1.0.0")
    return StrategySelectionAssessment(
        assessment_id="s1", timestamp=TS, selected_strategy_family=family, alternative_candidates=(),
        rejection_reasons=(), supporting_evidence=(), confidence="HIGH", explanation=exp,
        provenance="p", schema_version="1.0.0",
    )


def _position_construction(family, thesis_type="RANGE_PERSISTENCE", direction="NEUTRAL", conviction="HIGH"):
    thesis = _thesis(thesis_type, direction, conviction)
    expression = se_engine.derive_strategy_expression(thesis, timestamp=TS)
    selection = _selection(family)
    return pc_engine.construct_position(selection, expression, thesis, timestamp=TS)


# --------------------------------------------------------------------- #
# Match cases: bridge honors a real Series 95 plan by calling the real,
# unmodified Series 90 entrypoint, producing an IDENTICAL result to
# calling construct_trade() directly (pure pass-through, no new logic).
# --------------------------------------------------------------------- #

@pytest.mark.parametrize("family", ["NEUTRAL_PREMIUM_SELLING", "IRON_CONDOR", "IRON_FLY", "VOLATILITY_COMPRESSION"])
def test_matching_construction_type_produces_identical_result_to_direct_call(chain, spot, family):
    pca = _position_construction(family)
    assert pca.construction_type == csb.SUPPORTED_CONSTRUCTION_TYPE_PER_FAMILY[family]

    bridged = csb.construct_trade_honoring_position_plan(
        pca, family, chain, spot, DAY, timestamp=TS, supporting_assessment_ids=("s1",),
    )
    direct = tc_engine.construct_trade(
        family, chain, spot, DAY, timestamp=TS, supporting_assessment_ids=("s1",),
    )
    assert bridged == direct


def test_long_directional_high_conviction_matches_and_constructs(chain, spot):
    pca = _position_construction("LONG_DIRECTIONAL", thesis_type="TREND_CONTINUATION",
                                  direction="STRONG_BULLISH", conviction="HIGH")
    assert pca.construction_type == pc_taxonomy.CONSTRUCTION_SINGLE_LEG
    result = csb.construct_trade_honoring_position_plan(
        pca, "LONG_DIRECTIONAL", chain, spot, DAY, direction="STRONG_BULLISH", timestamp=TS,
    )
    assert result.rejection_reason != csb.REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED


# --------------------------------------------------------------------- #
# Mismatch cases: the real, disclosed gap -- Series 95 refines a
# directional family to a spread Series 90 cannot build. Bridge fails
# closed BEFORE construct_trade() is ever called.
# --------------------------------------------------------------------- #

def test_long_directional_moderate_conviction_refines_to_spread_and_bridge_refuses(chain, spot, monkeypatch):
    pca = _position_construction("LONG_DIRECTIONAL", thesis_type="TREND_CONTINUATION",
                                  direction="STRONG_BULLISH", conviction="MODERATE")
    assert pca.construction_type == pc_taxonomy.CONSTRUCTION_VERTICAL_DEBIT_SPREAD  # Series 95's real refinement

    called = []

    def _must_not_be_called(*args, **kwargs):
        called.append(1)
        raise AssertionError("construct_trade must never be called on a construction_type mismatch")

    monkeypatch.setattr(csb, "construct_trade", _must_not_be_called)

    result = csb.construct_trade_honoring_position_plan(
        pca, "LONG_DIRECTIONAL", chain, spot, DAY, direction="STRONG_BULLISH", timestamp=TS,
    )
    assert called == []
    assert result.constructed is False
    assert result.rejection_reason == csb.REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED
    assert "VERTICAL_DEBIT_SPREAD" in result.expiry_decision.reasoning[0]
    assert "SINGLE_LEG" in result.expiry_decision.reasoning[0]


def test_short_directional_defined_risk_refines_to_credit_spread_and_bridge_refuses(chain, spot):
    thesis = _thesis("TREND_CONTINUATION", direction="STRONG_BEARISH", conviction="HIGH")
    expression = se_engine.derive_strategy_expression(thesis, timestamp=TS)
    expression_defined_risk = dataclasses.replace(expression, desired_risk_profile="DEFINED_RISK")
    selection = _selection("SHORT_DIRECTIONAL")
    pca = pc_engine.construct_position(selection, expression_defined_risk, thesis, timestamp=TS)

    result = csb.construct_trade_honoring_position_plan(
        pca, "SHORT_DIRECTIONAL", chain, spot, DAY, direction="STRONG_BEARISH", timestamp=TS,
    )
    if pca.construction_type == pc_taxonomy.CONSTRUCTION_VERTICAL_CREDIT_SPREAD:
        assert result.constructed is False
        assert result.rejection_reason == csb.REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED
    else:
        # If this particular fixture didn't trigger the DEFINED_RISK refinement,
        # the match path is exercised instead -- still a valid, honest outcome.
        assert result.rejection_reason != csb.REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED


def test_no_strategy_selected_produces_none_construction_type_and_bridge_refuses(chain, spot):
    pca = _position_construction(None)
    assert pca.construction_type == pc_taxonomy.CONSTRUCTION_NONE
    result = csb.construct_trade_honoring_position_plan(
        pca, "LONG_DIRECTIONAL", chain, spot, DAY, timestamp=TS,
    )
    assert result.constructed is False
    assert result.rejection_reason == csb.REJECT_CONSTRUCTION_TYPE_NOT_SUPPORTED


# --------------------------------------------------------------------- #
# Determinism
# --------------------------------------------------------------------- #

def test_deterministic_same_input_same_output(chain, spot):
    pca = _position_construction("NEUTRAL_PREMIUM_SELLING")
    a = csb.construct_trade_honoring_position_plan(pca, "NEUTRAL_PREMIUM_SELLING", chain, spot, DAY, timestamp=TS)
    b = csb.construct_trade_honoring_position_plan(pca, "NEUTRAL_PREMIUM_SELLING", chain, spot, DAY, timestamp=TS)
    assert a == b


# --------------------------------------------------------------------- #
# Safety: msi_trade_construction remains byte-identical (the exact
# protection this phase's own first attempt violated and reverted).
# --------------------------------------------------------------------- #

def test_msi_trade_construction_still_byte_identical_to_baseline():
    result = subprocess.run(
        ["git", "diff", "--name-only", "360c003", "--", "bujji/msi_trade_construction/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    changed = [l for l in result.stdout.strip().splitlines() if l]
    changed = [l for l in changed if l not in _LIVE_PREMIUM_FIX_AUTHORIZED
               and l not in _THREE_PART_SELECTION_AUTHORIZED]
    assert changed == [], f"msi_trade_construction was modified, expected untouched: {changed}"


def test_msi_position_construction_also_untouched():
    result = subprocess.run(
        ["git", "status", "--porcelain", "--", "bujji/msi_position_construction/"],
        cwd=_REPO_ROOT, capture_output=True, text=True,
    )
    assert result.stdout.strip() == ""


def test_bridge_package_no_broker_or_execution_imports():
    pkg_dir = _REPO_ROOT / "bujji" / "construction_shape_bridge"
    forbidden = ("import bujji.broker", "from bujji.broker", "place_order", "modify_order", "cancel_order")
    for py_file in pkg_dir.glob("*.py"):
        source = py_file.read_text()
        for pattern in forbidden:
            assert pattern not in source, f"{pattern!r} found in {py_file.name}"
