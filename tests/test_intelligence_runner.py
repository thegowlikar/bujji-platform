"""Intelligence Runner — unit tests. The runner is the only place
production code touches bujji.intelligence; these tests verify it never
crashes on missing data, never fabricates position-dependent readings
without a real position, and never guesses an entry IV that isn't
genuinely available."""
from datetime import datetime, timedelta

import pytest

from bujji.core.clock import IST
from bujji.core.enums import Direction, Side
from bujji.core.models import Candle, OptionContract, Position
from bujji.intelligence.runner import run_intelligence


def _candles(closes, start=datetime(2026, 7, 20, 9, 15, tzinfo=IST)):
    return [Candle(start + timedelta(minutes=5 * i), c, c + 1, c - 1, c, 1000)
            for i, c in enumerate(closes)]


def _position(entry_price=300.0, entry_spot=24000.0, strike=24000,
              entry_time=datetime(2026, 7, 20, 9, 20, tzinfo=IST), expiry="2026-07-21"):
    ce = OptionContract(symbol="NSE:CE", underlying="NIFTY", strike=strike,
                        option_type=None, expiry=expiry, lot_size=65)
    pe = OptionContract(symbol="NSE:PE", underlying="NIFTY", strike=strike,
                        option_type=None, expiry=expiry, lot_size=65)
    return Position(contract=ce, direction=list(Direction)[0], entry_side=list(Side)[0],
                    quantity=65, entry_price=entry_price, entry_spot=entry_spot,
                    entry_time=entry_time, orb=None, ce_contract=ce, pe_contract=pe)


# ---------------------------------------------------------------------- #
# No crash on the sparsest possible input
# ---------------------------------------------------------------------- #
def test_empty_everything_does_not_crash():
    r = run_intelligence([], None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, [])
    assert r["regime"]["regime"] == "UNKNOWN"
    assert r["liquidity"]["tightness"] == "UNKNOWN"
    assert r["structure"]["proximity"] == "UNKNOWN"
    assert r["event"]["expiry_proximity"] == "UNKNOWN"
    assert r["behaviour"]["data_quality"] == "INSUFFICIENT"


# ---------------------------------------------------------------------- #
# Position-dependent brains are ABSENT (not fabricated) without a
# genuine open position
# ---------------------------------------------------------------------- #
def test_no_position_omits_position_dependent_brains():
    spot_candles = _candles([24000 + i for i in range(10)])
    r = run_intelligence(spot_candles, None, datetime(2026, 7, 20, 10, 0, tzinfo=IST), None, None, [])
    assert "volatility" not in r
    assert "premium" not in r
    assert "greeks" not in r
    assert "regime" in r  # Always available -- no position needed.


def test_position_without_real_premiums_omits_position_dependent_brains():
    spot_candles = _candles([24000 + i for i in range(10)])
    pos = _position()
    r = run_intelligence(spot_candles, pos, datetime(2026, 7, 20, 10, 0, tzinfo=IST), None, None, [])
    assert "volatility" not in r
    assert "premium" not in r
    assert "greeks" not in r


def test_position_past_its_own_expiry_omits_position_dependent_brains():
    spot_candles = _candles([24000 + i for i in range(10)])
    pos = _position(expiry="2026-07-21")
    r = run_intelligence(spot_candles, pos, datetime(2026, 7, 22, 10, 0, tzinfo=IST), 100.0, 100.0, [])
    assert "volatility" not in r
    assert "premium" not in r
    assert "greeks" not in r


# ---------------------------------------------------------------------- #
# With a real position and real current premiums
# ---------------------------------------------------------------------- #
def test_real_position_populates_all_three_position_brains():
    spot_candles = _candles([24000 + i for i in range(10)])
    pos = _position()
    r = run_intelligence(spot_candles, pos, datetime(2026, 7, 20, 10, 30, tzinfo=IST), 150.0, 140.0, [])
    assert "volatility" in r and "premium" in r and "greeks" in r


def test_entry_iv_is_never_fabricated_from_combined_entry_premium():
    """Position only records a COMBINED entry premium -- there is no real
    per-leg entry price to solve an entry IV from. The Premium Brain must
    receive entry_iv=None, not a guessed 50/50 split, and must therefore
    report UNKNOWN rather than a fabricated theta-only baseline."""
    spot_candles = _candles([24000 + i for i in range(10)])
    pos = _position()
    r = run_intelligence(spot_candles, pos, datetime(2026, 7, 20, 10, 30, tzinfo=IST), 150.0, 140.0, [])
    assert r["premium"]["data_quality"] == "INSUFFICIENT"
    assert "no_entry_iv" in r["premium"]["reason"]


def test_greeks_use_current_spot_not_entry_spot():
    """A real bug caught during review: passing entry_spot as 'current
    spot' would silently compute stale Greeks. Feed a current spot far
    from entry_spot and confirm the Greeks reading reflects the move
    (position delta should shift substantially from the ATM-at-entry
    case once spot has moved well past the strike)."""
    spot_candles = _candles([24000, 24500])  # Current spot = 24500, far above entry 24000.
    pos = _position(entry_spot=24000.0, strike=24000)
    r = run_intelligence(spot_candles, pos, datetime(2026, 7, 20, 10, 30, tzinfo=IST), 550.0, 20.0, [])
    assert r["greeks"]["data_quality"] == "SUFFICIENT"
    # Spot well above strike -> short straddle should show NET_SHORT_EXPOSURE.
    assert r["greeks"]["exposure"] == "NET_SHORT_EXPOSURE"


# ---------------------------------------------------------------------- #
# Behaviour trade ordering -- journal returns most-recent-first, the
# runner must feed the brain oldest-first for streak counting to be
# meaningful
# ---------------------------------------------------------------------- #
def test_behaviour_trade_rows_are_reordered_oldest_first():
    # journal.all_trades() ordering: id DESC, i.e. most recent row first.
    trade_rows = (
        [{"daily_result": 50.0, "exit_reason": "vwap_breach"}] * 3  # Most recent 3: wins.
        + [{"daily_result": -20.0, "exit_reason": "mtm_stop"}] * 27
    )
    r = run_intelligence([], None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, trade_rows)
    assert r["behaviour"]["data_quality"] == "SUFFICIENT"
    # Oldest-first means the most recent (first 3 rows, reversed to the
    # END of the sequence) should be the winning streak.
    assert r["behaviour"]["current_streak"] == 3


def test_behaviour_missing_exit_reason_does_not_crash():
    trade_rows = [{"daily_result": 10.0, "exit_reason": None}] * 30
    r = run_intelligence([], None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, trade_rows)
    assert r["behaviour"]["data_quality"] == "SUFFICIENT"


# ---------------------------------------------------------------------- #
# Optional live-data brains are always present (null-safe) and never
# crash, whether or not real data is supplied
# ---------------------------------------------------------------------- #
def test_liquidity_and_structure_and_event_always_present_and_null_safe():
    r = run_intelligence([], None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, [])
    assert "liquidity" in r and "structure" in r and "event" in r


def test_liquidity_uses_real_data_when_provided():
    r = run_intelligence([], None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, [],
                         ce_bid=82.4, ce_ask=82.6, pe_bid=68.0, pe_ask=68.05)
    assert r["liquidity"]["tightness"] == "TIGHT"


def test_structure_uses_real_data_when_provided():
    spot_candles = _candles([24243.1])
    r = run_intelligence(spot_candles, None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, [],
                         oi_strikes=[(24100, 3940820, 17299295), (24250, 12541490, 13034125)])
    assert r["structure"]["proximity"] == "NEAR_RESISTANCE_WALL"


def test_event_uses_real_vix_when_provided():
    r = run_intelligence([], None, datetime(2026, 7, 20, 9, 15, tzinfo=IST), None, None, [],
                         vix_level=13.02, vix_prev_close=13.15)
    assert r["event"]["vix_regime"] == "MODERATE"
