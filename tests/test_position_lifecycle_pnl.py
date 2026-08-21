"""Tests -- Phase 15K P&L Engine (bujji.position_lifecycle.pnl). Pure
fixtures, no broker, no execution. Sign correctness is the most
critical property tested here."""
from __future__ import annotations

from bujji.position_lifecycle.models import PNL_COMPLETE, PNL_PARTIAL, PNL_UNKNOWN
from bujji.position_lifecycle.pnl import compute_leg_gross_pnl, compute_net_pnl, compute_position_gross_pnl


# ---------------------------------------------------------------------------
# Sign correctness -- long call/put, short call/put.
# ---------------------------------------------------------------------------
def test_long_call_profit():
    # bought at 100, sold at 150 -- long profits when price rises.
    pnl, status = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 150.0)
    assert status == PNL_COMPLETE
    assert pnl == 50.0 * 75


def test_long_call_loss():
    pnl, status = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 60.0)
    assert pnl == -40.0 * 75


def test_short_call_profit():
    # sold at 100, bought back at 60 -- short profits when price falls.
    pnl, status = compute_leg_gross_pnl("SELL", 1, 75, 100.0, 60.0)
    assert pnl == 40.0 * 75


def test_short_call_loss():
    pnl, status = compute_leg_gross_pnl("SELL", 1, 75, 100.0, 150.0)
    assert pnl == -50.0 * 75


def test_long_put_profit_and_loss_same_formula_as_call():
    """A long PUT's P&L formula is IDENTICAL to a long CALL's --
    direction is captured entirely by option_type at the strike-
    selection level, not by this module (which only knows BUY/SELL)."""
    profit, _ = compute_leg_gross_pnl("BUY", 1, 75, 50.0, 80.0)
    loss, _ = compute_leg_gross_pnl("BUY", 1, 75, 50.0, 20.0)
    assert profit == 30.0 * 75
    assert loss == -30.0 * 75


def test_short_put_profit_and_loss():
    profit, _ = compute_leg_gross_pnl("SELL", 1, 75, 50.0, 20.0)
    loss, _ = compute_leg_gross_pnl("SELL", 1, 75, 50.0, 80.0)
    assert profit == 30.0 * 75
    assert loss == -30.0 * 75


# ---------------------------------------------------------------------------
# Quantity/multiplier scaling -- never double-signed.
# ---------------------------------------------------------------------------
def test_quantity_scales_linearly():
    pnl_1, _ = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 110.0)
    pnl_3, _ = compute_leg_gross_pnl("BUY", 3, 75, 100.0, 110.0)
    assert pnl_3 == pnl_1 * 3


def test_multiplier_scales_linearly():
    pnl_75, _ = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 110.0)
    pnl_150, _ = compute_leg_gross_pnl("BUY", 1, 150, 100.0, 110.0)
    assert pnl_150 == pnl_75 * 2


def test_zero_movement_is_zero_pnl_not_unknown():
    pnl, status = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 100.0)
    assert status == PNL_COMPLETE
    assert pnl == 0.0


# ---------------------------------------------------------------------------
# Zero/negative/invalid quantity -- rejected, never fabricated.
# ---------------------------------------------------------------------------
def test_zero_quantity_is_unknown():
    pnl, status = compute_leg_gross_pnl("BUY", 0, 75, 100.0, 110.0)
    assert status == PNL_UNKNOWN
    assert pnl is None


def test_negative_quantity_is_unknown():
    pnl, status = compute_leg_gross_pnl("BUY", -1, 75, 100.0, 110.0)
    assert status == PNL_UNKNOWN


def test_zero_multiplier_is_unknown():
    pnl, status = compute_leg_gross_pnl("BUY", 1, 0, 100.0, 110.0)
    assert status == PNL_UNKNOWN


def test_invalid_side_is_unknown():
    pnl, status = compute_leg_gross_pnl("HOLD", 1, 75, 100.0, 110.0)
    assert status == PNL_UNKNOWN


# ---------------------------------------------------------------------------
# Missing evidence -- never fabricated.
# ---------------------------------------------------------------------------
def test_missing_exit_price_is_unknown():
    pnl, status = compute_leg_gross_pnl("BUY", 1, 75, 100.0, None)
    assert status == PNL_UNKNOWN
    assert pnl is None


def test_missing_entry_price_is_unknown():
    pnl, status = compute_leg_gross_pnl("BUY", 1, 75, None, 110.0)
    assert status == PNL_UNKNOWN


def test_missing_multiplier_is_unknown_never_assumes_one():
    pnl, status = compute_leg_gross_pnl("BUY", 1, None, 100.0, 110.0)
    assert status == PNL_UNKNOWN
    assert pnl is None  # NEVER computed as if multiplier=1.


def test_missing_quantity_is_unknown_never_assumes_one():
    pnl, status = compute_leg_gross_pnl("BUY", None, 75, 100.0, 110.0)
    assert status == PNL_UNKNOWN


# ---------------------------------------------------------------------------
# Multi-leg aggregation -- position P&L == sum(leg P&L), only when ALL legs resolve.
# ---------------------------------------------------------------------------
def test_multi_leg_iron_condor_all_legs_known():
    short_call, _ = compute_leg_gross_pnl("SELL", 1, 75, 40.0, 20.0)   # +20*75
    long_call, _ = compute_leg_gross_pnl("BUY", 1, 75, 15.0, 5.0)      # -10*75
    short_put, _ = compute_leg_gross_pnl("SELL", 1, 75, 35.0, 15.0)    # +20*75
    long_put, _ = compute_leg_gross_pnl("BUY", 1, 75, 10.0, 2.0)       # -8*75
    leg_pnls = [(short_call, PNL_COMPLETE), (long_call, PNL_COMPLETE), (short_put, PNL_COMPLETE), (long_put, PNL_COMPLETE)]
    total, status = compute_position_gross_pnl(leg_pnls)
    assert status == PNL_COMPLETE
    assert total == short_call + long_call + short_put + long_put
    assert total == (20 - 10 + 20 - 8) * 75


def test_multi_leg_straddle_mixed_directions():
    call_leg, _ = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 130.0)   # +30*75
    put_leg, _ = compute_leg_gross_pnl("BUY", 1, 75, 90.0, 60.0)      # -30*75
    total, status = compute_position_gross_pnl([(call_leg, PNL_COMPLETE), (put_leg, PNL_COMPLETE)])
    assert status == PNL_COMPLETE
    assert total == 0.0  # a real straddle scenario -- one leg's gain offsets the other's loss exactly.


def test_partial_leg_information_never_produces_a_false_total():
    """One leg known, one leg's exit price missing -- the POSITION
    total must stay UNKNOWN (PARTIAL), never silently understate real
    exposure by summing only the known leg."""
    known, _ = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 130.0)
    unknown, unknown_status = compute_leg_gross_pnl("BUY", 1, 75, 100.0, None)
    total, status = compute_position_gross_pnl([(known, PNL_COMPLETE), (unknown, unknown_status)])
    assert status == PNL_PARTIAL
    assert total is None


def test_no_legs_at_all_is_unknown():
    total, status = compute_position_gross_pnl([])
    assert status == PNL_UNKNOWN
    assert total is None


def test_asymmetric_leg_quantities():
    leg_a, _ = compute_leg_gross_pnl("SELL", 1, 75, 40.0, 20.0)
    leg_b, _ = compute_leg_gross_pnl("SELL", 2, 75, 35.0, 15.0)  # a real 2-lot leg, e.g. a ratio spread.
    total, status = compute_position_gross_pnl([(leg_a, PNL_COMPLETE), (leg_b, PNL_COMPLETE)])
    assert status == PNL_COMPLETE
    assert total == (20 * 75) + (20 * 2 * 75)


# ---------------------------------------------------------------------------
# Net P&L -- fees/slippage genuinely UNKNOWN, never assumed zero.
# ---------------------------------------------------------------------------
def test_net_pnl_known_when_all_inputs_real():
    net = compute_net_pnl(1000.0, 20.0, 5.0)
    assert net == 975.0


def test_net_pnl_unknown_when_fees_missing():
    assert compute_net_pnl(1000.0, None, 5.0) is None


def test_net_pnl_unknown_when_slippage_missing():
    assert compute_net_pnl(1000.0, 20.0, None) is None


def test_net_pnl_unknown_when_gross_missing():
    assert compute_net_pnl(None, 20.0, 5.0) is None


def test_gross_known_even_when_net_unknown():
    """Step 4's explicit requirement: gross_realized_pnl may be known
    even when net_realized_pnl is not."""
    gross, status = compute_leg_gross_pnl("BUY", 1, 75, 100.0, 130.0)
    net = compute_net_pnl(gross, None, None)  # fees/slippage genuinely unavailable.
    assert gross is not None
    assert net is None


# ---------------------------------------------------------------------------
# P&L direction must NEVER be inferred from thesis status -- pure
# arithmetic only, no coupling to any thesis/decision concept.
# ---------------------------------------------------------------------------
def test_pnl_module_has_no_thesis_or_decision_coupling():
    import inspect
    from bujji.position_lifecycle import pnl as pnl_module
    source = inspect.getsource(pnl_module)
    for forbidden in ("thesis", "THESIS", "recommendation", "regime", "direction_band"):
        assert forbidden not in source, f"pnl.py unexpectedly references {forbidden!r} -- P&L must be pure arithmetic only"
