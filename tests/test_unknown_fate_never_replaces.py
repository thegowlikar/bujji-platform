"""An order whose fate is unknown must never be re-placed.

THE DEFECT. `ExecutionEngine._place_idempotent` retried like this:

    except Exception as exc:                 # the placement may have LANDED
        landed = await self._lookup(cid)
        if landed.status is not OrderStatus.UNKNOWN:
            return landed
        if attempt >= attempts:
            raise ...
        # Confirmed absent -> safe to try placing again.     <- NOT confirmed

`_lookup` returns UNKNOWN for two entirely different facts:

    1. the broker ANSWERED and does not have this order   -> absent
    2. the lookup ITSELF failed                           -> we could not ask

The loop treated both as (1) and re-placed. Case (2) is the dangerous one AND
the likely one: place_order has just failed, so the broker is already unwell,
so the follow-up query fails too -- which is precisely the state in which the
original order most plausibly DID reach the exchange.

`retry_attempts` is 3 on the production path (runner:695), so a broker outage
could place the same order up to THREE times. The engine is wired into the
trading spine at runner:691 and drives the exit path at runner:729.

The information needed to tell the cases apart already existed --
`_lookup` set `message="lookup_failed"` -- and was discarded one line later.

This is the state the brief named as most dangerous: "Bujji does not know
whether an order was submitted... Never automatically resubmit until broker
truth is reconciled."
"""
from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.core.config import AppConfig, BrokerConfig  # noqa: E402
from bujji.core.enums import OrderStatus, Side  # noqa: E402
from bujji.core.models import OrderResult  # noqa: E402
from bujji.execution.engine import (  # noqa: E402
    LOOKUP_FAILED, ExecutionEngine, ExecutionError,
)

LOG = logging.getLogger("test-engine")
CFG = AppConfig(broker=BrokerConfig(
    name="paper", retry_attempts=3, retry_backoff_seconds=0.0,
    poll_interval_seconds=0.0, order_timeout_seconds=1.0))


class _Req:
    class contract:
        symbol = "NSE:NIFTY2582524450CE"
    side = Side.SELL
    quantity = 65
    limit_price = None
    client_order_id = "COID-1"
    reference_price = None


class _Broker:
    """place_order always fails. get_order behaves as configured.

    Declares `order_tag_roundtrip_verified = True` EXPLICITLY, because that is
    what makes an "absent" answer count as evidence. A broker that cannot
    recognise its own client_order_id -- FyersBroker, whose orderTag echo is
    unverified -- returns "not found" for an order that may well be live, and
    the engine now refuses to re-place on that. Stating the capability here
    keeps these tests about the retry logic rather than about the gate, which
    has its own class below.
    """

    order_tag_roundtrip_verified = True

    def __init__(self, lookup):
        self.places = 0
        self._lookup = lookup

    async def place_order(self, request):
        self.places += 1
        raise ConnectionError("broker unreachable")

    async def get_order(self, cid):
        return self._lookup(cid)


def _engine(broker):
    return ExecutionEngine(broker, CFG, LOG)


class TestAnUnknownFateStopsImmediately:
    def test_a_failed_lookup_never_re_places(self):
        """The lookup could not be performed, so the order's fate is unknown.
        Exactly ONE placement attempt may ever have reached the exchange."""

        def _lookup_blows_up(_cid):
            raise ConnectionError("cannot query either")

        broker = _Broker(_lookup_blows_up)
        with pytest.raises(ExecutionError) as exc:
            asyncio.run(_engine(broker)._place_idempotent(_Req()))

        assert broker.places == 1, (
            f"the order was placed {broker.places} times after its fate became "
            f"unknown -- each extra placement may be a real duplicate")
        assert "may already be live" in str(exc.value)

    def test_the_operator_is_told_to_reconcile_not_to_retry(self):
        def _lookup_blows_up(_cid):
            raise ConnectionError("cannot query either")

        with pytest.raises(ExecutionError) as exc:
            asyncio.run(_engine(_Broker(_lookup_blows_up))._place_idempotent(_Req()))
        assert "Reconcile against broker truth" in str(exc.value)


class TestAConfirmedAbsentOrderStillRetries:
    """The guard must not become a wall: when the broker ANSWERS and does not
    have the order, re-placing is the correct behaviour and must survive."""

    def test_an_answered_absent_order_is_retried_to_the_limit(self):
        def _answers_absent(cid):
            return OrderResult(cid, OrderStatus.UNKNOWN, message="not found")

        broker = _Broker(_answers_absent)
        with pytest.raises(ExecutionError) as exc:
            asyncio.run(_engine(broker)._place_idempotent(_Req()))
        assert broker.places == 3, (
            f"expected 3 attempts against an answered-absent broker, saw "
            f"{broker.places}")
        assert "order absent after 3 attempts" in str(exc.value)


class TestALandedOrderIsReturned:
    def test_an_order_that_landed_despite_the_error_is_adopted(self):
        def _landed(cid):
            return OrderResult(cid, OrderStatus.FILLED, message="ok")

        broker = _Broker(_landed)
        out = asyncio.run(_engine(broker)._place_idempotent(_Req()))
        assert out.status is OrderStatus.FILLED
        assert broker.places == 1, "a landed order must never be placed again"


class TestTheSentinelIsNotAccidental:
    def test_lookup_failure_uses_the_named_constant(self):
        def _lookup_blows_up(_cid):
            raise ConnectionError("nope")

        eng = _engine(_Broker(_lookup_blows_up))
        result = asyncio.run(eng._lookup("COID-1"))
        assert result.status is OrderStatus.UNKNOWN
        assert result.message == LOOKUP_FAILED, (
            "the re-place guard keys on this exact message; a silent rename "
            "would restore the duplicate-order path")


class TestAnUnverifiableAbsentAnswerIsNotEvidence:
    """FyersBroker.get_order() finds an order by scanning today's book for
    `orderTag == client_order_id`. Whether FYERS echoes that tag back is
    UNVERIFIED -- place_order's own comment says it "cannot confirm without
    placing a real order".

    If it does not echo, EVERY lookup returns "not found", which is
    indistinguishable from an order that never reached the exchange.
    Re-placing on that is a duplicate-order path wearing a confirmation's
    clothes.
    """

    class _UnverifiableBroker(_Broker):
        order_tag_roundtrip_verified = False

    def test_an_absent_answer_from_an_unverifiable_broker_does_not_re_place(self):
        def _answers_absent(cid):
            return OrderResult(cid, OrderStatus.UNKNOWN, message="not found")

        broker = self._UnverifiableBroker(_answers_absent)
        with pytest.raises(ExecutionError) as exc:
            asyncio.run(_engine(broker)._place_idempotent(_Req()))
        assert broker.places == 1, (
            f"placed {broker.places} times against a broker whose 'not found' "
            f"cannot be trusted")
        assert "UNVERIFIED" in str(exc.value)
        assert "Reconcile against broker truth" in str(exc.value)

    def test_the_default_is_closed_for_a_broker_that_declares_nothing(self):
        """An unknown broker's absent-answer is not trustworthy. Failing
        closed costs a manual reconciliation; failing open costs a duplicate
        live order."""
        class _Silent:
            def __init__(self):
                self.places = 0

            async def place_order(self, request):
                self.places += 1
                raise ConnectionError("broker unreachable")

            async def get_order(self, cid):
                return OrderResult(cid, OrderStatus.UNKNOWN, message="not found")

        broker = _Silent()
        assert not hasattr(broker, "order_tag_roundtrip_verified")
        with pytest.raises(ExecutionError):
            asyncio.run(_engine(broker)._place_idempotent(_Req()))
        assert broker.places == 1

    def test_the_real_fyers_broker_declares_it_unverified(self):
        from bujji.broker.fyers import (
            FYERS_ORDERTAG_ROUNDTRIP_VERIFIED, FyersBroker,
        )

        assert FYERS_ORDERTAG_ROUNDTRIP_VERIFIED is False
        assert FyersBroker.order_tag_roundtrip_verified is False

    def test_the_paper_broker_declares_it_verified(self):
        """PaperBroker keys its own order book on the id it was given, in this
        process, so a lookup by that id is exact."""
        from bujji.broker.paper import PaperBroker

        assert PaperBroker.order_tag_roundtrip_verified is True
