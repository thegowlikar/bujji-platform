"""M3: every position consumer reads through one boundary, and UNKNOWN survives.

Before this migration four consumers each answered "what does the broker hold"
their own way. The registry's answer was the wrong one in a way the others were
not: it INTERSECTED the broker's reply with a local table of symbols this
process happened to register, and it had no notion of a read that failed.

These tests are about what that cost, expressed as behaviour:

  * a group was marked CLOSED when the account merely could not be read
  * a zero-quantity row counted as a holding (latent behind PaperBroker,
    waiting for the live read-only adapter)
  * a malformed row raised KeyError out of an async read model
"""
from __future__ import annotations

import datetime as _dt

import pytest

from bujji.broker_truth import (
    STATE_CONFIRMED_FLAT, STATE_CONFIRMED_OPEN, STATE_UNKNOWN,
    BrokerTruthUnknownError, for_paper,
)
from bujji.production_runtime.position_reality_registry import (
    PositionRealityRegistry, UnknownPositionGroupError,
)

CLOCK = lambda: _dt.datetime(2026, 8, 22, 9, 20)   # noqa: E731
LEG = "NSE:NIFTY26AUG24500CE"
OTHER = "NSE:NIFTY26AUG24000PE"


class _Book:
    """A broker whose book -- and whose failures -- the test controls."""

    def __init__(self, rows=(), raises=None):
        # `rows=None` is a real broker answer, not a construction error: it is
        # the "returned no list at all" case, which must read as UNKNOWN.
        self.rows = None if rows is None else list(rows)
        self.raises = raises
        self.reads = 0

    async def get_open_positions(self):
        self.reads += 1
        if self.raises is not None:
            raise self.raises
        return None if self.rows is None else list(self.rows)

    def get_realized_pnl(self, symbol):
        return 0.0


def _registry(book, symbols=(LEG,), pg="PG-1"):
    reg = PositionRealityRegistry(book, truth=for_paper(book))
    reg.register_entry(pg, "STRANGLE", list(symbols), 1000.0, CLOCK)
    return reg


def _row(symbol=LEG, qty=75):
    return {"symbol": symbol, "qty": qty, "avg_price": 120.0, "side": "SELL"}


# --------------------------------------------------------------------------
# THE defect: an unreadable account is not a closed position.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_unreadable_account_is_neither_open_nor_confirmed_flat():
    reg = _registry(_Book(raises=ConnectionError("socket closed")))
    reality = await reg.get_group_reality("PG-1")

    assert reality.truth_state == STATE_UNKNOWN
    assert reality.is_open is False, "an unread book is not evidence of a position"
    assert reality.is_confirmed_flat is False, (
        "an unread book is not evidence of flatness either -- and this is the "
        "reading that used to mark a live group CLOSED")
    assert reality.may_still_be_open is True


@pytest.mark.asyncio
async def test_is_confirmed_flat_is_not_the_negation_of_is_open():
    """They differ on exactly one case, and it is the one that matters."""
    unknown = await _registry(_Book(raises=IOError("down"))).get_group_reality("PG-1")
    answered = await _registry(_Book([])).get_group_reality("PG-1")

    assert unknown.is_open is answered.is_open is False
    assert unknown.is_confirmed_flat is False
    assert answered.is_confirmed_flat is True


@pytest.mark.asyncio
async def test_an_unreadable_account_leaves_every_group_unresolved():
    """open_group_ids feeds the EOD unresolved report and the heartbeat's
    active count. A group cannot be ruled out by a read that did not happen."""
    book = _Book(raises=TimeoutError("gateway"))
    reg = _registry(book)
    reg.register_entry("PG-2", "IRON_CONDOR", [OTHER], 500.0, CLOCK)

    assert set(await reg.open_group_ids()) == {"PG-1", "PG-2"}


@pytest.mark.asyncio
async def test_positions_for_group_raises_rather_than_reporting_no_legs():
    """Every caller reads [] as "nothing to reduce" and stops -- the executor
    rejects the action, the session governor submits no exit. On a hard-limit
    forced exit that silence is the worst available outcome."""
    reg = _registry(_Book(raises=ConnectionError("reset")))
    with pytest.raises(BrokerTruthUnknownError, match="could not be established"):
        await reg.positions_for_group("PG-1")


# --------------------------------------------------------------------------
# The latent defect that was waiting for the live adapter.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_zero_quantity_row_no_longer_pins_a_group_open():
    """FYERS returns netQty=0 for positions closed intraday. The old
    _open_symbols had no quantity filter at all, so one of those rows would
    have held a group open for the rest of the session -- permanently
    unresolved, blocking EOD closure. PaperBroker pops closed symbols, which
    is the only reason this never surfaced."""
    reality = await _registry(_Book([_row(qty=0)])).get_group_reality("PG-1")
    assert reality.is_confirmed_flat is True
    assert await _registry(_Book([_row(qty=0)])).open_group_ids() == ()


@pytest.mark.asyncio
async def test_a_malformed_row_is_unknown_not_a_keyerror():
    """`{p["symbol"] for p in positions}` raised out of an async read model."""
    reality = await _registry(_Book([{"qty": 75}])).get_group_reality("PG-1")
    assert reality.truth_state == STATE_UNKNOWN


# --------------------------------------------------------------------------
# Scoping to a group is still correct -- and is a different question.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_group_whose_legs_the_broker_holds_is_confirmed_open():
    reality = await _registry(_Book([_row()])).get_group_reality("PG-1")
    assert reality.truth_state == STATE_CONFIRMED_OPEN
    assert reality.is_open is True
    assert reality.open_symbols == (LEG,)


@pytest.mark.asyncio
async def test_a_group_is_flat_when_the_broker_holds_only_someone_elses_leg():
    """The broker ANSWERED and named none of this group's symbols. That is a
    confirmed flat for this group, even though the account is not empty."""
    reality = await _registry(_Book([_row(symbol=OTHER)])).get_group_reality("PG-1")
    assert reality.truth_state == STATE_CONFIRMED_OPEN
    assert reality.is_confirmed_flat is True
    assert reality.is_open is False


@pytest.mark.asyncio
async def test_the_unfiltered_question_still_sees_an_unregistered_position():
    """The registry scopes to a group by design. It must not be the ONLY door,
    or a leg this process never registered is invisible by construction."""
    reg = _registry(_Book([_row(symbol="NSE:SOMETHING-WE-NEVER-REGISTERED")]))
    truth = await reg.broker_truth()

    assert truth.state == STATE_CONFIRMED_OPEN
    assert truth.symbols == ("NSE:SOMETHING-WE-NEVER-REGISTERED",)
    assert (await reg.get_group_reality("PG-1")).is_open is False, (
        "scoped to the group -- which is the right answer to a different "
        "question, and why the unfiltered door has to exist")


# --------------------------------------------------------------------------
# Shape and read-count contracts the migration must not have broken.
# --------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_positions_for_group_still_returns_broker_native_rows():
    """revalue() takes the broker's dicts verbatim -- it wants entry price and
    timestamp, not just symbol and quantity."""
    rows = await _registry(_Book([_row(), _row(symbol=OTHER)])).positions_for_group("PG-1")
    assert rows == [{"symbol": LEG, "qty": 75, "avg_price": 120.0, "side": "SELL"}]


@pytest.mark.asyncio
async def test_each_registry_call_costs_exactly_one_broker_read():
    """The N+1 hazard: deriving two sides of a comparison from different reads
    lets a position closing between them manufacture a false divergence."""
    book = _Book([_row()])
    reg = _registry(book)

    await reg.get_group_reality("PG-1")
    assert book.reads == 1
    await reg.open_group_ids()
    assert book.reads == 2
    await reg.positions_for_group("PG-1")
    assert book.reads == 3


@pytest.mark.asyncio
async def test_pure_accessors_still_make_no_broker_call():
    book = _Book([_row()])
    reg = _registry(book)

    reg.symbols_for_group("PG-1")
    reg.initial_risk("PG-1")
    reg.all_group_ids()
    reg.contract_for_symbol("PG-1", LEG)
    assert book.reads == 0


@pytest.mark.asyncio
async def test_an_unregistered_group_is_still_refused_before_any_read():
    book = _Book([_row()])
    reg = _registry(book)
    for call in (reg.get_group_reality("PG-NEVER"), reg.positions_for_group("PG-NEVER")):
        with pytest.raises(UnknownPositionGroupError):
            await call
    assert book.reads == 0, "refuse the unknown group before paying for a read"


def test_the_registry_has_no_direct_position_read_left():
    """Structural. The migration is only real if the old door is gone -- a
    second reading of the same question is how the four diverged."""
    import ast, inspect
    import bujji.production_runtime.position_reality_registry as mod

    tree = ast.parse(inspect.getsource(mod))
    calls = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "get_open_positions"
    ]
    assert calls == [], (
        "the registry called the broker directly again; every read must go "
        "through bujji.broker_truth")


# --------------------------------------------------------------------------
# The runner's and the EOD machine's readers: same rule, one implementation.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("book, expected", [
    (_Book([]), True),
    (_Book([_row()]), False),
    (_Book(raises=ConnectionError("x")), None),
    (_Book([{"qty": 75}]), None),
])
def test_discover_broker_positions_stays_three_valued(book, expected):
    from bujji.production_runtime.eod_closure import discover_broker_positions
    import asyncio

    positions, detail = discover_broker_positions(book, asyncio.run)
    if expected is None:
        assert positions is None, f"a failed read became {positions!r}: {detail}"
    elif expected is True:
        assert positions == []
    else:
        assert positions and positions[0]["symbol"] == LEG


def test_discover_broker_positions_distinguishes_empty_from_failed():
    """`[]` and `None` are the two answers that must never converge: one
    closes the session, the other refuses to."""
    from bujji.production_runtime.eod_closure import discover_broker_positions
    import asyncio

    empty, _ = discover_broker_positions(_Book([]), asyncio.run)
    failed, _ = discover_broker_positions(_Book(raises=IOError("no route")), asyncio.run)
    assert empty == [] and failed is None
    assert empty is not None


# --------------------------------------------------------------------------
# The runner's flatness check -- the caller that governs the ordinary day.
# --------------------------------------------------------------------------

def _flat_via_runner(book):
    """Call the runner's own method against a stub self.

    Bound this way deliberately: constructing the whole runner would drag in
    config, feeds and a broker, and the method under test is exactly three
    lines of decision. `_position_truth` builds and caches its reader on
    whatever `self` it is handed.
    """
    from bujji_options_os_runner import OptionsOSRunner as _R

    class _Self:
        _broker = book

    return _R._broker_reports_flat(_Self())


@pytest.mark.parametrize("book, expected, why", [
    (_Book([]), True, "an empty book from a healthy read is flat"),
    (_Book([_row()]), False, "a held leg is not flat"),
    (_Book([_row(qty=0)]), True, "a zero-quantity row is not a holding"),
    (_Book(raises=ConnectionError("x")), None, "a failed read is not flat"),
    (_Book([{"qty": 75}]), None, "an unreadable row is not flat"),
    (_Book(None), None, "no list at all is not flat"),
])
def test_broker_reports_flat_stays_three_valued(book, expected, why):
    flat, detail = _flat_via_runner(book)
    assert flat is expected, f"{why} (detail: {detail})"


def test_broker_reports_flat_never_turns_a_failure_into_flatness():
    """The whole point of the third value. `None` and `True` lead to opposite
    actions: one ends monitoring and closes the session, the other refuses."""
    failed, _ = _flat_via_runner(_Book(raises=IOError("no route")))
    empty, _ = _flat_via_runner(_Book([]))
    assert failed is None and empty is True
    assert failed is not True


def test_the_runner_reuses_one_labelled_reader():
    from bujji_options_os_runner import OptionsOSRunner as _R

    class _Self:
        _broker = _Book([])

    stub = _Self()
    _R._broker_reports_flat(stub)
    first = stub._broker_truth_reader
    _R._broker_reports_flat(stub)
    assert stub._broker_truth_reader is first, (
        "a second reader is a second reading of the same question")


# --------------------------------------------------------------------------
# The risk snapshot's OPEN/CLOSED label.
# --------------------------------------------------------------------------

def test_the_risk_snapshot_labels_an_unreadable_account_as_still_open():
    """STRUCTURAL, and stated as such: exercising this behaviourally needs the
    whole lifecycle runtime assembled. What is asserted is the specific
    attribute the label is derived from, as an AST node -- not a substring,
    which would have accepted `is_open` inside a disabled branch.

    Feeding recommend_risk_action() a CLOSED snapshot for a position we merely
    could not read tells it there is nothing to manage, on exactly the pass
    where management is most likely to be needed.
    """
    import ast, inspect
    import bujji.production_runtime.position_lifecycle_runtime as mod

    tree = ast.parse(inspect.getsource(mod))
    labels = [
        n for n in ast.walk(tree)
        if isinstance(n, ast.IfExp)
        and isinstance(n.body, ast.Constant) and n.body.value == "OPEN"
        and isinstance(n.orelse, ast.Constant) and n.orelse.value == "CLOSED"
    ]
    assert len(labels) == 1, f"expected one OPEN/CLOSED label, found {len(labels)}"
    test = labels[0].test
    assert isinstance(test, ast.Attribute), ast.dump(test)
    assert test.attr == "may_still_be_open", (
        f"the label is derived from `.{test.attr}`; `is_open` is False for an "
        f"account that could not be read, which would label a live position "
        f"CLOSED")
