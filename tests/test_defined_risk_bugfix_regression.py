"""Regression tests for two audit findings fixed together:

1. defined_risk.py: _vertical_spread_max_loss / _iron_condor_max_loss
   used min(short_qty, long_qty) for BOTH pricing and the veto decision,
   silently discarding any excess SHORT quantity with no offsetting
   LONG hedge -- naked, unbounded risk that priced as a falsely-bounded
   ALLOW instead of a VETO.
"""
from __future__ import annotations

from datetime import datetime

import pytest

from bujji.trading_brain.nifty_contract_builder.models import NiftyOptionContract
from bujji.trading_brain.order_construction.models import OrderRequest, OrderTags
from bujji.trading_brain.risk_governor.defined_risk import (
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
         "cumulative_average_fill_price_after": price, "delta_quantity": qty, "delta_value": qty * price,
         "delta_cost_basis_status": "DERIVED", "fill_price": price},
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


def _order(client_order_id, contract, order_type="LIMIT", reference_price=None, quantity=50):
    return OrderRequest(
        request_id=f"REQ-{client_order_id}", contract=contract, side=contract.side, quantity=quantity,
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
IRON_CONDOR_PROFILE = StrategyRiskProfile(
    strategy_id="IRON_CONDOR", required_leg_roles=("SHORT_LEG_CE", "SHORT_LEG_PE", "LONG_LEG_CE", "LONG_LEG_PE"),
    formula="IRON_CONDOR_MAX_WING_WIDTH_MINUS_TOTAL_CREDIT", lot_size=75,
)


def test_vertical_spread_short_exceeding_long_vetoes_instead_of_understating_max_loss(tmp_path):
    """Bug: short=750 (10 lots), long=300 (4 lots) used to silently price
    at qty=300 (ALLOW, max_loss=21000.0) instead of vetoing the 6 lots of
    naked, unhedged short exposure."""
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-MISMATCH", {"C-SHORT": "COID-SHORT", "C-LONG": "COID-LONG"},
        {"COID-SHORT": 750, "COID-LONG": 300},
    )
    _submit_ack_fill(journal, pg_id, "COID-SHORT", 750, 50.0)
    _submit_ack_fill(journal, pg_id, "COID-LONG", 300, 20.0)
    state = fold(journal.read_events(pg_id))

    short_contract = _contract("C-SHORT", strike=24800)
    long_contract = _contract("C-LONG", strike=24900)
    contracts = {"COID-SHORT": short_contract, "COID-LONG": long_contract}
    orders = {
        "COID-SHORT": _order("COID-SHORT", short_contract, "LIMIT", 50.0, quantity=750),
        "COID-LONG": _order("COID-LONG", long_contract, "LIMIT", 20.0, quantity=300),
    }
    roles = {"COID-SHORT": "SHORT_LEG", "COID-LONG": "LONG_LEG"}

    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "INCOMPLETE_DEFINED_RISK_GROUP"
    assert result.max_loss is None


def test_vertical_spread_long_exceeding_short_still_allows_as_before(tmp_path):
    """The safe direction (excess LONG, no naked short) must remain
    unaffected by this fix -- extra long premium with no offsetting
    short is just extra bounded long exposure."""
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-SAFE-EXCESS-LONG", {"C-SHORT": "COID-SHORT", "C-LONG": "COID-LONG"},
        {"COID-SHORT": 300, "COID-LONG": 750},
    )
    _submit_ack_fill(journal, pg_id, "COID-SHORT", 300, 50.0)
    _submit_ack_fill(journal, pg_id, "COID-LONG", 750, 20.0)
    state = fold(journal.read_events(pg_id))

    short_contract = _contract("C-SHORT", strike=24800)
    long_contract = _contract("C-LONG", strike=24900)
    contracts = {"COID-SHORT": short_contract, "COID-LONG": long_contract}
    orders = {
        "COID-SHORT": _order("COID-SHORT", short_contract, "LIMIT", 50.0, quantity=300),
        "COID-LONG": _order("COID-LONG", long_contract, "LIMIT", 20.0, quantity=750),
    }
    roles = {"COID-SHORT": "SHORT_LEG", "COID-LONG": "LONG_LEG"}

    result = assess_defined_risk(state, contracts, orders, roles, VERTICAL_SPREAD_PROFILE, clock=_clock())
    assert result.decision == "ALLOW"
    assert result.max_loss == pytest.approx((100 * 50 * (300 / 50)) - (30 * 300))


def test_iron_condor_naked_call_wing_vetoes(tmp_path):
    """Bug: a global min() across all 4 legs let a mismatched CALL wing
    (short > long) hide behind an unrelated leg's smaller quantity,
    silently underpricing naked call exposure instead of vetoing it."""
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-CONDOR-NAKED-CALL",
        {"C-SC": "SC", "C-LC": "LC", "C-SP": "SP", "C-LP": "LP"},
        {"SC": 150, "LC": 75, "SP": 75, "LP": 75},   # call side: short 150 > long 75 (naked)
    )
    for coid, qty, price in (("SC", 150, 100.0), ("LC", 75, 5.0), ("SP", 75, 90.0), ("LP", 75, 3.0)):
        _submit_ack_fill(journal, pg_id, coid, qty, price)
    state = fold(journal.read_events(pg_id))
    sc = _contract("SC", 24900, "CE")
    lc = _contract("LC", 24950, "CE")
    sp = _contract("SP", 24700, "PE")
    lp = _contract("LP", 24650, "PE")
    contracts = {"SC": sc, "LC": lc, "SP": sp, "LP": lp}
    orders = {
        "SC": _order("SC", sc, "LIMIT", 100.0, quantity=150), "LC": _order("LC", lc, "LIMIT", 5.0, quantity=75),
        "SP": _order("SP", sp, "LIMIT", 90.0, quantity=75), "LP": _order("LP", lp, "LIMIT", 3.0, quantity=75),
    }
    roles = {"SC": "SHORT_LEG_CE", "LC": "LONG_LEG_CE", "SP": "SHORT_LEG_PE", "LP": "LONG_LEG_PE"}
    result = assess_defined_risk(state, contracts, orders, roles, IRON_CONDOR_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "INCOMPLETE_DEFINED_RISK_GROUP"
    assert result.max_loss is None


def test_iron_condor_naked_put_wing_vetoes(tmp_path):
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-CONDOR-NAKED-PUT",
        {"C-SC": "SC", "C-LC": "LC", "C-SP": "SP", "C-LP": "LP"},
        {"SC": 75, "LC": 75, "SP": 150, "LP": 75},   # put side: short 150 > long 75 (naked)
    )
    for coid, qty, price in (("SC", 75, 100.0), ("LC", 75, 5.0), ("SP", 150, 90.0), ("LP", 75, 3.0)):
        _submit_ack_fill(journal, pg_id, coid, qty, price)
    state = fold(journal.read_events(pg_id))
    sc = _contract("SC", 24900, "CE")
    lc = _contract("LC", 24950, "CE")
    sp = _contract("SP", 24700, "PE")
    lp = _contract("LP", 24650, "PE")
    contracts = {"SC": sc, "LC": lc, "SP": sp, "LP": lp}
    orders = {
        "SC": _order("SC", sc, "LIMIT", 100.0, quantity=75), "LC": _order("LC", lc, "LIMIT", 5.0, quantity=75),
        "SP": _order("SP", sp, "LIMIT", 90.0, quantity=150), "LP": _order("LP", lp, "LIMIT", 3.0, quantity=75),
    }
    roles = {"SC": "SHORT_LEG_CE", "LC": "LONG_LEG_CE", "SP": "SHORT_LEG_PE", "LP": "LONG_LEG_PE"}
    result = assess_defined_risk(state, contracts, orders, roles, IRON_CONDOR_PROFILE, clock=_clock())
    assert result.decision == "VETO"
    assert result.blocking_reason == "INCOMPLETE_DEFINED_RISK_GROUP"
    assert result.max_loss is None


def test_iron_condor_both_wings_hedged_still_allows_as_before(tmp_path):
    """Unaffected control case: both wings properly hedged (short <=
    long on each side) must still ALLOW exactly as before the fix."""
    journal = _journal(tmp_path)
    pg_id = _mint_and_construct(
        journal, "PLAN-CONDOR-HEDGED",
        {"C-SC": "SC", "C-LC": "LC", "C-SP": "SP", "C-LP": "LP"},
        {"SC": 75, "LC": 75, "SP": 75, "LP": 75},
    )
    for coid, qty, price in (("SC", 75, 20.0), ("LC", 75, 5.0), ("SP", 75, 18.0), ("LP", 75, 3.0)):
        _submit_ack_fill(journal, pg_id, coid, qty, price)
    state = fold(journal.read_events(pg_id))
    sc = _contract("SC", 24900, "CE")
    lc = _contract("LC", 24950, "CE")
    sp = _contract("SP", 24700, "PE")
    lp = _contract("LP", 24650, "PE")
    contracts = {"SC": sc, "LC": lc, "SP": sp, "LP": lp}
    orders = {
        "SC": _order("SC", sc, "LIMIT", 20.0, quantity=75), "LC": _order("LC", lc, "LIMIT", 5.0, quantity=75),
        "SP": _order("SP", sp, "LIMIT", 18.0, quantity=75), "LP": _order("LP", lp, "LIMIT", 3.0, quantity=75),
    }
    roles = {"SC": "SHORT_LEG_CE", "LC": "LONG_LEG_CE", "SP": "SHORT_LEG_PE", "LP": "LONG_LEG_PE"}
    result = assess_defined_risk(state, contracts, orders, roles, IRON_CONDOR_PROFILE, clock=_clock())
    assert result.decision == "ALLOW"
    assert result.max_loss is not None
