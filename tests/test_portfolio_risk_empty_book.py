"""Empty-book portfolio risk: zero is not unknown.

D.2 previously classified a portfolio holding NOTHING as RISK_INVALID
(INSUFFICIENT_PORTFOLIO_RISK_DATA), blocking the whole governor
pipeline. That inverted the risk model at the safest possible moment --
an empty book is less risky than any open one -- and made the first
trade against a fresh journal unplaceable.

The bulk of this file is deliberately about what did NOT change: every
fail-closed path that protects a real book must still fire.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.risk_governor.portfolio_risk_aggregator import (
    IllegalPortfolioRiskAggregationError,
    RISK_INVALID,
    aggregate_portfolio_risk,
    classify_portfolio_risk,
)
from bujji.trading_brain.risk_governor.position_group_fold import (
    LIFECYCLE_CLOSED,
    LIFECYCLE_OPEN,
)

FIXED = datetime(2026, 8, 17, 10, 0, 0, tzinfo=timezone.utc)


def clock():
    return FIXED


class _Fill:
    def __init__(self, filled=75, reduced=0):
        self.cumulative_filled_quantity = filled
        self.reduced_quantity = reduced


class _Leg:
    """Real shape: net quantity is derived from the leg's own fill record
    (`position_group_fold.net_quantity`), never stored directly."""

    def __init__(self, filled=75, reduced=0):
        self.fill = _Fill(filled, reduced)


class _Group:
    def __init__(self, pg_id, lifecycle=LIFECYCLE_OPEN, strategy_id="IRON_CONDOR", legs=None, underlying="NIFTY"):
        self.position_group_id = pg_id
        self.lifecycle_state = lifecycle
        self.strategy_id = strategy_id
        # Real shape: legs are keyed by client_order_id, not a list.
        self.legs = legs if legs is not None else {"COID-1": _Leg()}
        self.underlying = underlying


class TestEmptyBookIsAllowed:
    def test_empty_book_yields_zero_concentration_not_none(self):
        snapshot = aggregate_portfolio_risk([], None, None, {}, clock)
        assert snapshot.largest_position_concentration == 0.0
        assert snapshot.strategy_concentration == {}
        assert snapshot.total_max_loss == 0.0

    def test_empty_book_is_not_classified_invalid(self):
        snapshot = aggregate_portfolio_risk([], None, None, {}, clock)
        status, reasons = classify_portfolio_risk(snapshot)
        assert status != RISK_INVALID
        assert "INSUFFICIENT_PORTFOLIO_RISK_DATA" not in reasons

    def test_a_book_of_only_closed_groups_is_also_empty(self):
        """CLOSED groups are not active -- a book whose positions have all
        been closed is as empty as one that never traded."""
        groups = [_Group("PG-OLD", lifecycle=LIFECYCLE_CLOSED)]
        snapshot = aggregate_portfolio_risk(groups, None, None, {}, clock)
        assert snapshot.largest_position_concentration == 0.0
        assert classify_portfolio_risk(snapshot)[0] != RISK_INVALID


class TestFailClosedPathsUnchanged:
    """The safety behaviour that must NOT have been weakened."""

    def test_active_group_missing_risk_entry_still_raises(self):
        groups = [_Group("PG-1")]
        with pytest.raises(IllegalPortfolioRiskAggregationError, match="missing an entry"):
            aggregate_portfolio_risk(groups, None, None, {}, clock)

    def test_active_group_with_negative_risk_still_raises(self):
        groups = [_Group("PG-1")]
        with pytest.raises(IllegalPortfolioRiskAggregationError, match="negative risk"):
            aggregate_portfolio_risk(groups, None, None, {"PG-1": -1.0}, clock)

    def test_no_risk_map_at_all_is_still_invalid_even_with_no_positions(self):
        """`None` means the caller supplied no risk data -- genuinely
        unknown. Only an explicit empty MAP asserts 'no positions'."""
        snapshot = aggregate_portfolio_risk([], None, None, None, clock)
        assert snapshot.largest_position_concentration is None
        assert classify_portfolio_risk(snapshot)[0] == RISK_INVALID

    def test_no_risk_map_with_active_positions_is_still_invalid(self):
        groups = [_Group("PG-1")]
        snapshot = aggregate_portfolio_risk(groups, None, None, None, clock)
        assert classify_portfolio_risk(snapshot)[0] == RISK_INVALID

    def test_a_real_concentrated_book_is_still_flagged(self):
        """One position carrying all the risk is 100% concentrated and
        must still be classified as such, not smoothed toward zero."""
        groups = [_Group("PG-1", strategy_id="IRON_CONDOR")]
        snapshot = aggregate_portfolio_risk(groups, None, None, {"PG-1": 5000.0}, clock)
        assert snapshot.largest_position_concentration == 1.0
        status, _ = classify_portfolio_risk(snapshot)
        assert status != RISK_INVALID
        assert status in ("HIGH_RISK", "CONCENTRATED")
