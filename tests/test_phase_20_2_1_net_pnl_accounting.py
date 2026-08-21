"""Phase 20.2.1 -- Net P&L Accounting Correction. Drives a REAL
PaperBroker through real entry+exit `place_order()` calls (exactly as
`shadow_lifecycle.orchestrator` does: entry at sequence 1, exit at
sequence 2) and proves the corrected bridge/pnl pipeline produces
currency-correct, non-double-counted, entry-AND-exit-aware net P&L.
"""
from __future__ import annotations

import asyncio

import pytest

from bujji.broker.paper import PaperBroker
from bujji.broker.simulation.slippage import SlippageConfig, SlippageMode
from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.position_lifecycle.engine import build_structured_exit
from bujji.position_lifecycle.identity import leg_id_for, position_id_for
from bujji.position_lifecycle.models import LegRecord
from bujji.position_lifecycle.paper_bridge import client_order_id_for, reconcile_position_exit
from bujji.position_lifecycle.pnl import reconstruct_reference_price


def _contract():
    return OptionContract("NIFTY24450CE", "NIFTY", 24450.0, OptionType.CE, "WEEKLY", 75)


def _place(broker, position_id, leg_id, side, qty, reference_price, sequence):
    coid = client_order_id_for(position_id, leg_id, sequence)
    request = OrderRequest(
        contract=_contract(), side=Side.BUY if side == "BUY" else Side.SELL,
        quantity=qty, client_order_id=coid, reference_price=reference_price,
    )
    return asyncio.run(broker.place_order(request))


def _round_trip(entry_theoretical, exit_theoretical, quantity=100, tick_size=0.10):
    """Entry BUY at `entry_theoretical`, exit SELL at `exit_theoretical`,
    both adverse-adjusted by exactly one `tick_size` tick (FIXED_TICK,
    1 tick) -- matches this phase's own worked example: entry fills
    HIGH (100 -> 100.10), exit fills LOW (110 -> 109.90)."""
    broker = PaperBroker(slippage_config=SlippageConfig(mode=SlippageMode.FIXED_TICK, tick_size=tick_size, fixed_ticks=1))
    pid = position_id_for("S1", "C-ACCOUNTING", "t0")
    leg = LegRecord(
        leg_id=leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), role="PRIMARY",
        option_type="CE", strike=24450.0, expiry="2026-08-13",
        side="BUY", quantity=quantity, entry_premium=entry_theoretical, entry_delta=0.5,
    )
    entry_result = _place(broker, pid, leg.leg_id, "BUY", quantity, entry_theoretical, sequence=1)
    exit_result = _place(broker, pid, leg.leg_id, "SELL", quantity, exit_theoretical, sequence=2)
    return broker, pid, leg, entry_result, exit_result


# --------------------------------------------------------------------- #
# The phase's own worked example, verified end to end.
# --------------------------------------------------------------------- #
def test_worked_example_entry_and_exit_fill_prices():
    """Sanity check on the fixture itself: entry fills HIGH (BUY,
    adverse), exit fills LOW (SELL, adverse) -- exactly the phase's
    own numbers."""
    broker, pid, leg, entry_result, exit_result = _round_trip(100.0, 110.0, quantity=100)
    assert entry_result.average_price == pytest.approx(100.10)
    assert exit_result.average_price == pytest.approx(109.90)


def test_worked_example_reconstructed_exit_price_is_theoretical_not_fill():
    """`exit_prices[...]["exit_price"]` must be the THEORETICAL 110.0,
    not the slippage-adjusted fill 109.90 -- otherwise gross P&L
    silently bakes in exit slippage and double-counts it against
    `slippage` in `compute_net_pnl`."""
    broker, pid, leg, _, _ = _round_trip(100.0, 110.0, quantity=100)
    reconciliation = reconcile_position_exit(broker, pid, (leg,), exit_sequence_start=2)
    assert reconciliation.exit_prices[leg.leg_id]["exit_price"] == pytest.approx(110.0)


def test_worked_example_slippage_reflects_entry_and_exit_both():
    """Entry slippage cost = 0.10 * 100 = 10. Exit slippage cost =
    0.10 * 100 = 10. Total execution drag from slippage = 20 --
    BOTH legs represented, in currency, not the old per-unit-only,
    exit-only, blended figure."""
    broker, pid, leg, _, _ = _round_trip(100.0, 110.0, quantity=100)
    reconciliation = reconcile_position_exit(broker, pid, (leg,), exit_sequence_start=2)
    assert reconciliation.slippage == pytest.approx(20.0)


def test_worked_example_gross_pnl_is_theoretical_1000():
    broker, pid, leg, _, _ = _round_trip(100.0, 110.0, quantity=100)
    reconciliation = reconcile_position_exit(broker, pid, (leg,), exit_sequence_start=2)
    structured_exit = build_structured_exit(
        (leg,), lot_size=1, exit_timestamp="t1", exit_reason="TEST",
        exit_prices=reconciliation.exit_prices, fees=reconciliation.fees, slippage=reconciliation.slippage,
    )
    assert structured_exit.gross_realized_pnl == pytest.approx(1000.0)  # (110-100)*100*1, theoretical, no slippage baked in.


def test_worked_example_net_pnl_equals_gross_minus_fees_minus_slippage_exactly_once():
    broker, pid, leg, _, _ = _round_trip(100.0, 110.0, quantity=100)
    reconciliation = reconcile_position_exit(broker, pid, (leg,), exit_sequence_start=2)
    structured_exit = build_structured_exit(
        (leg,), lot_size=1, exit_timestamp="t1", exit_reason="TEST",
        exit_prices=reconciliation.exit_prices, fees=reconciliation.fees, slippage=reconciliation.slippage,
    )
    assert reconciliation.fees is not None and reconciliation.fees > 0
    expected_net = 1000.0 - reconciliation.fees - 20.0
    assert structured_exit.net_realized_pnl == pytest.approx(expected_net)
    # The drag between gross and net is EXACTLY fees + slippage -- no more, no less.
    assert structured_exit.gross_realized_pnl - structured_exit.net_realized_pnl == pytest.approx(reconciliation.fees + 20.0)


# --------------------------------------------------------------------- #
# reconstruct_reference_price -- the pure function directly.
# --------------------------------------------------------------------- #
def test_reconstruct_reference_price_buy_side():
    # BUY fills HIGH: fill = reference + delta -> reference = fill - delta.
    assert reconstruct_reference_price(100.10, 10.0, 100, "BUY") == pytest.approx(100.0)


def test_reconstruct_reference_price_sell_side():
    # SELL fills LOW: fill = reference - delta -> reference = fill + delta.
    assert reconstruct_reference_price(109.90, 10.0, 100, "SELL") == pytest.approx(110.0)


def test_reconstruct_reference_price_zero_slippage_returns_fill_price_unchanged():
    assert reconstruct_reference_price(150.0, None, 1, "BUY") == 150.0
    assert reconstruct_reference_price(150.0, 0.0, 1, "SELL") == 150.0


def test_reconstruct_reference_price_unknown_side_returns_fill_price_unchanged():
    assert reconstruct_reference_price(150.0, 10.0, 1, None) == 150.0


# --------------------------------------------------------------------- #
# Backward compatibility: default exit_sequence_start=1 unchanged.
# --------------------------------------------------------------------- #
def test_default_exit_sequence_start_preserves_legacy_single_fill_behavior():
    """Every pre-Phase-20.2.1 caller placed exactly ONE fill at
    sequence 1 and called `reconcile_position_exit` with no
    `exit_sequence_start` -- must behave EXACTLY as before (raw
    avg_fill_price, since no `leg_exit_sides` reconstruction applies
    without the sequence split)."""
    broker = PaperBroker()
    pid = position_id_for("S1", "C-LEGACY", "t0")
    leg = LegRecord(
        leg_id=leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), role="PRIMARY",
        option_type="CE", strike=24450.0, expiry="2026-08-13",
        side="BUY", quantity=1, entry_premium=100.0, entry_delta=0.5,
    )
    _place(broker, pid, leg.leg_id, "SELL", 1, 150.0, sequence=1)
    result = reconcile_position_exit(broker, pid, (leg,))
    assert result.exit_prices[leg.leg_id]["exit_price"] == 150.0


def test_zero_slippage_default_paperbroker_gross_pnl_unaffected_by_fix():
    """With the default (zero-slippage) `PaperBroker()`, this fix must
    change NOTHING numerically -- confirms the correction is additive,
    not a behavior change for every existing zero-slippage caller."""
    broker = PaperBroker()
    pid = position_id_for("S1", "C-ZERO", "t0")
    leg = LegRecord(
        leg_id=leg_id_for(pid, "PRIMARY", "CE", 24450.0, "2026-08-13"), role="PRIMARY",
        option_type="CE", strike=24450.0, expiry="2026-08-13",
        side="BUY", quantity=100, entry_premium=100.0, entry_delta=0.5,
    )
    _place(broker, pid, leg.leg_id, "BUY", 100, 100.0, sequence=1)
    _place(broker, pid, leg.leg_id, "SELL", 100, 110.0, sequence=2)
    reconciliation = reconcile_position_exit(broker, pid, (leg,), exit_sequence_start=2)
    structured_exit = build_structured_exit(
        (leg,), lot_size=1, exit_timestamp="t1", exit_reason="TEST",
        exit_prices=reconciliation.exit_prices, fees=reconciliation.fees, slippage=reconciliation.slippage,
    )
    assert reconciliation.exit_prices[leg.leg_id]["exit_price"] == pytest.approx(110.0)
    assert reconciliation.slippage == pytest.approx(0.0)
    assert structured_exit.gross_realized_pnl == pytest.approx(1000.0)
    assert structured_exit.net_realized_pnl == pytest.approx(1000.0 - reconciliation.fees)
