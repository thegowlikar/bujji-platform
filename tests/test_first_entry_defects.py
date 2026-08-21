"""Three defects found in the first live session that ever reached Gate B.

2026-08-20, cycle 66. Every stage cleared -- capture, stability gate, thesis,
three-part selection, strike construction on a live chain, 82/82 quote
coverage, risk budget -- and the entry was refused at the margin gate with
MARGIN_NOT_CERTIFIED.

The margin API was never broken. Measured live:

    'NIFTY2026-08-2524500CE'  (internal)  -> verified=False  total=None
    'NSE:NIFTY26AUG22700PE'   (broker)    -> verified=True   total=98915.87

Gate B was sending the INTERNAL contract identity to FYERS. Every entry in
Bujji's history was blocked by a symbol format. The same trap D-7 fixed for
the quote sync -- the chain speaks broker symbols and the internal contract
does not -- was here too, unnoticed, because nothing had ever reached it.
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.options_observation.engine import build_option_observation
from bujji.trading_brain.risk_governor.msi_entry_bridge import _leg_to_core_contract
from bujji_options_os_runner import _entry_failure_reason

GATE_B_SRC = (REPO_ROOT / "bujji" / "production_runtime" / "trading_brain_runtime.py").read_text()


@dataclass(frozen=True)
class _Leg:
    strike: float = 24500.0
    expiry: str = "2026-08-25"
    option_type: str = "CE"
    ratio: int = 1
    premium: float = 26.0
    role: str = "SHORT"
    side: str = "SELL"


def _chain_row(strike=24500.0, option_type="CE", symbol="NSE:NIFTY26AUG24500CE"):
    return build_option_observation(
        underlying="NIFTY", instrument_symbol=symbol, strike=strike,
        expiry="2026-08-25", option_type=option_type, exchange="NSE", segment="FO",
        timestamp="T", resolution="SNAPSHOT", open_=None, high=None, low=None,
        close=26.0, settlement=None, volume=1, open_interest=1,
        change_in_open_interest=0, underlying_price=24000.0, origin="test",
        acquisition_timestamp="T", normalization_timestamp="T", bid=25.5, ask=26.5,
        # "NSE:NIFTY26AUG24500CE" is the FYERS form -- this fixture exists
        # precisely to stand for the BROKER's own symbol.
        symbol_provenance="BROKER_AUTHORITATIVE")


class TestTheSymbolTrap:
    def test_the_internal_and_broker_symbols_really_do_differ(self):
        """Pins WHY the bug existed. If these ever converge, the fix below
        stops being load-bearing and someone should know."""
        internal = _leg_to_core_contract(_Leg(), "NIFTY", 65).symbol
        broker = _chain_row().instrument_symbol
        assert internal != broker
        assert not internal.startswith("NSE:")
        assert broker.startswith("NSE:")

    def test_the_chain_row_carries_the_broker_symbol(self):
        """The fix looks the symbol up rather than rebuilding it -- a second
        string-builder would be a second thing to drift."""
        assert _chain_row().instrument_symbol == "NSE:NIFTY26AUG24500CE"

    def test_gate_b_no_longer_sends_the_internal_contract_symbol(self):
        assert "symbol=contract.symbol" not in GATE_B_SRC

    def test_gate_b_looks_the_symbol_up_from_the_chain(self):
        """The lookup moved into option_symbol_resolver (2026-08-21) and is
        now SHARED with order construction, so this asserts the delegation
        rather than the old inline map -- the invariant is unchanged."""
        assert "symbol_index.resolve_leg(leg)" in GATE_B_SRC
        assert "symbol=broker_symbol" in GATE_B_SRC

    def test_gate_b_fails_closed_when_a_leg_cannot_be_resolved(self):
        """Falling back to the internal symbol is exactly the defect, and it
        would be invisible: the call simply returns nothing usable."""
        assert "OptionSymbolUnresolvable" in GATE_B_SRC
        assert "unresolved" in GATE_B_SRC


class TestTheEntryFailureReason:
    """A report that misstates the stage sends the next investigation to the
    wrong module. It cost an hour on 2026-08-20."""

    @dataclass
    class _Result:
        blocking_reason: object = None
        governor_result: object = None
        proposal: object = None

    def test_a_gate_b_veto_is_reported_as_a_gate_b_veto(self):
        """The exact regression: construction SUCCEEDED, Gate B refused, and
        the log said 'not constructed'."""
        r = self._Result(blocking_reason="GATE_B_MARGIN_NOT_CERTIFIED", proposal=object())
        assert _entry_failure_reason(r) == "GATE_B_MARGIN_NOT_CERTIFIED"

    def test_a_governor_block_still_reports_its_stage(self):
        @dataclass
        class _Gov:
            blocking_stage: str = "PORTFOLIO_LIMITS"

        r = self._Result(governor_result=_Gov(), proposal=object())
        assert _entry_failure_reason(r) == "PORTFOLIO_LIMITS"

    def test_blocking_reason_wins_over_the_governor_stage(self):
        """It is the most specific thing the runtime can say."""
        @dataclass
        class _Gov:
            blocking_stage: str = "SOMETHING_GENERIC"

        r = self._Result(blocking_reason="GATE_B_MARGIN_DATA_MISSING",
                         governor_result=_Gov(), proposal=object())
        assert _entry_failure_reason(r) == "GATE_B_MARGIN_DATA_MISSING"

    def test_a_genuinely_unconstructed_proposal_still_says_so(self):
        assert _entry_failure_reason(self._Result()) == "not constructed"

    def test_a_constructed_proposal_with_no_reason_is_not_called_unconstructed(self):
        r = self._Result(proposal=object())
        assert "not constructed" not in _entry_failure_reason(r)

    def test_no_cycle_result_is_its_own_answer(self):
        assert _entry_failure_reason(None) == "no cycle result"


class TestTheSpotSnapshotUnwrap:
    """PRICE LEVELS -- context build failed (float() argument must be a string
    or a real number, not 'SpotSnapshot'). Non-fatal and observation-only, so
    it degraded silently and L-5 recorded nothing for the entire session."""

    def test_market_snapshot_spot_is_an_object_not_a_number(self):
        from bujji.market_perception.models import SpotSnapshot

        snap = SpotSnapshot(symbol="NSE:NIFTY50-INDEX", ltp=24100.0)
        with pytest.raises(TypeError):
            float(snap)
        assert float(snap.ltp) == 24100.0

    def test_the_runner_unwraps_ltp_rather_than_passing_the_object(self):
        source = (REPO_ROOT / "bujji_options_os_runner.py").read_text()
        assert 'getattr(spot_snapshot, "ltp", None)' in source
        assert 'self._last_spot = getattr(snapshots[-1], "spot", None)' not in source
