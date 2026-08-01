"""Tests — Numeric Risk Governor Gate B (defined-risk projection engine)."""
from __future__ import annotations

from datetime import datetime, timezone

import pytest

from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.order_construction.models import OrderRequest, OrderTags
from bujji.trading_brain.risk_governor.defined_risk import (
    DefinedRiskAssessment,
    IllegalDefinedRiskInputError,
    StrategyRiskProfile,
    assess_defined_risk,
)
from bujji.trading_brain.risk_governor.position_group_fold import fold
from bujji.journal.position_group_journal import PositionGroupJournal


def _clock(iso="2026-08-01T09:15:00+00:00"):
    dt = datetime.fromisoformat(iso)
    return lambda: dt


def _journal(tmp_path):
    return PositionGroupJournal(tmp_path / "pg.db")


def _mint_and_construct(journal, plan_id, contract_client_order_map, requested_quantities):
    from bujji.trading_brain.risk_governor.position_group_mint import mint_position_group_id
    mint = mint_position_group_id(journal, plan_id, "VERTICAL_SPREAD", "NIFTY", clock=_clock())
    journal.append_event(
        mint.position_group_id, "CONSTRUCTED", f"{mint.position_group_id}:CONSTRUCTED:0",
        {"contract_client_order_map": contract_client_order_map, "requested_quantities": requested_quantities,
         "actions": {}, "target_position_group_ids": {}, "target_contract_ids": {}, "flip_link_ids": {}},
        clock=_clock(),
    )
    return mint.position_group_id


def _submit_ack_fill(journal, pg, coid, qty, price):
    journal.append_event(pg, "SUBMIT_INTENT", f"{pg}:SUBMIT_INTENT:{coid}", {"client_order_id": coid}, clock=_clock())
    journal.append_event(
        pg, "SUBMIT_ACK", f"{pg}:SUBMIT_ACK:{coid}",
        {"client_order_id": coid, "broker_order_id": coid, "broker_reported_status": "ACCEPTED"}, clock=_clock(),
    )
    journal.append_event(
        pg, "FILL_OBSERVED", f"{pg}:FILL_OBSERVED:{coid}:{qty}:{price}",
        {"client_order_id": coid, "cumulative_filled_quantity_after": qty,
         "cumulative_average_fill_price_after": price, "delta_quantity": qty,
         "delta_value": qty * price, "delta_cost_basis_status": "DERIVED", "fill_price": price},
        clock=_clock(),
    )


def _contract(contract_id, strike, option_type="CE"):
    return NiftyOptionContract(
        contract_id=contract_id, underlying="NIFTY", expiry="2026-08-27", strike=strike,
        option_type=option_type, side="SELL" if option_type == "CE" else "BUY",
        contract_symbol=f"NIFTY26AUG{strike}{option_type}", capital_intent="STANDARD",
        strategy_id="VERTICAL_SPREAD", selection_reason="test", construction_trace="test",
        timestamp="2026-08-01T09:15:00+00:00", version="1.0.0",
    )


def _order(client_order_id, contract, order_type="LIMIT", reference_price=None):
    return OrderRequest(
        request_id=f"REQ-{client_order_id}", contract=contract, side=contract.side, quantity=50,
        order_type=order_type, product="MIS", validity="DAY", execution_policy="LIMIT",
        client_order_id=client_order_id,
        tags=OrderTags(strategy_id="VERTICAL_SPREAD", session_id="S1", pipeline_version="1.0.0",
                        qualification_fingerprint="FP-1"),
        creation_trace="test", timestamp="2026-08-01T09:15:00+00:00", version="1.0.0",
        reference_price=reference_price,
    )


VERTICAL_SPREAD_PROFILE = StrategyRiskProfile(
    strategy_id="VERTICAL_SPREAD", required_leg_roles=("SHORT_LEG", "LONG_LEG"),
    formula="VERTICAL_SPREAD_WIDTH_MINUS_CREDIT", lot_size=50,
)


def _build_open_spread(journal, pg="PLAN-SPREAD"):
    pg_id = _mint_and_construct(
        journal, pg, {"C-SHORT": "COID-SHORT", "C-LONG": "COID-LONG"},
        {"COID-SHORT": 50, "COID-LONG": 50},
    )
    _submit_ack_fill(journal, pg_id, "COID-SHORT", 50, 100.0)
    _submit_ack_fill(journal, pg_id, "COID-LONG", 50, 40.0)
    state = fold(journal.read_events(pg_id))

    short_contract = _contract("C-SHORT", strike=24800)
    long_contract = _contract("C-LONG", strike=24900)
    short_order = _order("COID-SHORT", short_contract, order_type="LIMIT", reference_price=100.0)
    long_order = _order("COID-LONG", long_contract, order_type="LIMIT", reference_price=40.0)

    contracts_by_coid = {"COID-SHORT": short_contract, "COID-LONG": long_contract}
    orders_by_coid = {"COID-SHORT": short_order, "COID-LONG": long_order}
    leg_roles = {"COID-SHORT": "SHORT_LEG", "COID-LONG": "LONG_LEG"}
    return state, contracts_by_coid, orders_by_coid, leg_roles


def test_complete_spread_within_limit_allows(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_open_spread(journal)
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    # width=100, credit=60/share -> max_loss = (100*50) - (60*50) = 2000.0
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx(2000.0)
    assert result.formula_used == "VERTICAL_SPREAD_WIDTH_MINUS_CREDIT"


def test_market_order_anywhere_in_group_vetoes(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_open_spread(journal)
    orders = dict(orders)
    orders["COID-LONG"] = _order("COID-LONG", contracts["COID-LONG"], order_type="MARKET")
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "NO_ADVERSE_FILL_BOUND_FOR_MARKET_ORDER"
    assert result.max_loss is None


def test_missing_required_leg_vetoes_incomplete(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_open_spread(journal)
    roles = {"COID-SHORT": "SHORT_LEG"}  # LONG_LEG role never assigned
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "INCOMPLETE_DEFINED_RISK_GROUP"


def test_leg_not_yet_ack_vetoes_incomplete(tmp_path):
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-PENDING", {"C-SHORT": "COID-SHORT", "C-LONG": "COID-LONG"},
        {"COID-SHORT": 50, "COID-LONG": 50},
    )
    journal.append_event(pg_id, "SUBMIT_INTENT", f"{pg_id}:SUBMIT_INTENT:COID-SHORT",
                          {"client_order_id": "COID-SHORT"}, clock=_clock())
    _submit_ack_fill(journal, pg_id, "COID-LONG", 50, 40.0)
    state = fold(journal.read_events(pg_id))
    short_contract = _contract("C-SHORT", 24800)
    long_contract = _contract("C-LONG", 24900)
    contracts = {"COID-SHORT": short_contract, "COID-LONG": long_contract}
    orders = {
        "COID-SHORT": _order("COID-SHORT", short_contract, "LIMIT", 100.0),
        "COID-LONG": _order("COID-LONG", long_contract, "LIMIT", 40.0),
    }
    roles = {"COID-SHORT": "SHORT_LEG", "COID-LONG": "LONG_LEG"}
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    # Gate A's own group-level lifecycle fold already marks the whole group
    # UNRESOLVED when any leg is SUBMIT_PENDING_UNKNOWN -- that check fires
    # before Gate B's own per-leg completeness check, correctly deferring to
    # Gate A's fail-closed lifecycle rather than re-deciding it here.
    assert "GROUP_NOT_ASSESSABLE_LIFECYCLE" in result.blocking_reason


def test_naked_leg_no_profile_vetoes_undefined_risk(tmp_path):
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(journal, "PLAN-NAKED", {"C-NAKED": "COID-NAKED"}, {"COID-NAKED": 50})
    _submit_ack_fill(journal, pg_id, "COID-NAKED", 50, 80.0)
    state = fold(journal.read_events(pg_id))
    naked_contract = _contract("C-NAKED", 24800)
    contracts = {"COID-NAKED": naked_contract}
    orders = {"COID-NAKED": _order("COID-NAKED", naked_contract, "LIMIT", 80.0)}
    roles = {"COID-NAKED": "NAKED_LEG"}
    result = assess_defined_risk(state, contracts, orders, roles, profile=None, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "UNDEFINED_RISK_NO_STRESS_MODEL"


def test_unresolved_group_lifecycle_vetoes(tmp_path):
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(journal, "PLAN-UNRES", {"C-SHORT": "COID-SHORT"}, {"COID-SHORT": 50})
    journal.append_event(pg_id, "SUBMIT_INTENT", f"{pg_id}:SUBMIT_INTENT:COID-SHORT",
                          {"client_order_id": "COID-SHORT"}, clock=_clock())
    state = fold(journal.read_events(pg_id))
    result = assess_defined_risk(state, {}, {}, {}, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert "GROUP_NOT_ASSESSABLE_LIFECYCLE" in result.blocking_reason


def test_partial_close_recomputes_from_net_quantity(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_open_spread(journal)
    pg_id = state.position_group_id

    # Reduce the short leg by 20 via a mirrored TARGET_GROUP_REDUCTION_APPLIED
    # (Gate A's own close/flip mechanism), leaving net 30 open on that leg.
    from bujji.journal.position_group_journal import EventSpec
    reduce_mint_pg = "PG-REDUCER"
    journal.append_event(
        reduce_mint_pg, "MINTED", f"{reduce_mint_pg}:MINTED:0",
        {"plan_id": "PLAN-REDUCE", "strategy_id": "REDUCE", "underlying": "NIFTY"}, clock=_clock(),
    )
    journal.append_event(
        reduce_mint_pg, "CONSTRUCTED", f"{reduce_mint_pg}:CONSTRUCTED:0",
        {"contract_client_order_map": {"C-SHORT": "COID-REDUCE"}, "requested_quantities": {"COID-REDUCE": 20},
         "actions": {"COID-REDUCE": "REDUCING"}, "target_position_group_ids": {"COID-REDUCE": pg_id},
         "target_contract_ids": {"COID-REDUCE": "C-SHORT"}, "flip_link_ids": {}}, clock=_clock(),
    )
    journal.append_event(reduce_mint_pg, "SUBMIT_INTENT", f"{reduce_mint_pg}:SUBMIT_INTENT:COID-REDUCE",
                          {"client_order_id": "COID-REDUCE"}, clock=_clock())
    journal.append_event(
        reduce_mint_pg, "SUBMIT_ACK", f"{reduce_mint_pg}:SUBMIT_ACK:COID-REDUCE",
        {"client_order_id": "COID-REDUCE", "broker_order_id": "COID-REDUCE", "broker_reported_status": "ACCEPTED"},
        clock=_clock(),
    )
    fill_spec = EventSpec(
        reduce_mint_pg, "FILL_OBSERVED", f"{reduce_mint_pg}:FILL_OBSERVED:COID-REDUCE:20:100.0",
        {"client_order_id": "COID-REDUCE", "cumulative_filled_quantity_after": 20,
         "cumulative_average_fill_price_after": 100.0, "delta_quantity": 20, "delta_value": 2000.0,
         "delta_cost_basis_status": "DERIVED", "fill_price": 100.0},
    )
    reduction_spec = EventSpec(
        pg_id, "TARGET_GROUP_REDUCTION_APPLIED", f"{pg_id}:TARGET_GROUP_REDUCTION_APPLIED:COID-REDUCE",
        {"source_client_order_id": "COID-REDUCE", "source_position_group_id": reduce_mint_pg,
         "target_contract_id": "C-SHORT", "reduced_quantity_delta": 20},
    )
    journal.append_linked_events([fill_spec, reduction_spec], clock=_clock())

    reduced_state = fold(journal.read_events(pg_id))
    result = assess_defined_risk(reduced_state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    # min(net_short=30, net_long=50) = 30 governs quantity now, not the original 50.
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx((100 * 50 * (30 / 50)) - (60.0 * 30))


def test_purity_no_mutation_of_inputs(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_open_spread(journal)
    state_before = state
    contracts_before = dict(contracts)
    orders_before = dict(orders)
    assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert state is state_before
    assert contracts == contracts_before
    assert orders == orders_before


# --------------------------------------------------------------------- #
# Pre-trade (CONSTRUCTED, nothing submitted yet) entry authorization
# --------------------------------------------------------------------- #

def _build_constructed_spread(journal, pg="PLAN-PRETRADE"):
    """A freshly-minted, freshly-CONSTRUCTED group -- nothing submitted,
    nothing filled. This is exactly the state a NEW entry decision is
    assessed from, before any broker interaction has happened."""
    pg_id = _mint_and_construct(
        journal, pg, {"C-SHORT": "COID-SHORT", "C-LONG": "COID-LONG"},
        {"COID-SHORT": 50, "COID-LONG": 50},
    )
    state = fold(journal.read_events(pg_id))

    short_contract = _contract("C-SHORT", strike=24800)
    long_contract = _contract("C-LONG", strike=24900)
    short_order = _order("COID-SHORT", short_contract, order_type="LIMIT", reference_price=100.0)
    long_order = _order("COID-LONG", long_contract, order_type="LIMIT", reference_price=40.0)

    contracts_by_coid = {"COID-SHORT": short_contract, "COID-LONG": long_contract}
    orders_by_coid = {"COID-SHORT": short_order, "COID-LONG": long_order}
    leg_roles = {"COID-SHORT": "SHORT_LEG", "COID-LONG": "LONG_LEG"}
    return state, contracts_by_coid, orders_by_coid, leg_roles


def test_pre_trade_within_limit_allows(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_constructed_spread(journal)
    assert state.lifecycle_state == "CONSTRUCTED"
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    # Same math as the post-fill spread test, but sourced from
    # requested_quantity (50/50) since nothing has filled yet.
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx(2000.0)


def test_pre_trade_leg_already_submitted_is_not_a_valid_pre_trade_state(tmp_path):
    """A leg that has already moved past NOT_SUBMITTED (e.g. a caller
    bug re-running pre-trade assessment on a group that's already
    mid-submission) must be rejected, not silently assessed as if
    nothing had happened."""
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_constructed_spread(journal)
    pg_id = state.position_group_id
    journal.append_event(pg_id, "SUBMIT_INTENT", f"{pg_id}:SUBMIT_INTENT:COID-SHORT",
                          {"client_order_id": "COID-SHORT"}, clock=_clock())
    # The group itself is now UNRESOLVED (Gate A's own fold), so this is
    # actually caught one layer up -- but confirm explicitly it is never
    # silently treated as still-pre-trade-assessable.
    state = fold(journal.read_events(pg_id))
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert "GROUP_NOT_ASSESSABLE_LIFECYCLE" in result.blocking_reason


def test_pre_trade_market_order_still_vetoes(tmp_path):
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_constructed_spread(journal)
    orders = dict(orders)
    orders["COID-LONG"] = _order("COID-LONG", contracts["COID-LONG"], order_type="MARKET")
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "NO_ADVERSE_FILL_BOUND_FOR_MARKET_ORDER"


def test_pre_trade_missing_requested_quantity_vetoes_incomplete(tmp_path):
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-NOQTY", {"C-SHORT": "COID-SHORT", "C-LONG": "COID-LONG"},
        {"COID-SHORT": 50},  # LONG_LEG never given a requested quantity
    )
    state = fold(journal.read_events(pg_id))
    short_contract = _contract("C-SHORT", 24800)
    long_contract = _contract("C-LONG", 24900)
    contracts = {"COID-SHORT": short_contract, "COID-LONG": long_contract}
    orders = {
        "COID-SHORT": _order("COID-SHORT", short_contract, "LIMIT", 100.0),
        "COID-LONG": _order("COID-LONG", long_contract, "LIMIT", 40.0),
    }
    roles = {"COID-SHORT": "SHORT_LEG", "COID-LONG": "LONG_LEG"}
    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "INCOMPLETE_DEFINED_RISK_GROUP"


def test_zero_lot_size_never_crashes_with_zero_division(tmp_path):
    """Audit finding: StrategyRiskProfile.lot_size=0 used to crash the
    vertical-spread formula with an unhandled ZeroDivisionError instead
    of failing closed like every other malformed-input case."""
    journal = _journal(tmp_path)
    state, contracts, orders, roles = _build_open_spread(journal)
    zero_lot_profile = StrategyRiskProfile(
        strategy_id="VERTICAL_SPREAD", required_leg_roles=("SHORT_LEG", "LONG_LEG"),
        formula="VERTICAL_SPREAD_WIDTH_MINUS_CREDIT", lot_size=0,
    )
    result = assess_defined_risk(state, contracts, orders, roles, zero_lot_profile, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "INCOMPLETE_DEFINED_RISK_GROUP"
