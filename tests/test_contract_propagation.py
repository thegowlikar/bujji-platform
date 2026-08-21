"""Post-order code consumes the contract the broker was handed, never a rebuild.

THE INVARIANT. After an order is constructed, no production component may
derive broker identity from strategy-leg fields when the actual
OrderRequest.contract is available.

WHY A KEYED MAP, NOT POSITION. The runner paired legs to results with
`zip(proposal.legs, order_results)`. That is WRONG on a reachable path: when
SUBMIT_INTENT cannot be journaled, journaled_entry records the pair in
`unfilled_pairs` and `continue`s WITHOUT appending to `results`. So
len(order_results) < len(proposal.legs) is reachable, and the zip pairs
leg[0] with results[1] -- one leg's contract against another leg's fill.
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from bujji.core.enums import OptionType, Side
from bujji.core.models import OptionContract, OrderRequest
from bujji.production_runtime.trading_brain_runtime import (
    TradingBrainCycleResult, _contracts_by_coid)

RUNNER = (REPO_ROOT / "bujji_options_os_runner.py").read_text()


def _contract(symbol, strike=24050.0, opt=OptionType.PE, expiry="2026-08-25"):
    return OptionContract(symbol=symbol, underlying="NIFTY", strike=strike,
                          option_type=opt, expiry=expiry, lot_size=65)


def _request(coid, contract):
    return OrderRequest(contract=contract, side=Side.SELL, quantity=65,
                        client_order_id=coid, limit_price=None,
                        reference_price=120.0, tag="t")


def _result(order_contracts):
    return TradingBrainCycleResult(
        proposal=None, governor_result=None, context_unavailable=None,
        order_results=(), approved_quantity=1, filled=True, blocking_reason=None,
        order_contracts=order_contracts)


class TestTheContractSurvivesUnchanged:
    def test_a_ce_contract_survives(self):
        c = _contract("NSE:NIFTY2682524450CE", 24450.0, OptionType.CE)
        r = _result(_contracts_by_coid([_request("A-LEG-0", c)]))
        assert r.contract_for("A-LEG-0") is c

    def test_a_pe_contract_survives(self):
        c = _contract("NSE:NIFTY2682524050PE", 24050.0, OptionType.PE)
        r = _result(_contracts_by_coid([_request("A-LEG-0", c)]))
        assert r.contract_for("A-LEG-0") is c

    def test_a_non_synthetic_broker_symbol_survives_verbatim(self):
        """The whole point: a real FYERS symbol must not be normalised,
        rebuilt, or reformatted on the way through."""
        c = _contract("NSE:NIFTY2682524050PE")
        r = _result(_contracts_by_coid([_request("A-LEG-0", c)]))
        assert r.contract_for("A-LEG-0").symbol == "NSE:NIFTY2682524050PE"


class TestNoMisassociation:
    def test_ce_and_pe_cannot_swap(self):
        ce = _contract("NSE:...24450CE", 24450.0, OptionType.CE)
        pe = _contract("NSE:...24050PE", 24050.0, OptionType.PE)
        r = _result(_contracts_by_coid([_request("A-LEG-0", ce), _request("A-LEG-1", pe)]))
        assert r.contract_for("A-LEG-0") is ce
        assert r.contract_for("A-LEG-1") is pe

    def test_different_strikes_stay_distinct(self):
        a = _contract("S-24000", 24000.0)
        b = _contract("S-24100", 24100.0)
        r = _result(_contracts_by_coid([_request("A-LEG-0", a), _request("A-LEG-1", b)]))
        assert r.contract_for("A-LEG-0").strike == 24000.0
        assert r.contract_for("A-LEG-1").strike == 24100.0

    def test_different_expiries_stay_distinct(self):
        near = _contract("NEAR", expiry="2026-08-25")
        far = _contract("FAR", expiry="2026-09-01")
        r = _result(_contracts_by_coid([_request("A-LEG-0", near), _request("A-LEG-1", far)]))
        assert r.contract_for("A-LEG-0").expiry == "2026-08-25"
        assert r.contract_for("A-LEG-1").expiry == "2026-09-01"

    def test_a_missing_leg_cannot_shift_the_association(self):
        """THE reachable defect. If leg 0's SUBMIT_INTENT fails to journal it
        never enters order_results, and a positional pairing would hand leg
        0's slot to leg 1's result. A keyed lookup cannot."""
        ce = _contract("CE-SYM", 24450.0, OptionType.CE)
        pe = _contract("PE-SYM", 24050.0, OptionType.PE)
        r = _result(_contracts_by_coid([_request("A-LEG-0", ce), _request("A-LEG-1", pe)]))
        # Only leg 1 produced a result -- leg 0 was skipped before placement.
        surviving_coid = "A-LEG-1"
        assert r.contract_for(surviving_coid) is pe, "the surviving result got the wrong contract"

    def test_an_unknown_coid_returns_none_not_a_guess(self):
        r = _result(_contracts_by_coid([_request("A-LEG-0", _contract("S"))]))
        assert r.contract_for("A-LEG-9") is None


class TestNoPostOrderReconstruction:
    def test_the_runner_never_calls_leg_to_core_contract(self):
        """The invariant, asserted structurally."""
        calls = [n.lineno for n in ast.walk(ast.parse(RUNNER))
                 if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
                 and n.func.id == "_leg_to_core_contract"]
        assert calls == [], f"post-order reconstruction remains at lines {calls}"

    def test_registration_uses_contract_for(self):
        fn = next(n for n in ast.walk(ast.parse(RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_attempt_entry")
        assert "contract_for" in ast.unparse(fn)

    def test_orphan_registration_uses_the_ordered_contracts(self):
        fn = next(n for n in ast.walk(ast.parse(RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_register_orphaned_legs")
        body = ast.unparse(fn)
        assert "contract_for" in body
        assert "order_contracts" in body

    def test_no_site_pairs_legs_to_results_positionally(self):
        assert "zip(cycle_result.proposal.legs, cycle_result.order_results)" not in RUNNER


class TestMissingPropagationIsLoudNotSilent:
    def test_registration_escalates_on_a_missing_contract(self):
        fn = next(n for n in ast.walk(ast.parse(RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_attempt_entry")
        body = ast.unparse(fn)
        assert "CONTRACT PROPAGATION MISSING" in body

    def test_the_orphan_path_escalates_too(self):
        """The enclosing bare except is documented 'Never raises'. A missing
        contract must therefore be reported explicitly, or it is swallowed and
        an unmanaged position is left with no record."""
        fn = next(n for n in ast.walk(ast.parse(RUNNER))
                  if isinstance(n, ast.FunctionDef) and n.name == "_register_orphaned_legs")
        body = ast.unparse(fn)
        assert "ORPHAN CONTRACT PROPAGATION MISSING" in body

    def test_a_missing_contract_never_falls_back_to_a_rebuild(self):
        for name in ("_attempt_entry", "_register_orphaned_legs"):
            fn = next(n for n in ast.walk(ast.parse(RUNNER))
                      if isinstance(n, ast.FunctionDef) and n.name == name)
            assert "_leg_to_core_contract" not in ast.unparse(fn)


class TestTheNoOrderPathCarriesNothing:
    def test_an_empty_map_yields_none(self):
        """The JOURNAL_UNAVAILABLE path places no order, so there is no
        authoritative contract. Empty is the honest value."""
        assert _result(()).contract_for("anything") is None

    def test_contracts_by_coid_tolerates_no_requests(self):
        assert _contracts_by_coid(None) == ()
        assert _contracts_by_coid([]) == ()
