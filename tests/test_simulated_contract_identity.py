"""A simulator must not manufacture a venue symbol.

PaperBroker.resolve_atm_contract returned f"{underlying}{strike}{opt.value}"
-- "NIFTY24500CE" -- and stamped the contract's expiry as the literal
"WEEKLY". Two defects wearing one shape:

  1. NO EXPIRY. A strike alone is not a contract. Every ledger PaperBroker
     owns keys on the symbol string, and the position row has no expiry
     field, so two contracts differing only in expiry MERGE.
  2. VENUE-SHAPED, AND NOT A VENUE SYMBOL. A real FYERS symbol is
     "NSE:NIFTY26AUG24500CE". "NIFTY24500CE" can never equal one, while
     looking enough like one that a comparison silently matches nothing.

The fix reuses the identity this codebase already owns for "no broker symbol
was available": bujji.options_observation.taxonomy.unresolved_symbol().
"""
from __future__ import annotations

import ast
import asyncio
import inspect

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import Direction, OptionType, Side
from bujji.core.models import OrderRequest
from bujji.options_observation import taxonomy as opt_taxonomy

NEAR, FAR = "2026-08-27", "2026-09-24"


def _resolve(broker, direction=Direction.BEARISH, spot=24512.0):
    return asyncio.run(broker.resolve_atm_contract("NIFTY", spot, direction, 50, 75))


def _connected(**kw):
    b = PaperBroker(**kw)
    asyncio.run(b.connect())
    return b


# --------------------------------------------------------------------------
# The expiry is carried, and it is never invented.
# --------------------------------------------------------------------------

def test_the_identity_carries_the_expiry_it_was_given():
    contract = _resolve(_connected(simulated_expiry=NEAR))
    assert contract.symbol == f"UNRESOLVED|NIFTY|{NEAR}|24500|CE"
    assert contract.expiry == NEAR


def test_two_expiries_no_longer_produce_the_same_identity():
    """THE defect, stated as behaviour."""
    near = _resolve(_connected(simulated_expiry=NEAR))
    far = _resolve(_connected(simulated_expiry=FAR))
    assert near.symbol != far.symbol
    assert near.strike == far.strike and near.option_type is far.option_type


def test_an_unsupplied_expiry_says_so_rather_than_claiming_weekly():
    """A simulator does not know which expiry is listed -- that answer is in
    the instrument master, a network download it must not make. "WEEKLY" was
    an assertion about a contract class it never established."""
    contract = _resolve(_connected())
    assert contract.expiry == opt_taxonomy.UNRESOLVED_EXPIRY
    assert contract.expiry != "WEEKLY"
    assert opt_taxonomy.UNRESOLVED_EXPIRY in contract.symbol


def test_a_calendar_spread_no_longer_nets_to_no_position():
    """Short the near leg, long the far leg, same strike. Before the fix both
    legs keyed the same ledger row and the broker reported NO POSITION AT
    ALL -- a phantom flat manufactured by the simulator itself."""
    broker = _connected(simulated_expiry=NEAR)
    near = _resolve(broker)
    broker._simulated_expiry = FAR
    far = _resolve(broker)

    asyncio.run(broker.place_order(OrderRequest(near, Side.SELL, 75, client_order_id="A")))
    asyncio.run(broker.place_order(OrderRequest(far, Side.BUY, 75, client_order_id="B")))
    positions = asyncio.run(broker.get_open_positions())

    assert len(positions) == 2, (
        f"the two legs merged into {positions!r} -- a position that exists "
        f"reported as flat")
    assert {p["side"] for p in positions} == {"SELL", "BUY"}


# --------------------------------------------------------------------------
# It can never be mistaken for something tradable.
# --------------------------------------------------------------------------

def test_the_identity_is_the_absent_sentinel_this_codebase_already_owns():
    contract = _resolve(_connected(simulated_expiry=NEAR))
    assert contract.symbol.startswith(opt_taxonomy.UNRESOLVED_SYMBOL_PREFIX)
    assert contract.symbol == opt_taxonomy.unresolved_symbol("NIFTY", NEAR, 24500, "CE"), (
        "the simulator must reuse the owned constructor, not grow a second one")


@pytest.mark.parametrize("venue_symbol", [
    "NSE:NIFTY26AUG24500CE",     # monthly, from the real FYERS instrument master
    "NSE:NIFTY2690124500CE",     # weekly
    "NIFTY26AUG24500CE",         # NSE bhavcopy form
])
def test_it_cannot_equal_a_real_venue_symbol(venue_symbol):
    symbol = _resolve(_connected(simulated_expiry=NEAR)).symbol
    assert symbol != venue_symbol
    assert "|" in symbol, (
        "the pipe is what makes this safe: no exchange vocabulary in use "
        "here contains one, so this can never be accidentally matched "
        "against a tradable identity")
    assert "|" not in venue_symbol


def test_the_contract_parser_refuses_to_read_it_as_a_tradable_contract():
    """`_parse_identity` reads NIFTY|expiry|strike|CE. The sentinel has a
    fifth field, so it parses as None -- "a stray identity in the store can
    never be mistaken for an option contract"."""
    from bujji.production_runtime.store_chain_provider import _parse_identity

    assert _parse_identity(f"NIFTY|{NEAR}|24500|CE") is not None
    assert _parse_identity(_resolve(_connected(simulated_expiry=NEAR)).symbol) is None


def test_absent_provenance_is_not_usable_for_an_order_anywhere():
    """The sentinel names nothing real, and the one place a broker symbol is
    obtained refuses that class of identity permanently."""
    from bujji.production_runtime.option_symbol_resolver import USABLE_PROVENANCES

    assert opt_taxonomy.SYMBOL_PROVENANCE_ABSENT not in USABLE_PROVENANCES
    assert opt_taxonomy.SYMBOL_PROVENANCE_SYNTHETIC not in USABLE_PROVENANCES


# --------------------------------------------------------------------------
# The old construction is gone -- structurally, not by substring.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("module_name", [
    "bujji.broker.paper",
    "bujji.replay.broker",
])
def test_no_simulator_builds_a_venue_shaped_symbol_from_parts(module_name):
    """AST, not substring: a substring check would accept the construction
    sitting inside a disabled branch, and this repository has been bitten by
    exactly that three times.

    Flags any f-string that joins an `underlying`-ish name directly to a
    strike or option type WITHOUT a pipe -- which is what makes a string
    venue-shaped rather than an internal identity.
    """
    module = __import__(module_name, fromlist=["_"])
    tree = ast.parse(inspect.getsource(module))

    offenders = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.JoinedStr):
            continue
        names = [n.value.id for n in node.values
                 if isinstance(n, ast.FormattedValue) and isinstance(n.value, ast.Name)]
        attrs = [n.value.attr for n in node.values
                 if isinstance(n, ast.FormattedValue) and isinstance(n.value, ast.Attribute)]
        literals = "".join(n.value for n in node.values
                           if isinstance(n, ast.Constant) and isinstance(n.value, str))
        if "|" in literals:
            continue        # an internal identity, deliberately excluded
        has_underlying = any("underlying" in n for n in names)
        has_contract_part = (any(n in ("strike", "expiry") for n in names)
                             or any(a in ("value", "strike", "expiry") for a in attrs))
        if has_underlying and has_contract_part:
            offenders.append(ast.unparse(node))

    assert offenders == [], (
        f"{module_name} builds a venue-shaped symbol from parts: {offenders}")


def test_the_taxonomy_owns_the_expiry_sentinel():
    """One definition, not a literal repeated at each simulator."""
    assert opt_taxonomy.UNRESOLVED_EXPIRY
    assert "|" not in opt_taxonomy.UNRESOLVED_EXPIRY, (
        "a pipe here would split the identity into a fifth field")


# --------------------------------------------------------------------------
# The detector, and the module it could not see.
#
# THESE EXIST BECAUSE THE ARCHITECTURE RATCHET DOES NOT COVER THEM. Its
# dormant-module test only fires when a quarantined module becomes REACHABLE;
# an undeclared DORMANT violation breaks nothing. Both negative controls aimed
# at the ratchet came back silent, which means the ratchet was never the guard
# here. These are.
# --------------------------------------------------------------------------

def _detector():
    import importlib.util, pathlib as _p
    spec = importlib.util.spec_from_file_location(
        "fp", _p.Path(__file__).resolve().parent.parent / "tools" / "forbidden_patterns.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.PATTERNS["broker-symbol-built"]


@pytest.mark.parametrize("source, should_flag, why", [
    ('f"{underlying}{strike}{opt.value}"', True, "the original paper builder"),
    ('f"{underlying_symbol}{int(leg.strike)}{leg.option_type}"', True,
     "THE BLIND SPOT: same defect, one identifier longer"),
    ('f"{underlying}{leg.expiry}{int(leg.strike)}{leg.option_type}"', True,
     "carries an expiry and is still a manufactured venue symbol"),
    ('f"NSE:{underlying}{expiry_code}{strike}{opt}"', True, "with an exchange prefix"),
    ('f"{UNRESOLVED_SYMBOL_PREFIX}{underlying}|{expiry}|{strike}|{option_type}"', False,
     "the pipe-delimited internal identity is deliberately excluded"),
    ('f"{underlying} spot is {spot}"', False, "no contract part -- not a symbol"),
])
def test_the_detector_matches_the_shape_not_the_variable_name(source, should_flag, why):
    """A detector keyed to the literal name `underlying` is defeated by a
    rename -- the single most likely accidental edit. It missed
    shadow_lifecycle/orchestrator.py for exactly that reason."""
    assert bool(_detector().search(source)) is should_flag, why


def test_the_shadow_lifecycle_contract_uses_the_legs_own_expiry():
    """Its docstring claimed it invented nothing while stamping the literal
    "WEEKLY" and discarding `leg.expiry`, which sat right there carrying the
    real, already-solved value."""
    from bujji.shadow_lifecycle.orchestrator import _contract_for_leg

    class _Leg:
        option_type = "CE"
        strike = 24500.0
        expiry = "2026-08-27"

    contract = _contract_for_leg(_Leg(), "NIFTY", 75)
    assert contract.expiry == "2026-08-27", "the leg's own expiry was discarded"
    assert contract.symbol == opt_taxonomy.unresolved_symbol("NIFTY", "2026-08-27", 24500, "CE")
    assert "WEEKLY" not in contract.symbol


def test_a_leg_with_no_expiry_still_never_claims_one():
    from bujji.shadow_lifecycle.orchestrator import _contract_for_leg

    class _Leg:
        option_type = "PE"
        strike = 24000.0

    contract = _contract_for_leg(_Leg(), "NIFTY", 75)
    assert contract.expiry == opt_taxonomy.UNRESOLVED_EXPIRY


# --------------------------------------------------------------------------
# The sentinel can be recognised, not merely produced.
# --------------------------------------------------------------------------

def test_the_sentinel_has_a_predicate():
    """A sentinel with no predicate is a convention, and a convention is what
    the next reader is free to not know about."""
    simulated = _resolve(_connected(simulated_expiry=NEAR)).symbol
    assert opt_taxonomy.is_unresolved_symbol(simulated)
    for real in ("NSE:NIFTY26AUG24500CE", "NIFTY26AUG24500CE"):
        assert not opt_taxonomy.is_unresolved_symbol(real)
    assert not opt_taxonomy.is_unresolved_symbol("")
    assert not opt_taxonomy.is_unresolved_symbol(None)
