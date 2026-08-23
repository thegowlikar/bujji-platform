"""One strategy per day, enforced across a restart.

THE GAP. `entry_control.can_enter_trade` refuses a second entry on
STRATEGY_ALREADY_DEPLOYED -- a state in an IN-MEMORY tracker. A restarted
process starts at INITIALIZING with that fact gone.

Reconciliation covers the case where the position is STILL OPEN: the registry
is empty after a restart, so the legs read as BROKER_ONLY, which is CRITICAL,
which blocks new risk. It cannot cover the case where the position was already
EXITED. The account is genuinely flat, the broker cannot distinguish "never
traded today" from "traded and closed today", and every remaining gate permits
entry.

The evidence needed to refuse was already durable in the position group
journal. Nothing read it.
"""
from __future__ import annotations

import datetime as _dt

import pytest

from bujji.core.clock import IST
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.prior_fills import (
    group_ids_filled_on, prior_fills_snapshot,
)

DAY = "2026-08-22"
OTHER_DAY = "2026-08-21"


def _journal(tmp_path):
    return PositionGroupJournal(str(tmp_path / "pg.db"))


def _at(hour, minute=0, tz=IST, day=22):
    return lambda: _dt.datetime(2026, 8, day, hour, minute, tzinfo=tz)


def _fill(journal, group_id, clock, legs=("LEG-1",)):
    """A realistic fill. The journal validates payloads AND ordering -- a
    SUBMIT_INTENT must reference a client_order_id that CONSTRUCTED already
    declared -- so this walks the same sequence a real entry writes, with
    every leg declared up front.
    """
    coids = {f"NIFTY-{leg}": f"{group_id}-{leg}" for leg in legs}
    for event_type, payload in (
        ("MINTED", {"plan_id": f"plan-{group_id}", "strategy_id": "STRANGLE",
                    "underlying": "NIFTY"}),
        ("CONSTRUCTED", {"contract_client_order_map": coids}),
    ):
        journal.append_event(
            position_group_id=group_id, event_type=event_type,
            idempotency_key=f"{group_id}:{event_type}", payload=payload, clock=clock)

    for leg in legs:
        coid = f"{group_id}-{leg}"
        for event_type, payload in (
            ("SUBMIT_INTENT", {"client_order_id": coid}),
            ("SUBMIT_ACK", {"client_order_id": coid, "broker_order_id": f"B-{coid}",
                            "broker_reported_status": "FILLED"}),
            ("FILL_OBSERVED", {"client_order_id": coid, "fill_price": 23.1,
                               "delta_quantity": 65, "delta_value": 1501.5,
                               "delta_cost_basis_status": "DERIVED",
                               "cumulative_filled_quantity_after": 65,
                               "cumulative_average_fill_price_after": 23.1}),
        ):
            journal.append_event(
                position_group_id=group_id, event_type=event_type,
                idempotency_key=f"{group_id}:{event_type}:{leg}",
                payload=payload, clock=clock)


# --------------------------------------------------------------------------
# The journal answers the question.
# --------------------------------------------------------------------------

def test_a_fill_today_is_found(tmp_path):
    j = _journal(tmp_path)
    _fill(j, "PG-1", _at(9, 52))
    assert group_ids_filled_on(j, DAY) == ["PG-1"]


def test_a_fill_on_another_day_is_not_todays_business(tmp_path):
    j = _journal(tmp_path)
    _fill(j, "PG-1", _at(9, 52, day=21))
    assert group_ids_filled_on(j, DAY) == []
    assert group_ids_filled_on(j, OTHER_DAY) == ["PG-1"]


def test_a_group_that_never_filled_does_not_burn_the_day(tmp_path):
    """Mirrors the in-session rule exactly. A group that minted or submitted
    but never filled leaves the tracker in STRATEGY_LOCKED, where a retry is
    legitimate -- only a fill moves it to POSITION_ACTIVE."""
    j = _journal(tmp_path)
    for event_type, payload in (
        ("MINTED", {"plan_id": "p1", "strategy_id": "STRANGLE", "underlying": "NIFTY"}),
        ("CONSTRUCTED", {"contract_client_order_map": {"NIFTY-CE": "c1"}}),
        ("SUBMIT_INTENT", {"client_order_id": "c1"}),
    ):
        j.append_event(
            position_group_id="PG-1", event_type=event_type,
            idempotency_key=f"PG-1:{event_type}", payload=payload, clock=_at(9, 52))
    assert group_ids_filled_on(j, DAY) == []


def test_two_groups_filled_today_are_both_reported(tmp_path):
    j = _journal(tmp_path)
    _fill(j, "PG-1", _at(9, 52))
    _fill(j, "PG-2", _at(11, 5))
    assert group_ids_filled_on(j, DAY) == ["PG-1", "PG-2"]


def test_the_same_group_filling_twice_is_reported_once(tmp_path):
    j = _journal(tmp_path)
    _fill(j, "PG-1", _at(9, 52), legs=("LEG-1", "LEG-2"))
    assert group_ids_filled_on(j, DAY) == ["PG-1"]


def test_the_date_is_parsed_not_prefix_matched(tmp_path):
    """`recorded_at` carries whatever timezone its writer used -- this class
    defaults to UTC while the runner injects IST. A 2026-08-22 04:00 UTC fill
    is 09:30 IST on the same trading day, and a substring comparison happens
    to agree. A 2026-08-21 20:00 UTC fill is 2026-08-22 01:30 IST, and there
    a substring comparison would answer the wrong day."""
    j = _journal(tmp_path)
    _fill(j, "PG-UTC-MORNING", _at(4, 0, tz=_dt.timezone.utc))
    _fill(j, "PG-UTC-EVENING", _at(20, 0, tz=_dt.timezone.utc, day=21))

    found = group_ids_filled_on(j, DAY)
    assert "PG-UTC-MORNING" in found
    assert "PG-UTC-EVENING" in found, (
        "20:00 UTC on the 21st is 01:30 IST on the 22nd; a text prefix "
        "comparison would have filed it under the wrong trading day")


def test_an_unparseable_timestamp_counts_rather_than_vanishes(tmp_path):
    """The safe error here is refusing one entry too many."""
    j = _journal(tmp_path)
    _fill(j, "PG-1", _at(9, 52))
    class _NoTimestamp:
        """A fill whose timestamp did not survive. Not evidence of a quiet day."""

        def read_all_group_ids(self):
            return ["PG-1"]

        def read_events(self, group_id):
            class _E:
                event_type = "FILL_OBSERVED"
                recorded_at = None
            return [_E()]

    assert group_ids_filled_on(_NoTimestamp(), DAY) == ["PG-1"]


# --------------------------------------------------------------------------
# The runner refuses.
# --------------------------------------------------------------------------

def _gate(prior_fills, unreadable=False):
    """Call the runner's pre-entry gate against a stand-in `self`.

    The gate under test is three lines of decision inside a method that also
    grades data quality; constructing the whole runner would drag in config,
    feeds and a broker. The stub declares only what the gate reads before it
    decides.
    """
    from bujji_options_os_runner import OptionsOSRunner

    blocked = []

    class _Stub:
        _as_of_date = DAY
        _prior_fills_today = prior_fills
        _prior_fills_unreadable = unreadable
        _reconciliation_blocks_entry = False
        _last_reconciliation = object()
        _reconciliation_ran = True
        _data_quality = None
        _intelligence_origin = None
        _governor_result_summary = {}
        _logger = __import__("logging").getLogger("test")

        # Declared out of scope for this test: the universe gates run ahead of
        # the one under test and have their own coverage elsewhere.
        def _ensure_universe_subscribed(self):
            return None

        def _record_universe_coverage(self):
            return None

        def _block_entry(self, reason):
            blocked.append(reason)

    permitted = OptionsOSRunner._data_quality_permits_entry(_Stub())
    return permitted, blocked


def test_a_restart_after_a_completed_trade_refuses_the_second_entry():
    permitted, blocked = _gate(["PG-1"])
    assert permitted is False
    assert blocked == ["STRATEGY_ALREADY_DEPLOYED_TODAY"]


def test_a_restart_with_nothing_filled_today_still_permits_entry():
    """The gate must not refuse a genuine first entry -- a guard that always
    refuses is not a guard."""
    permitted, blocked = _gate([])
    assert permitted is True
    assert blocked == []


def test_an_unreadable_journal_refuses_and_says_it_could_not_look():
    permitted, blocked = _gate(["UNREADABLE_JOURNAL"], unreadable=True)
    assert permitted is False
    assert blocked == ["PRIOR_FILLS_UNREADABLE"]


# --------------------------------------------------------------------------
# The two refusals are not the same refusal.
# --------------------------------------------------------------------------

def test_a_disciplined_decline_does_not_page_the_operator():
    """A restart that correctly refuses a second entry is a disciplined
    outcome reached with FULL SIGHT. Escalating it would page the operator
    every time a completed trading day was restarted."""
    from bujji.production_runtime.session_safety_verdict import _blindness_detail

    assert _blindness_detail("STRATEGY_ALREADY_DEPLOYED_TODAY") is None


def test_but_failing_to_look_does():
    from bujji.production_runtime.session_safety_verdict import _blindness_detail

    detail = _blindness_detail("PRIOR_FILLS_UNREADABLE")
    assert detail and "never established" in detail


# --------------------------------------------------------------------------
# The snapshot is taken before this session can contaminate it.
# --------------------------------------------------------------------------

def test_the_prior_fills_snapshot_is_read_once_at_startup_not_in_the_gate():
    """STRUCTURAL, and stated as such. A live query inside the gate would
    match THIS session's own fills and refuse its legitimate retries. The
    read must happen in startup, before any mint.
    """
    import ast
    import inspect

    import bujji_options_os_runner as mod

    src = inspect.getsource(mod)
    tree = ast.parse(src)
    fns = {n.name: n for n in ast.walk(tree)
           if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    def calls_journal_read(fn):
        return [n for n in ast.walk(fn)
                if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
                and n.func.attr == "group_ids_filled_on"]

    # The read itself lives in `_prior_fills_snapshot`, which `_startup`
    # calls; what matters is that the GATE never re-queries.
    startup_calls = [n for n in ast.walk(fns["_startup"])
                     if isinstance(n, ast.Call)
                     and getattr(n.func, "id", None) == "prior_fills_snapshot"]
    assert startup_calls, "the snapshot must be taken at startup, before any mint"

    gate_reads = [n for n in ast.walk(fns["_data_quality_permits_entry"])
                  if isinstance(n, ast.Call)
                  and getattr(n.func, "id", None) in ("prior_fills_snapshot",
                                                      "group_ids_filled_on")]
    assert not gate_reads, (
        "the gate must read the startup snapshot, never re-query the journal -- "
        "a live query would match this session's own fills")


def test_the_frozen_journal_package_is_not_modified():
    """`bujji/journal/` is byte-frozen since 360c003 -- see
    tests/test_replay_engine_safety.py. A convenience query is not a reason to
    break a freeze that protects order handling, so this reads the journal
    through its EXISTING public API from outside the package."""
    import inspect

    import bujji.production_runtime.prior_fills as mod

    src = inspect.getsource(mod)
    # M4 (2026-08-23): this used to assert `read_all_group_ids` by name.
    # `prior_fills` now enumerates through
    # `position_group_scope.position_group_ids`, which filters session-scoped
    # identities by explicit contract -- a strictly stronger boundary, and the
    # ratchet in tests/test_session_scope_is_excluded.py refuses any runtime
    # module that calls `read_all_group_ids` directly.
    #
    # The property this test protects is unchanged: prior_fills reads the
    # journal through its public API and never touches the frozen package.
    assert "position_group_ids" in src and "read_events" in src
    assert "position_group_events" not in src, (
        "this must not query the table directly -- that would be a second "
        "authority over the journal's own schema")
    assert "sqlite" not in src.lower(), "no direct database access"


# --------------------------------------------------------------------------
# The startup read fails CLOSED.
#
# The gate tests above inject `_prior_fills_today` directly, so none of them
# exercises this branch -- a negative control came back silent and said so.
# --------------------------------------------------------------------------

def _snapshot(journal):
    import logging

    return prior_fills_snapshot(journal, DAY, logging.getLogger("test"))


def test_a_readable_journal_reports_what_it_found(tmp_path):
    j = _journal(tmp_path)
    _fill(j, "PG-1", _at(9, 52))
    assert _snapshot(j) == (["PG-1"], False)


def test_a_readable_empty_journal_permits_the_first_entry(tmp_path):
    """A guard that cannot distinguish empty from broken is not a guard."""
    assert _snapshot(_journal(tmp_path)) == ([], False)


def test_an_unreadable_journal_is_not_an_empty_one():
    """THE fail-closed branch. A journal we could not read is not evidence
    that nothing was traded, and must refuse exactly as an unreadable
    position book does."""
    class _Broken:
        def group_ids_filled_on(self, day):
            raise OSError("database is locked")

    fills, unreadable = _snapshot(_Broken())
    assert unreadable is True
    assert fills, "an unreadable journal must not report an empty day"


def test_the_unreadable_marker_reaches_the_gate_as_a_refusal():
    """End to end across the seam: what the failed read returns is exactly
    what the gate refuses on."""
    class _Broken:
        def group_ids_filled_on(self, day):
            raise RuntimeError("corrupt")

    fills, unreadable = _snapshot(_Broken())
    permitted, blocked = _gate(fills, unreadable=unreadable)
    assert permitted is False
    assert blocked == ["PRIOR_FILLS_UNREADABLE"]
