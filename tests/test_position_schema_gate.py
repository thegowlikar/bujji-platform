"""`FYERS_POSITION_SCHEMA_VERIFIED` must be a gate, not a comment.

The flag's own definition says: "This flag exists so that fact is a gate
rather than a comment." It was a comment. A sweep of every non-test reference
found exactly two -- the definition and the class attribute mirroring it. No
production code consulted it, so the safeguard against manufactured flatness
guarded nothing. (Positive control for that sweep: the same grep shape finds
real consumers for `disable_live_execution` and `order_book_survives_restart`.)

WHY IT MATTERS, in the flag's own words: `get_open_positions()` reads `netQty`
and `symbol`. "If `netQty` were actually named something else, each row would
read as qty 0, every position would be filtered out as flat, and the account
would look EMPTY. That failure is silent and points the wrong way: it
manufactures flatness."

For a system that SELLS options, believing you are flat when you are short is
the worst available error. So: no real order may be placed while the system
cannot verify what it holds.
"""
import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.errors import UnverifiedPositionSchemaError  # noqa: E402
from bujji.broker.fyers import (  # noqa: E402
    FYERS_POSITION_SCHEMA_VERIFIED, FyersBroker,
)


def _bare_broker():
    """A FyersBroker with NO config, NO client, NO network.

    Constructed via __new__ deliberately: if the gate fires on this, it must
    run before anything that needs a client, which is the whole point.
    """
    return FyersBroker.__new__(FyersBroker)


class TestTheFlagItself:
    def test_the_flag_is_still_false(self):
        """Pinned. Flipping this is an operator action taken against a REAL
        observed position -- never a code change made to get a test green."""
        assert FYERS_POSITION_SCHEMA_VERIFIED is False
        assert FyersBroker.position_schema_verified is False


class TestTheGate:
    def test_place_order_refuses_while_the_schema_is_unverified(self):
        with pytest.raises(UnverifiedPositionSchemaError) as exc:
            asyncio.run(_bare_broker().place_order(object()))
        message = str(exc.value)
        assert "FYERS_POSITION_SCHEMA_VERIFIED" in message
        assert "manufactures flatness" in message

    def test_the_gate_fires_before_any_network_call(self):
        """No config, no client, no token -- so nothing was contacted.

        If the gate ran AFTER the SDK call it would raise AttributeError or
        TypeError here instead, and a real order might already be live.
        """
        broker = _bare_broker()
        assert not hasattr(broker, "_client")
        with pytest.raises(UnverifiedPositionSchemaError):
            asyncio.run(broker.place_order(object()))

    def test_a_verified_schema_lets_the_order_through(self, monkeypatch):
        """The gate must be a gate, not a wall: once an operator has verified
        the payload, orders proceed."""
        broker = _bare_broker()
        monkeypatch.setattr(broker, "position_schema_verified", True, raising=False)
        seen = {}

        async def _fake_call(method, **kwargs):
            seen["method"] = method
            return {"s": "ok", "id": "X1"}

        monkeypatch.setattr(broker, "_call", _fake_call, raising=False)
        monkeypatch.setattr(broker, "_raise_if_auth_error", lambda _d: None, raising=False)
        monkeypatch.setattr(broker, "_map_order",
                            lambda cid, _d: type("R", (), {"broker_order_id": "X1"})(),
                            raising=False)
        broker._cid_to_order_id = {}

        from bujji.core.enums import Side

        class _Req:
            class contract:
                symbol = "NSE:NIFTY2582524450CE"
            side = Side.SELL
            quantity = 65
            limit_price = None
            client_order_id = "COID-1"

        asyncio.run(broker.place_order(_Req()))
        assert seen["method"] == "place_order", "the gate blocked a verified order"


class TestTheGateDoesNotStrandAPosition:
    """Gating a RISK-REDUCING call would turn the guard into the hazard.

    An exit, a cancel and a position read must all stay reachable: if a
    position somehow exists, the system must still be able to see it and
    close it.
    """

    # Real arities, taken from the signatures. An earlier version of this test
    # called every method with one argument; `get_open_positions()` takes none,
    # so it raised TypeError at CALL time and the body never ran -- the test
    # passed no matter what the body did. A negative control that deliberately
    # gated `get_open_positions` failed to turn it red, which is how that was
    # found. A vacuous safety test is worse than no test: it reports coverage
    # it does not have.
    @pytest.mark.parametrize("method,args", [
        ("cancel_order", ("COID-1",)),
        ("get_order", ("COID-1",)),
        ("get_open_positions", ()),
    ])
    def test_risk_reducing_calls_are_not_gated(self, method, args):
        broker = _bare_broker()
        fn = getattr(broker, method)
        try:
            asyncio.run(fn(*args))
        except UnverifiedPositionSchemaError:  # pragma: no cover
            pytest.fail(f"{method}() is gated -- this can strand an open position")
        except TypeError as exc:  # pragma: no cover
            pytest.fail(
                f"{method}{args} has the wrong arity ({exc}) -- this test would "
                f"never reach the method body and so could not detect a gate")
        except Exception:
            pass  # any OTHER failure (no client, no config) is expected here
