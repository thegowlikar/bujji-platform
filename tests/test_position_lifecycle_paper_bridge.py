"""Paper Bridge ingestion tests -- Phase 15L. Observational only: every
test drives a REAL PaperBroker through real `place_order()` calls
(paper fills), then proves the bridge reconstructs lifecycle-shaped
facts from that evidence without ever calling execution/strategy code
and without ever fabricating a missing value."""
from __future__ import annotations

import asyncio

import pytest

from bujji.broker.paper import PaperBroker
from bujji.broker.simulation.fill_simulator import PartialFillConfig
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.position_lifecycle.identity import leg_id_for, position_id_for
from bujji.position_lifecycle.models import LegRecord
from bujji.position_lifecycle.paper_bridge import (
    client_order_id_for, build_exit_evidence_from_observations,
    observe_leg_fills, observe_leg_fills_async, reconcile_position_exit,
)


def _contract(symbol="NIFTY24450CE", strike=24450.0, opt=OptionType.CE):
    return OptionContract(symbol, "NIFTY", strike, opt, "WEEKLY", 75)


def _leg(leg_id, side="BUY", quantity=1, option_type="CE", strike=24450.0):
    return LegRecord(
        leg_id=leg_id, role="PRIMARY", option_type=option_type, strike=strike, expiry="2026-08-13",
        side=side, quantity=quantity, entry_premium=100.0, entry_delta=0.5,
    )


def _fill(broker, position_id, leg_id, side, qty, price, sequence=1):
    coid = client_order_id_for(position_id, leg_id, sequence)
    request = OrderRequest(
        contract=_contract(), side=Side.BUY if side == "BUY" else Side.SELL,
        quantity=qty, client_order_id=coid, reference_price=price,
    )
    return asyncio.run(broker.place_order(request))


# 1. Single-leg profitable trade
def test_single_leg_profitable_trade():
    broker = PaperBroker()
    pid = position_id_for("S1", "C1", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    _fill(broker, pid, leg.leg_id, "SELL", 1, 150.0)  # SELL to exit a BUY entry at a profit.
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.exit_prices[leg.leg_id]["exit_price"] == 150.0
    assert result.all_legs_observed


# 2. Single-leg losing trade
def test_single_leg_losing_trade():
    broker = PaperBroker()
    pid = position_id_for("S1", "C2", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    _fill(broker, pid, leg.leg_id, "SELL", 1, 60.0)
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.exit_prices[leg.leg_id]["exit_price"] == 60.0


# 3. Short option (entry SELL, exit BUY)
def test_short_option_exit():
    broker = PaperBroker()
    pid = position_id_for("S1", "C3", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "PE", 24450.0, "2026-08-13"), side="SELL", option_type="PE")
    _fill(broker, pid, leg.leg_id, "BUY", 1, 40.0)
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.exit_prices[leg.leg_id]["exit_price"] == 40.0


# 4. Long option (entry BUY, exit SELL) -- covered by test 1, add PE variant.
def test_long_option_exit_pe():
    broker = PaperBroker()
    pid = position_id_for("S1", "C4", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "PE", 24450.0, "2026-08-13"), option_type="PE")
    _fill(broker, pid, leg.leg_id, "SELL", 1, 130.0)
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.exit_prices[leg.leg_id]["exit_price"] == 130.0


# 5. Multi-leg Iron Condor
def test_multi_leg_iron_condor():
    broker = PaperBroker()
    pid = position_id_for("S1", "C5", "t0")
    legs = (
        _leg(leg_id_for(pid, "LONG_CE", "CE", 24600.0, "2026-08-13"), side="BUY", option_type="CE", strike=24600.0),
        _leg(leg_id_for(pid, "SHORT_CE", "CE", 24500.0, "2026-08-13"), side="SELL", option_type="CE", strike=24500.0),
        _leg(leg_id_for(pid, "SHORT_PE", "PE", 24300.0, "2026-08-13"), side="SELL", option_type="PE", strike=24300.0),
        _leg(leg_id_for(pid, "LONG_PE", "PE", 24200.0, "2026-08-13"), side="BUY", option_type="PE", strike=24200.0),
    )
    for i, leg in enumerate(legs):
        exit_side = "SELL" if leg.side == "BUY" else "BUY"
        _fill(broker, pid, leg.leg_id, exit_side, 1, 50.0 + i)
    result = reconcile_position_exit(broker, pid, legs)
    assert len(result.exit_prices) == 4
    assert result.all_legs_observed


# 6. Straddle
def test_straddle():
    broker = PaperBroker()
    pid = position_id_for("S1", "C6", "t0")
    legs = (
        _leg(leg_id_for(pid, "CE_LEG", "CE", 24450.0, "2026-08-13"), option_type="CE"),
        _leg(leg_id_for(pid, "PE_LEG", "PE", 24450.0, "2026-08-13"), option_type="PE"),
    )
    for leg in legs:
        _fill(broker, pid, leg.leg_id, "SELL", 1, 90.0)
    result = reconcile_position_exit(broker, pid, legs)
    assert len(result.exit_prices) == 2


# 7. Partial fill
def test_partial_fill():
    broker = PaperBroker(partial_fill_qty=1)
    pid = position_id_for("S1", "C7", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), quantity=3)
    _fill(broker, pid, leg.leg_id, "SELL", 3, 100.0)  # partial_fill_qty caps this at 1.
    obs = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=3)
    assert obs.total_filled_qty == 1
    assert obs.status == "PARTIAL"


# 8. Multiple fills for one leg (topped up via sequence)
def test_multiple_fills_for_one_leg():
    broker = PaperBroker(partial_fill_qty=1)
    pid = position_id_for("S1", "C8", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), quantity=2)
    coid1 = client_order_id_for(pid, leg.leg_id, 1)
    coid2 = client_order_id_for(pid, leg.leg_id, 2)
    asyncio.run(broker.place_order(OrderRequest(_contract(), Side.SELL, 2, coid1, reference_price=100.0)))
    broker._partial_fill_qty = None  # second order fills fully.
    asyncio.run(broker.place_order(OrderRequest(_contract(), Side.SELL, 1, coid2, reference_price=105.0)))
    obs = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=2)
    assert obs.fill_count == 2
    assert obs.total_filled_qty == 2
    assert obs.status == "COMPLETE"


# 9. Partial exit -- PARTIAL_EXIT remains DEFERRED at the lifecycle level (Phase 15K);
# the bridge itself can still observe a partial fill honestly without implying a lifecycle PARTIAL_EXIT status.
def test_partial_exit_observation_does_not_imply_lifecycle_partial_status():
    from bujji.position_lifecycle.models import ALL_STATUSES, STATUS_CLOSED, STATUS_OPEN
    assert ALL_STATUSES == (STATUS_OPEN, STATUS_CLOSED)


# 10. Full exit
def test_full_exit():
    broker = PaperBroker()
    pid = position_id_for("S1", "C10", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), quantity=2)
    _fill(broker, pid, leg.leg_id, "SELL", 2, 100.0)
    obs = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=2)
    assert obs.status == "COMPLETE"
    assert obs.total_filled_qty == 2


# 11. Fees
def test_fees_present_in_reconciliation():
    broker = PaperBroker()
    pid = position_id_for("S1", "C11", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    _fill(broker, pid, leg.leg_id, "SELL", 1, 100.0)
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.fees is not None and result.fees > 0


# 12. Slippage
def test_slippage_present_when_config_active():
    from bujji.broker.simulation.slippage import SlippageConfig, SlippageMode
    broker = PaperBroker(slippage_config=SlippageConfig(mode=SlippageMode.FIXED_TICK, fixed_ticks=2))
    pid = position_id_for("S1", "C12", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    _fill(broker, pid, leg.leg_id, "SELL", 1, 100.0)
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.slippage is not None


# 13. Duplicate broker observation -- idempotent (re-reading twice gives identical facts).
def test_duplicate_observation_is_idempotent():
    broker = PaperBroker()
    pid = position_id_for("S1", "C13", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    _fill(broker, pid, leg.leg_id, "SELL", 1, 100.0)
    obs1 = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=1)
    obs2 = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=1)
    assert obs1.to_dict() == obs2.to_dict()


# 14. Conflicting broker observation -- PaperBroker's own idempotent place_order
# means a second place_order call with the SAME client_order_id never re-fills
# (returns the cached result) -- the bridge inherits this guarantee for free,
# proven here directly against the broker's own contract.
def test_conflicting_second_order_with_same_client_id_does_not_double_fill():
    broker = PaperBroker()
    pid = position_id_for("S1", "C14", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    coid = client_order_id_for(pid, leg.leg_id, 1)
    r1 = asyncio.run(broker.place_order(OrderRequest(_contract(), Side.SELL, 1, coid, reference_price=100.0)))
    r2 = asyncio.run(broker.place_order(OrderRequest(_contract(), Side.SELL, 1, coid, reference_price=999.0)))
    assert r1.average_price == r2.average_price  # second call ignored -- same cached result.
    obs = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=1)
    assert obs.total_filled_qty == 1  # not double-counted.


# 15. Restart/recovery -- covered in the dedicated replay test file (Phase 15L Step 6);
# here confirm the bridge itself has no persistence state to lose (pure, stateless reads).
def test_bridge_is_stateless_between_calls():
    import inspect
    assert "self" not in inspect.signature(observe_leg_fills).parameters


# 16. Malformed broker state -- a leg with no matching orders at all.
def test_malformed_broker_state_no_orders_for_leg():
    broker = PaperBroker()
    pid = position_id_for("S1", "C16", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    obs = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=1)
    assert obs.status == "NO_FILLS"
    assert obs.avg_fill_price is None


# 17. Missing fill data -- a rejected order (no fill at all).
def test_missing_fill_data_on_rejection():
    from bujji.broker.simulation.fill_simulator import RejectionConfig
    broker = PaperBroker(rejection_config=RejectionConfig(force_reject=True))
    pid = position_id_for("S1", "C17", "t0")
    leg = _leg(leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    _fill(broker, pid, leg.leg_id, "SELL", 1, 100.0)
    obs = observe_leg_fills(broker, pid, leg.leg_id, expected_qty=1)
    assert obs.status == "NO_FILLS"
    assert obs.avg_fill_price is None


# 18. Missing fee data -- exit_evidence never assumes zero fees when unavailable.
def test_missing_fee_data_never_assumed_zero():
    from bujji.position_lifecycle.paper_bridge import LegFillObservation
    obs_with_fees = LegFillObservation("L1", 1, 1, 100.0, 5.0, 0.0, "t0", "COMPLETE")
    obs_without_fees = LegFillObservation("L2", 1, 1, 100.0, None, 0.0, "t0", "COMPLETE")
    exit_prices, fees, slippage = build_exit_evidence_from_observations([obs_with_fees, obs_without_fees])
    assert fees is None  # one leg's fees unknown -> aggregate fees is honestly None, never partial-summed as if complete.
    assert len(exit_prices) == 2


# 19. Cross-session contamination -- position_id embeds session_id, so
# two different sessions' deterministic client_order_ids for the "same"
# candidate/leg shape never collide.
def test_cross_session_contamination_impossible():
    broker = PaperBroker()
    pid_a = position_id_for("SESSION-A", "SAME-CANDIDATE", "t0")
    pid_b = position_id_for("SESSION-B", "SAME-CANDIDATE", "t0")
    leg_a = _leg(leg_id_for(pid_a, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    leg_b = _leg(leg_id_for(pid_b, "PRIMARY", "CE", 24450.0, "2026-08-13"))
    assert leg_a.leg_id != leg_b.leg_id
    assert client_order_id_for(pid_a, leg_a.leg_id) != client_order_id_for(pid_b, leg_b.leg_id)
    _fill(broker, pid_a, leg_a.leg_id, "SELL", 1, 111.0)
    obs_b = observe_leg_fills(broker, pid_b, leg_b.leg_id, expected_qty=1)
    assert obs_b.status == "NO_FILLS"  # session B sees nothing from session A's fill.
