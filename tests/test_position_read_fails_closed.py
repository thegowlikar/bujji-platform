"""A failed position read must never be reported as a flat account.

THE DEFECT. `FyersBroker.get_open_positions()` went straight to
`data.get("netPositions", [])` with no success check of any kind. An error
response, a malformed body, or a renamed field all produced `[]` -- and `[]`
means FLAT to every caller above it.

`get_funds()`, twenty lines further down the same file, has always guarded
`if str(data.get("s","")).lower() != "ok": return None`. The position read --
the one that decides whether an options position is still open -- had no
guard at all.

WHY IT MATTERS MORE THAN IT LOOKS. Everything above this method is carefully
three-valued. `_broker_reports_flat()` documents it outright: "A read that
fails returns None, never False: 'I could not ask' must never become 'there
is nothing there'." `eod_closure.discover_broker_positions()` maps an
exception to None and even refuses a malformed row. That machinery is
correct, and it was starving: the adapter never handed it the None it is
built to act on. The one component that could see the failure was the one
component that swallowed it.

So the fix is to RAISE. Both callers already convert an exception to
UNKNOWN, so failing closed here is what switches the existing safety
machinery on.

A genuinely flat account is unaffected: `s: "ok"` with no open legs is a real
answer and still returns an empty list.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.broker.errors import PositionReadError  # noqa: E402
from bujji.broker.fyers import FyersBroker  # noqa: E402


def _broker_returning(payload):
    b = FyersBroker.__new__(FyersBroker)

    async def _call(_action, **_kw):
        return payload

    b._call = _call
    b._raise_if_auth_error = lambda _d: None
    return b


def _positions(payload):
    return asyncio.run(_broker_returning(payload).get_open_positions())


class TestAGenuinelyFlatAccountStillReadsFlat:
    def test_ok_with_an_empty_book_is_an_empty_list(self):
        assert _positions({"s": "ok", "netPositions": []}) == []

    def test_ok_with_only_closed_legs_is_an_empty_list(self):
        assert _positions({"s": "ok", "netPositions": [
            {"symbol": "NSE:X", "netQty": 0, "netAvg": 10.0}]}) == []

    def test_a_real_open_leg_is_returned(self):
        out = _positions({"s": "ok", "netPositions": [
            {"symbol": "NSE:NIFTY2582524450CE", "netQty": -65, "netAvg": 24.6}]})
        assert out == [{"symbol": "NSE:NIFTY2582524450CE", "side": "SELL",
                        "qty": 65, "avg_price": 24.6}]


class TestAFailedReadRaisesInsteadOfClaimingFlat:
    @pytest.mark.parametrize("payload", [
        {"s": "error", "message": "invalid token"},
        {"s": "error", "code": -16},
        {},                                   # empty body
        {"message": "gateway timeout"},       # no 's' at all
        {"s": "ok"},                          # ok, but no netPositions key
    ])
    def test_an_unusable_response_raises(self, payload):
        with pytest.raises(PositionReadError):
            _positions(payload)

    @pytest.mark.parametrize("payload", [
        {"s": "error", "message": "rate limited", "netPositions": []},
        {"s": "error", "code": -300, "netPositions": []},
        {"s": "", "netPositions": []},
    ])
    def test_an_error_that_still_carries_netPositions_raises(self, payload):
        """The most dangerous realistic shape, and the one that isolates the
        `s == "ok"` guard.

        An error body that still includes an empty `netPositions` list is
        indistinguishable from a genuinely flat account UNLESS the success
        field is checked. Without this case every error payload in the test
        above also happened to lack the key, so the shape check caught them
        all and deleting the `s == "ok"` guard left the suite green -- found
        by the negative control, not by reading.
        """
        with pytest.raises(PositionReadError) as exc:
            _positions(payload)
        assert "not ok" in str(exc.value)

    def test_a_row_without_netQty_raises_rather_than_reading_as_flat(self):
        """The documented "manufactures flatness" failure. If `netQty` is
        actually named something else, `p.get("netQty", 0)` returns 0 for
        EVERY row, every position is filtered out, and the account looks
        empty -- silently, and in the dangerous direction."""
        with pytest.raises(PositionReadError) as exc:
            _positions({"s": "ok", "netPositions": [
                {"symbol": "NSE:NIFTY2582524450CE", "quantity": -65}]})
        assert "manufacture flatness" in str(exc.value)


class TestTheCallersTurnThatIntoUNKNOWN:
    """Raising is only useful because the machinery above acts on it."""

    def test_broker_reports_flat_maps_the_raise_to_None_not_False(self):
        import importlib.util

        spec = importlib.util.spec_from_file_location(
            "_runner_pos_guard", REPO_ROOT / "bujji_options_os_runner.py")
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)

        runner = mod.OptionsOSRunner.__new__(mod.OptionsOSRunner)
        runner._broker = _broker_returning({"s": "error", "message": "boom"})
        flat, detail = runner._broker_reports_flat()
        assert flat is None, (
            "a failed read became a definite answer -- UNKNOWN must never "
            "collapse to FLAT")
        assert "PositionReadError" in detail

    def test_eod_closure_maps_the_raise_to_None(self):
        from bujji.production_runtime.eod_closure import discover_broker_positions

        broker = _broker_returning({"s": "error"})
        positions, detail = discover_broker_positions(broker, asyncio.run)
        assert positions is None
        assert "position read failed" in detail
