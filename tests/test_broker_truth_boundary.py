"""The broker-truth boundary: three answers, and the third is the point.

Every test here is about the SAME distinction, approached from a different
direction: an empty book from a healthy read is CONFIRMED_FLAT, and anything
this adapter could not read or could not understand is UNKNOWN. The two must
never converge, because they lead to opposite actions -- one closes a session,
the other refuses to.
"""
import pytest

from bujji.broker_truth import (
    STATE_CONFIRMED_FLAT, STATE_CONFIRMED_OPEN, STATE_UNKNOWN,
    BrokerPositionTruth, BrokerTruth, BrokerTruthUnknownError,
    ControllablePaperPositions, OpenLeg, for_paper,
)


class _Book:
    """The smallest thing that answers get_open_positions()."""

    def __init__(self, rows):
        self._rows = rows

    async def get_open_positions(self):
        return self._rows


def _read(rows):
    return for_paper(_Book(rows)).read()


# --------------------------------------------------------------------------
# The distinction itself.
# --------------------------------------------------------------------------

def test_empty_book_from_a_healthy_read_is_confirmed_flat():
    truth = _read([])
    assert truth.state == STATE_CONFIRMED_FLAT
    assert truth.is_flat and not truth.is_unknown


def test_a_read_that_raises_is_unknown_not_flat():
    class _Broken:
        async def get_open_positions(self):
            raise ConnectionError("socket closed")

    truth = for_paper(_Broken()).read()
    assert truth.state == STATE_UNKNOWN
    assert not truth.is_flat, "a failed read must never present as a flat account"
    assert "socket closed" in truth.detail


def test_none_is_unknown_because_no_answer_is_not_an_empty_answer():
    assert _read(None).state == STATE_UNKNOWN


def test_a_held_leg_is_confirmed_open_and_carries_its_quantity():
    truth = _read([{"symbol": "NIFTY-CE", "qty": 75, "side": "SELL", "avg_price": 120.5}])
    assert truth.state == STATE_CONFIRMED_OPEN
    assert truth.symbols == ("NIFTY-CE",)
    assert truth.quantity_for("NIFTY-CE") == 75
    assert truth.legs[0].average_price == 120.5


def test_a_zero_quantity_row_is_not_a_holding():
    """FYERS returns netQty=0 rows for positions closed intraday. Counting one
    as open would leave a group permanently unresolved."""
    assert _read([{"symbol": "NIFTY-CE", "qty": 0}]).state == STATE_CONFIRMED_FLAT


# --------------------------------------------------------------------------
# Failing closed on a payload this adapter no longer understands.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("rows, why", [
    ({"symbol": "X"}, "a mapping is not a list of rows"),
    ("NIFTY-CE", "a bare string is not a position book"),
    ([["NIFTY-CE", 75]], "a row that is not a mapping"),
    ([{"qty": 75}], "a holding with no symbol cannot be closed"),
    ([{"symbol": "NIFTY-CE", "qty": "seventy-five"}], "an unreadable quantity"),
])
def test_payloads_this_adapter_cannot_understand_are_unknown(rows, why):
    assert _read(rows).state == STATE_UNKNOWN, why


def test_one_unreadable_row_poisons_the_whole_read():
    """NOT a partial answer. A book where one row was skipped looks complete
    and is not -- which is more dangerous than one that is plainly unreadable.
    """
    truth = _read([
        {"symbol": "GOOD", "qty": 75},
        {"symbol": "BAD", "qty": object()},
    ])
    assert truth.state == STATE_UNKNOWN
    assert truth.legs == (), "an UNKNOWN answer must not carry a half-read book"
    assert "BAD" in truth.detail


# --------------------------------------------------------------------------
# States that must be unconstructable.
# --------------------------------------------------------------------------

def test_confirmed_open_with_no_legs_is_refused():
    with pytest.raises(ValueError, match="CONFIRMED_OPEN with no legs"):
        BrokerTruth(STATE_CONFIRMED_OPEN, (), "d", "s", True)


@pytest.mark.parametrize("state", [STATE_CONFIRMED_FLAT, STATE_UNKNOWN])
def test_a_non_open_state_carrying_legs_is_refused(state):
    with pytest.raises(ValueError, match="contradictory"):
        BrokerTruth(state, (OpenLeg("X", 1),), "d", "s", True)


def test_an_unrecognised_state_string_is_refused():
    with pytest.raises(ValueError, match="unknown broker-truth state"):
        BrokerTruth("PROBABLY_FLAT", (), "d", "s", True)


# --------------------------------------------------------------------------
# The None-is-not-zero rule.
# --------------------------------------------------------------------------

def test_quantity_for_an_absent_leg_is_none_not_zero():
    truth = _read([{"symbol": "HELD", "qty": 75}])
    assert truth.quantity_for("NOT-HELD") is None, (
        "0 would read as 'nothing to reduce'; None reads as 'I do not know "
        "about this leg', which is what is actually true")


def test_require_known_raises_only_on_unknown():
    class _Broken:
        async def get_open_positions(self):
            raise TimeoutError("no route")

    with pytest.raises(BrokerTruthUnknownError, match="UNKNOWN is not FLAT"):
        for_paper(_Broken()).read().require_known("EOD closure")
    assert _read([]).require_known("EOD closure").is_flat


# --------------------------------------------------------------------------
# Provenance travels in the result, not in a comment.
# --------------------------------------------------------------------------

def test_source_and_schema_verified_travel_with_every_answer():
    truth = _read([])
    assert truth.source == "paper" and truth.schema_verified is True


def test_fyers_answers_are_schema_unverified_and_stay_that_way():
    from bujji.broker.fyers import FYERS_POSITION_SCHEMA_VERIFIED
    from bujji.broker_truth import for_fyers_read_only

    assert FYERS_POSITION_SCHEMA_VERIFIED is False, (
        "netQty/netAvg have never been seen with a real position; verifying "
        "that requires placing a real order")
    assert for_fyers_read_only(_Book([])).read().schema_verified is False


def test_an_unknown_answer_never_claims_schema_verification():
    class _Broken:
        async def get_open_positions(self):
            raise RuntimeError("boom")

    assert for_paper(_Broken()).read().schema_verified is False, (
        "an answer we could not read tells us nothing about whether we would "
        "have understood it")


def test_a_boundary_with_no_broker_is_refused():
    with pytest.raises(ValueError, match="requires a broker"):
        BrokerPositionTruth(None, source="paper", schema_verified=True)


# --------------------------------------------------------------------------
# Fault injection -- the UNKNOWN branches are unreachable without it.
# --------------------------------------------------------------------------

def test_paper_read_cannot_fail_which_is_why_the_injector_exists():
    """PaperBroker.get_open_positions() is a dict read. Every UNKNOWN branch
    downstream is correct and, without this wrapper, entirely unexercised."""
    from bujji.broker.paper import PaperBroker

    truth = for_paper(PaperBroker()).read()
    assert truth.state == STATE_CONFIRMED_FLAT


def test_a_transient_failure_recovers_the_way_a_real_one_does():
    book = ControllablePaperPositions(_Book([{"symbol": "NIFTY-CE", "qty": 75}]))
    book.fail_once(ConnectionError("reset by peer"))
    reader = for_paper(book)
    assert reader.read().state == STATE_UNKNOWN
    assert reader.read().state == STATE_CONFIRMED_OPEN
    assert book.reads == 2


def test_a_persistent_failure_stays_unknown():
    book = ControllablePaperPositions(_Book([]))
    book.fail_always(TimeoutError("gateway"))
    assert for_paper(book).read().state == STATE_UNKNOWN
    assert for_paper(book).read().state == STATE_UNKNOWN


def test_the_injector_can_substitute_a_payload_shape():
    book = ControllablePaperPositions(_Book([]))
    book.return_instead({"netPositions": []})
    assert for_paper(book).read().state == STATE_UNKNOWN


# --------------------------------------------------------------------------
# Both doors, one interpretation.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_the_async_door_gives_the_same_answers():
    """The registry and lifecycle layers are async; read() would raise inside
    a running loop. Both doors must agree, or the boundary has two meanings."""
    rows = [{"symbol": "NIFTY-CE", "qty": 75}]
    assert (await for_paper(_Book(rows)).read_async()).state == STATE_CONFIRMED_OPEN
    assert (await for_paper(_Book([])).read_async()).state == STATE_CONFIRMED_FLAT
    assert (await for_paper(_Book(None)).read_async()).state == STATE_UNKNOWN


@pytest.mark.asyncio
async def test_the_async_door_turns_a_raise_into_unknown_too():
    class _Broken:
        async def get_open_positions(self):
            raise ConnectionError("reset")

    truth = await for_paper(_Broken()).read_async()
    assert truth.state == STATE_UNKNOWN and "reset" in truth.detail


@pytest.mark.asyncio
async def test_the_sync_door_refuses_inside_a_running_loop_rather_than_lying():
    """asyncio.run cannot nest. The failure must present as UNKNOWN, not as a
    flat account -- a caller that used the wrong door must not be told the
    book is empty."""
    assert for_paper(_Book([{"symbol": "X", "qty": 1}])).read().state == STATE_UNKNOWN


# --------------------------------------------------------------------------
# Dispatch: a branch that never fires is worse than no branch.
# --------------------------------------------------------------------------

def test_for_broker_labels_each_broker_it_recognises():
    """This caught a real defect: the hybrid branch imported a class name that
    does not exist, so its ImportError was swallowed and it never fired."""
    from bujji.broker.hybrid import HybridPaperBroker
    from bujji.broker.paper import PaperBroker
    from bujji.broker_truth import for_broker

    assert for_broker(PaperBroker()).source == "paper"

    hybrid = HybridPaperBroker.__new__(HybridPaperBroker)
    reader = for_broker(hybrid)
    assert reader.source == "hybrid_paper_ledger"
    assert reader.schema_verified is True, "its rows come from this codebase"


def test_for_broker_labels_fyers_as_read_only_and_unverified():
    from bujji.broker.fyers import FyersBroker
    from bujji.broker_truth import for_broker

    reader = for_broker(FyersBroker.__new__(FyersBroker))
    assert reader.source == "fyers_read_only"
    assert reader.schema_verified is False


def test_an_unrecognised_broker_is_never_claimed_as_schema_verified():
    class SomeFutureBroker:
        async def get_open_positions(self):
            return []

    from bujji.broker_truth import for_broker

    reader = for_broker(SomeFutureBroker())
    assert reader.source == "SomeFutureBroker"
    assert reader.schema_verified is False, (
        "a row shape this codebase has never confirmed is exactly where a "
        "verification claim would be a guess")
