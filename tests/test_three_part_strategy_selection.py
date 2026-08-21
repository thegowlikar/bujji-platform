"""Three-part regime selection: up, down, and the sideways market.

Operator directive 2026-08-19. The previous table had two tradeable outcomes,
both neutral, so a TRENDING market always resolved to no-trade -- its own
reasoning blamed the absence of "a Gate-B-approved defined-risk SELLING
strategy", which was true only because no directional credit spread existed.
Two were added, so the trending two-thirds of the market becomes tradeable.

The tests here pin three things: that each regime reaches the shape it should,
that every shape the selector can name is one the engine can actually BUILD
(a selector picking an unbuildable family produces a guaranteed rejection
instead of a trade), and that widening what Bujji sells did not widen WHEN it
is willing to sell.
"""
from __future__ import annotations

import datetime
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.msi_trade_construction import taxonomy as mtc_taxonomy
from bujji.msi_trade_construction.config import FAMILY_DELTA_TARGETS
from bujji.msi_trade_construction.engine import construct_trade
from bujji.options_observation.engine import build_option_observation
from bujji.production_runtime.trading_session_governor import strategy_selector as sel

CLOCK = lambda: datetime.datetime(2026, 8, 20, 10, 0)  # noqa: E731
SPOT = 24000.0
EXPIRY = "2026-08-25"


def pick(trend, vol):
    return sel.select_strategy(trend, vol, CLOCK)


class TestTheThreeMarketConditions:
    def test_sideways_high_vol_sells_a_straddle(self):
        r = pick("SIDEWAYS", "HIGH_VOL")
        assert r.selected_strategy == sel.FAMILY_SHORT_STRADDLE
        assert "straddle" in r.reasoning and r.confidence == "HIGH"

    @pytest.mark.parametrize("vol", ["LOW_VOL", "CONTRACTION"])
    def test_sideways_thin_premium_sells_a_strangle(self, vol):
        """Premium is thin, so distance from the money is worth more than the
        extra credit -- the judgement this rule encodes."""
        r = pick("SIDEWAYS", vol)
        assert r.selected_strategy == sel.FAMILY_SHORT_STRANGLE
        assert "strangle" in r.reasoning

    def test_uptrend_sells_a_bull_put_spread(self):
        r = pick("TRENDING_UP", "LOW_VOL")
        assert r.selected_strategy == "BULL_PUT_SPREAD"
        assert "Defined risk" in r.reasoning

    def test_downtrend_sells_a_bear_call_spread(self):
        r = pick("TRENDING_DOWN", "HIGH_VOL")
        assert r.selected_strategy == "BEAR_CALL_SPREAD"
        assert "Defined risk" in r.reasoning

    def test_a_trending_market_is_no_longer_an_automatic_no_trade(self):
        """The specific behaviour this phase changed."""
        assert pick("TRENDING_UP", "LOW_VOL").selected_strategy is not None
        assert pick("TRENDING_DOWN", "LOW_VOL").selected_strategy is not None

    def test_the_sideways_rule_is_config_not_a_hardcoded_opinion(self):
        """Straddle-vs-strangle is a trading judgement, so it must be
        invertible without touching selection logic."""
        original = dict(sel.SIDEWAYS_SHAPE_BY_VOLATILITY)
        try:
            sel.SIDEWAYS_SHAPE_BY_VOLATILITY.update({
                "HIGH_VOL": sel.FAMILY_SHORT_STRANGLE,
                "LOW_VOL": sel.FAMILY_SHORT_STRADDLE,
            })
            assert pick("SIDEWAYS", "HIGH_VOL").selected_strategy == sel.FAMILY_SHORT_STRANGLE
            assert pick("SIDEWAYS", "LOW_VOL").selected_strategy == sel.FAMILY_SHORT_STRADDLE
        finally:
            sel.SIDEWAYS_SHAPE_BY_VOLATILITY.clear()
            sel.SIDEWAYS_SHAPE_BY_VOLATILITY.update(original)


class TestWideningWhatNotWhen:
    """Every no-trade path that existed before must still exist."""

    @pytest.mark.parametrize("trend,vol", [
        (None, "LOW_VOL"), ("SIDEWAYS", None), ("UNKNOWN", "LOW_VOL"), ("SIDEWAYS", "UNKNOWN"),
    ])
    def test_an_unknown_regime_still_fails_closed(self, trend, vol):
        r = pick(trend, vol)
        assert r.selected_strategy is None and r.confidence == "NONE"

    @pytest.mark.parametrize("trend", ["SIDEWAYS", "TRENDING_UP", "TRENDING_DOWN"])
    def test_volatility_expansion_vetoes_every_regime_including_trends(self, trend):
        """Checked BEFORE direction on purpose: selling into rising vol is a
        bad trade in a trend exactly as in a range, and the ordering makes it
        non-negotiable rather than something a directional branch reaches
        around."""
        r = pick(trend, "EXPANSION")
        assert r.selected_strategy is None
        assert "expansion" in r.reasoning.lower()

    def test_an_unmapped_combination_still_fails_closed(self):
        r = pick("SIDEWAYS", "SOME_NEW_VOL_STATE")
        assert r.selected_strategy is None and "fail closed" in r.reasoning


class TestTheSelectorCanOnlyNameBuildableShapes:
    def test_every_selectable_family_is_supported_by_construction(self):
        """A selector that picks a shape the engine cannot build produces a
        guaranteed rejection instead of a trade."""
        selectable = set()
        for trend in ("SIDEWAYS", "TRENDING_UP", "TRENDING_DOWN"):
            for vol in ("LOW_VOL", "HIGH_VOL", "CONTRACTION"):
                chosen = pick(trend, vol).selected_strategy
                if chosen:
                    selectable.add(chosen)
        assert selectable, "positive control: the selector must choose something"
        assert selectable <= set(mtc_taxonomy.SUPPORTED_FAMILIES)

    def test_every_selectable_family_has_a_delta_target(self):
        for family in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD",
                       sel.FAMILY_SHORT_STRANGLE, sel.FAMILY_SHORT_STRADDLE):
            assert family in FAMILY_DELTA_TARGETS


class TestRiskClassificationIsHonest:
    def test_the_credit_spreads_are_classified_defined_risk(self):
        """Structural: every short leg is paired with a protective long."""
        for family in ("BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"):
            assert family in mtc_taxonomy.DEFINED_RISK_FAMILIES
            assert family not in mtc_taxonomy.UNDEFINED_RISK_FAMILIES

    def test_the_sideways_shapes_are_classified_undefined_risk(self):
        """Naked short legs. This is the risk posture the operator approved on
        2026-08-19, and it must stay visible rather than be quietly reclassified."""
        for family in (sel.FAMILY_SHORT_STRADDLE, sel.FAMILY_SHORT_STRANGLE):
            assert family in mtc_taxonomy.UNDEFINED_RISK_FAMILIES

    def test_the_sideways_reasoning_says_the_loss_is_unbounded(self):
        """A reader of the decision record must not have to know the taxonomy
        to learn that the shape does not cap the loss."""
        for vol in ("HIGH_VOL", "LOW_VOL"):
            assert "NAKED" in pick("SIDEWAYS", vol).reasoning


def _chain():
    rows = []
    for i in range(21):
        strike = float(23500 + 50 * i)
        for option_type in ("CE", "PE"):
            moneyness = (SPOT - strike) if option_type == "CE" else (strike - SPOT)
            premium = max(5.0, moneyness + 120.0)
            rows.append(build_option_observation(
                symbol_provenance="BROKER_AUTHORITATIVE",   # "NSE:..." -- broker form
                underlying="NIFTY", instrument_symbol=f"NSE:NIFTY{int(strike)}{option_type}",
                strike=strike, expiry=EXPIRY, option_type=option_type, exchange="NSE",
                segment="FO", timestamp="2026-08-19T10:00:00+05:30", resolution="SNAPSHOT",
                open_=None, high=None, low=None, close=premium, settlement=None,
                volume=1000, open_interest=50000, change_in_open_interest=100,
                underlying_price=SPOT, origin="test",
                acquisition_timestamp="x", normalization_timestamp="x",
                bid=premium - 0.5, ask=premium + 0.5))
    return rows


def _build(family):
    return construct_trade(family, _chain(), SPOT, "2026-08-19", expected_move_pct=0.006,
                           timestamp="2026-08-19T10:00:00+05:30", min_dte=0, max_dte=40)


class TestTheCreditSpreadsBuildCorrectly:
    def test_a_bull_put_spread_sells_a_put_and_buys_a_lower_one(self):
        result = _build("BULL_PUT_SPREAD")
        assert result.constructed, result.rejection_reason
        assert len(result.legs) == 2
        short = [l for l in result.legs if l.side == "SELL"][0]
        long_ = [l for l in result.legs if l.side == "BUY"][0]
        assert short.option_type == long_.option_type == "PE"
        assert long_.strike < short.strike, "protection must be FURTHER out of the money"

    def test_a_bear_call_spread_sells_a_call_and_buys_a_higher_one(self):
        result = _build("BEAR_CALL_SPREAD")
        assert result.constructed, result.rejection_reason
        short = [l for l in result.legs if l.side == "SELL"][0]
        long_ = [l for l in result.legs if l.side == "BUY"][0]
        assert short.option_type == long_.option_type == "CE"
        assert long_.strike > short.strike

    @pytest.mark.parametrize("family", ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"])
    def test_both_spreads_collect_a_credit(self, family):
        """A CREDIT spread that pays a debit is not one."""
        result = _build(family)
        sold = sum(l.premium for l in result.legs if l.side == "SELL")
        bought = sum(l.premium for l in result.legs if l.side == "BUY")
        assert sold > bought

    @pytest.mark.parametrize("family", ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"])
    def test_both_spreads_pair_every_short_with_a_long(self, family):
        """The structural fact behind the defined-risk classification."""
        result = _build(family)
        assert len([l for l in result.legs if l.side == "SELL"]) == 1
        assert len([l for l in result.legs if l.side == "BUY"]) == 1

    def test_the_wing_width_follows_the_expected_move(self):
        """Width comes from VSB's real expected move, the same source the iron
        condor uses -- not a constant private to these two families.

        NOTE THE UNIT: `expected_move_pct` is a PERCENTAGE NUMBER (0.6 means
        0.6%), not a fraction. `_wing_width` divides by 100. Passing 0.006
        intending 0.6% yields a width of 1.44 points, which rounds to zero and
        silently falls back to the 200-point constant -- a caller would get
        the fallback while believing the expected move drove the width. This
        test uses the correct unit and a second test below pins that trap."""
        def width_for(expected_move_pct):
            r = construct_trade("BULL_PUT_SPREAD", _chain(), SPOT, "2026-08-19",
                                expected_move_pct=expected_move_pct,
                                timestamp="2026-08-19T10:00:00+05:30", min_dte=0, max_dte=40)
            strikes = sorted(l.strike for l in r.legs)
            return strikes[-1] - strikes[0]

        assert width_for(2.0) > width_for(0.5)

    def test_a_fractional_expected_move_silently_becomes_the_fallback(self):
        """PRE-EXISTING TRAP, pinned rather than fixed here. A caller passing
        0.006 for "0.6%" gets the 200-point fallback, and `dominant_constraints`
        still reports "expected_move" because that label is chosen from
        `expected_move_pct is not None` rather than from whether the value
        actually produced a width. Shared with IRON_CONDOR/IRON_FLY -- changing
        it would move existing behaviour, so it is documented here and raised
        as a separate decision rather than altered inside a selection change."""
        from bujji.msi_trade_construction.config import WING_WIDTH_FALLBACK_POINTS
        from bujji.msi_trade_construction.engine import _wing_width

        assert _wing_width(0.006, 24000.0) == WING_WIDTH_FALLBACK_POINTS
        assert _wing_width(0.6, 24000.0) != WING_WIDTH_FALLBACK_POINTS

    @pytest.mark.parametrize("family", ["BULL_PUT_SPREAD", "BEAR_CALL_SPREAD"])
    def test_an_unreachable_wing_clamps_to_the_furthest_strike(self, family):
        """PRE-EXISTING BEHAVIOUR, pinned rather than fixed. `_nearest_grid`
        returns the closest available strike and never None, so a wing the
        chain cannot reach silently becomes the furthest listed strike:
        REJECT_IMPOSSIBLE_WING_WIDTH is effectively unreachable.

        The direction is conservative -- the spread ends up NARROWER than
        requested, so max loss is SMALLER, not larger -- but the disclosed
        width is then not the width that was applied. Shared with IRON_CONDOR;
        raised as a separate decision."""
        result = construct_trade(family, _chain(), SPOT, "2026-08-19", expected_move_pct=40.0,
                                 timestamp="2026-08-19T10:00:00+05:30", min_dte=0, max_dte=40)
        assert result.constructed, "documents the clamp; a refusal would be the safer design"
        strikes = sorted(l.strike for l in result.legs)
        chain_strikes = sorted({l.strike for l in _chain()})
        assert strikes[0] >= chain_strikes[0] and strikes[-1] <= chain_strikes[-1]


class TestEveryBranchEndToEnd:
    @pytest.mark.parametrize("trend,vol", [
        ("SIDEWAYS", "HIGH_VOL"), ("SIDEWAYS", "LOW_VOL"),
        ("TRENDING_UP", "LOW_VOL"), ("TRENDING_DOWN", "LOW_VOL"),
    ])
    def test_what_the_selector_picks_is_what_the_engine_builds(self, trend, vol):
        """The linkage that makes selection meaningful."""
        family = pick(trend, vol).selected_strategy
        assert family is not None
        result = _build(family)
        assert result.constructed, f"{family} selected but not constructible: {result.rejection_reason}"
        assert result.legs
