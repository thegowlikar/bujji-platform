"""Tests — Numeric Risk Governor portfolio-level limits (simultaneous
positions counted by group, not leg; concentration by underlying)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.trading_brain.risk_governor.portfolio_limits import (
    PortfolioLimits,
    assess_portfolio_limits,
)
from bujji.trading_brain.risk_governor.position_group_fold import fold
from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id


def _clock(iso="2026-08-01T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _journal(tmp_path):
    return PositionGroupJournal(tmp_path / "pg.db")


def _open_group(journal, plan_id, underlying, coid, qty=50, price=100.0):
    mint = mint_position_group_id(journal, plan_id, "STRATEGY", underlying, clock=_clock())
    pg = mint.position_group_id
    journal.append_event(
        pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C1": coid}, "requested_quantities": {coid: qty},
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    journal.append_event(pg, "SUBMIT_INTENT", f"{pg}:SUBMIT_INTENT:{coid}", {"client_order_id": coid}, clock=_clock())
    journal.append_event(
        pg, "SUBMIT_ACK", f"{pg}:SUBMIT_ACK:{coid}",
        {"client_order_id": coid, "broker_order_id": coid, "broker_reported_status": "ACCEPTED"}, clock=_clock(),
    )
    journal.append_event(
        pg, "FILL_OBSERVED", f"{pg}:FILL_OBSERVED:{coid}:{qty}:{price}",
        {"client_order_id": coid, "cumulative_filled_quantity_after": qty,
         "cumulative_average_fill_price_after": price, "delta_quantity": qty, "delta_value": qty * price,
         "delta_cost_basis_status": "DERIVED", "fill_price": price},
        clock=_clock(),
    )
    return fold(journal.read_events(pg))


def _open_multi_leg_group(journal, plan_id, underlying, coids, qty=50, price=100.0):
    """A multi-leg group -- proves position counting is per-group, not per-leg."""
    mint = mint_position_group_id(journal, plan_id, "SPREAD", underlying, clock=_clock())
    pg = mint.position_group_id
    contract_map = {f"C{i}": coid for i, coid in enumerate(coids)}
    requested = {coid: qty for coid in coids}
    journal.append_event(
        pg, "CONSTRUCTED", f"{pg}:CONSTRUCTED:0",
        {"contract_client_order_map": contract_map, "requested_quantities": requested,
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    for coid in coids:
        journal.append_event(pg, "SUBMIT_INTENT", f"{pg}:SUBMIT_INTENT:{coid}",
                              {"client_order_id": coid}, clock=_clock())
        journal.append_event(
            pg, "SUBMIT_ACK", f"{pg}:SUBMIT_ACK:{coid}",
            {"client_order_id": coid, "broker_order_id": coid, "broker_reported_status": "ACCEPTED"},
            clock=_clock(),
        )
        journal.append_event(
            pg, "FILL_OBSERVED", f"{pg}:FILL_OBSERVED:{coid}:{qty}:{price}",
            {"client_order_id": coid, "cumulative_filled_quantity_after": qty,
             "cumulative_average_fill_price_after": price, "delta_quantity": qty, "delta_value": qty * price,
             "delta_cost_basis_status": "DERIVED", "fill_price": price},
            clock=_clock(),
        )
    return fold(journal.read_events(pg))


def test_within_limits_allows(tmp_path):
    journal = _journal(tmp_path)
    g1 = _open_group(journal, "PLAN-1", "NIFTY", "COID-1")
    g2 = _open_group(journal, "PLAN-2", "BANKNIFTY", "COID-2")
    limits = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=0.9)
    result = assess_portfolio_limits(
        [g1, g2], {g1.position_group_id: 5000.0, g2.position_group_id: 5000.0}, limits, clock=_clock(),
    )
    assert result.decision == "ALLOW"
    assert result.active_position_count == 2
    assert result.concentration_by_underlying == {"NIFTY": 0.5, "BANKNIFTY": 0.5}


def test_multi_leg_group_counts_as_one_position(tmp_path):
    journal = _journal(tmp_path)
    g = _open_multi_leg_group(journal, "PLAN-CONDOR", "NIFTY", ["COID-A", "COID-B", "COID-C", "COID-D"])
    limits = PortfolioLimits(max_simultaneous_positions=1, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits([g], {g.position_group_id: 1000.0}, limits, clock=_clock())
    assert result.decision == "ALLOW"
    assert result.active_position_count == 1   # not 4


def test_max_simultaneous_positions_exceeded(tmp_path):
    journal = _journal(tmp_path)
    g1 = _open_group(journal, "PLAN-1", "NIFTY", "COID-1")
    g2 = _open_group(journal, "PLAN-2", "NIFTY", "COID-2")
    limits = PortfolioLimits(max_simultaneous_positions=1, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits(
        [g1, g2], {g1.position_group_id: 1000.0, g2.position_group_id: 1000.0}, limits, clock=_clock(),
    )
    assert result.decision == "VETO"
    assert result.blocking_reason == "MAX_SIMULTANEOUS_POSITIONS_EXCEEDED"


def test_concentration_exceeded(tmp_path):
    journal = _journal(tmp_path)
    g1 = _open_group(journal, "PLAN-1", "NIFTY", "COID-1")
    g2 = _open_group(journal, "PLAN-2", "BANKNIFTY", "COID-2")
    limits = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=0.5)
    result = assess_portfolio_limits(
        [g1, g2], {g1.position_group_id: 9000.0, g2.position_group_id: 1000.0}, limits, clock=_clock(),
    )
    assert result.decision == "VETO"
    assert result.blocking_reason.startswith("CONCENTRATION_EXCEEDED:NIFTY")


def test_missing_exposure_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    g1 = _open_group(journal, "PLAN-1", "NIFTY", "COID-1")
    limits = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits([g1], {}, limits, clock=_clock())  # no exposure entry at all
    assert result.decision == "VETO"
    assert result.blocking_reason == "EXPOSURE_DATA_MISSING"


def test_negative_exposure_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    g1 = _open_group(journal, "PLAN-1", "NIFTY", "COID-1")
    limits = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits([g1], {g1.position_group_id: -100.0}, limits, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "EXPOSURE_DATA_INVALID_NEGATIVE"


def test_zero_total_exposure_fails_closed(tmp_path):
    journal = _journal(tmp_path)
    g1 = _open_group(journal, "PLAN-1", "NIFTY", "COID-1")
    limits = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits([g1], {g1.position_group_id: 0.0}, limits, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "EXPOSURE_UNTRUSTED_ZERO_TOTAL"


def test_no_active_groups_allows_trivially(tmp_path):
    limits = PortfolioLimits(max_simultaneous_positions=5, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits([], {}, limits, clock=_clock())
    assert result.decision == "ALLOW"
    assert result.active_position_count == 0


def test_non_active_lifecycle_groups_excluded_from_count(tmp_path):
    journal = _journal(tmp_path)
    # A MINTED-only (never constructed) group must not count as an active position.
    mint = mint_position_group_id(journal, "PLAN-MINTED-ONLY", "STRATEGY", "NIFTY", clock=_clock())
    minted_state = fold(journal.read_events(mint.position_group_id))
    limits = PortfolioLimits(max_simultaneous_positions=0, max_concentration_per_underlying=1.0)
    result = assess_portfolio_limits([minted_state], {}, limits, clock=_clock())
    assert result.decision == "ALLOW"
    assert result.active_position_count == 0
