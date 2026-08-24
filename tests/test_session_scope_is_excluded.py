"""Session rows are excluded from every position-group boundary, by contract.

M4 put session lifecycle into the SAME durable journal as position lifecycle.
Session rows carry a `SESSION:<id>` identity and a SESSION_TRANSITION type.

A session identity happens to fold to LIFECYCLE_MINTED, and MINTED is not in
`_ACTIVE_LIFECYCLE_STATES`, so margin projection skips it. That property is
real and is asserted below -- but it is INCIDENTAL: it holds because the fold
treats an unknown event type as a no-op, not because anything states session
rows are not position groups. One more state in the active set, or one change
to the fold, and session rows become visible to margin.

So every boundary the enabled runtime uses to enumerate or reconstruct
position groups filters EXPLICITLY, and these tests prove it at each one.
"""
from __future__ import annotations

import datetime as _dt
import logging
import pathlib

import pytest

from bujji.core.clock import IST
from bujji.journal.position_group_journal import PositionGroupJournal
from bujji.production_runtime.position_group_scope import (
    PositionGroupScopedJournal, SESSION_EVENT_TYPE, SESSION_SCOPE_PREFIX,
    SessionScopeViolation, append_scoped_event, assert_scope_consistent,
    is_session_scoped, position_group_events, position_group_ids,
    record_scope_violation, reset_scope_violations, scope_violations,
    session_events, session_scope_id)

CLOCK = lambda: _dt.datetime(2026, 8, 24, 9, 30, tzinfo=IST)   # noqa: E731
LOG = logging.getLogger("test")
SESSION = "S-2026-08-24"
GROUP = "PG-REAL"


def _journal_with_both(tmp_path, name="j.db"):
    """One journal holding a real position group AND a session's own rows."""
    j = PositionGroupJournal(str(tmp_path / name))
    coid = f"{GROUP}-LEG-0"
    j.append_event(GROUP, "MINTED", f"{GROUP}:M",
                   {"plan_id": "p", "strategy_id": "S", "underlying": "NIFTY"}, clock=CLOCK)
    j.append_event(GROUP, "CONSTRUCTED", f"{GROUP}:C", {
        "contract_client_order_map": {"C0": coid},
        "requested_quantities": {coid: 65}, "actions": {coid: "SELL"},
        "target_position_group_ids": {}, "target_contract_ids": {},
        "flip_link_ids": {}}, clock=CLOCK)
    j.append_event(session_scope_id(SESSION), SESSION_EVENT_TYPE,
                   f"{SESSION}:T:1",
                   {"session_id": SESSION, "prior_state": "INITIALIZING",
                    "next_state": "ANALYSING_MARKET", "cause": "session_start",
                    "evidence_ref": "startup"}, clock=CLOCK)
    return j


# --------------------------------------------------------------------------
# The contract itself.
# --------------------------------------------------------------------------

def test_a_session_identity_is_recognised_as_session_scoped():
    assert is_session_scoped(session_scope_id(SESSION))
    assert session_scope_id(SESSION).startswith(SESSION_SCOPE_PREFIX)


@pytest.mark.parametrize("value", ["PG-REAL", "", None, 123, "SESSIONISH"])
def test_a_position_group_is_not_session_scoped(value):
    assert not is_session_scoped(value)


def test_a_session_event_can_be_written_with_no_position_group(tmp_path):
    """A no-trade day mints no group at all. Session history must not depend
    on one existing."""
    j = PositionGroupJournal(str(tmp_path / "empty.db"))
    j.append_event(session_scope_id(SESSION), SESSION_EVENT_TYPE, f"{SESSION}:T:1",
                   {"session_id": SESSION, "prior_state": "INITIALIZING",
                    "next_state": "ANALYSING_MARKET", "cause": "session_start",
                    "evidence_ref": "startup"}, clock=CLOCK)
    assert position_group_ids(j) == [], "no position group exists, and none was invented"
    assert len(session_events(j.read_events(session_scope_id(SESSION)))) == 1


def test_a_transition_to_the_same_state_is_refused(tmp_path):
    from bujji.trading_brain.risk_governor.position_group_validation import IllegalEventError

    j = PositionGroupJournal(str(tmp_path / "x.db"))
    with pytest.raises(IllegalEventError, match="carries no information"):
        j.append_event(session_scope_id(SESSION), SESSION_EVENT_TYPE, f"{SESSION}:T:x",
                       {"session_id": SESSION, "prior_state": "MANAGING",
                        "next_state": "MANAGING", "cause": "noop",
                        "evidence_ref": "none"}, clock=CLOCK)


# --------------------------------------------------------------------------
# BOUNDARY 1: enumeration.
# --------------------------------------------------------------------------

def test_the_raw_journal_DOES_return_session_ids(tmp_path):
    """The reason the filter has to exist. `read_all_group_ids` is frozen and
    returns every identity in the table."""
    j = _journal_with_both(tmp_path)
    assert session_scope_id(SESSION) in j.read_all_group_ids()


def test_the_sanctioned_enumeration_excludes_them(tmp_path):
    j = _journal_with_both(tmp_path)
    assert position_group_ids(j) == [GROUP]


# --------------------------------------------------------------------------
# BOUNDARY 2: recovery and closure enumeration.
# --------------------------------------------------------------------------

def test_recovery_and_closure_enumeration_excludes_session_rows(tmp_path):
    """`all_group_ids` is what BOTH startup recovery and EOD closure walk. A
    session row reaching either would be reconstructed as a position group
    that does not exist -- recovery looking for legs it never had, closure
    trying to flatten it."""
    from bujji.production_runtime.execution_journal_bridge import all_group_ids

    _journal_with_both(tmp_path, name="rec.db")
    ids = all_group_ids(str(tmp_path / "rec.db"))
    assert GROUP in ids
    assert not any(is_session_scoped(g) for g in ids)


# --------------------------------------------------------------------------
# BOUNDARY 3: the frozen consumers, filtered by injection.
# --------------------------------------------------------------------------

def test_the_scoped_journal_hides_session_ids(tmp_path):
    scoped = PositionGroupScopedJournal(_journal_with_both(tmp_path))
    assert scoped.read_all_group_ids() == [GROUP]


def test_the_scoped_journal_refuses_a_session_identity_outright(tmp_path):
    """Not an empty list -- a category error. Silently returning nothing is
    how a caller comes to believe a session has no history."""
    scoped = PositionGroupScopedJournal(_journal_with_both(tmp_path))
    with pytest.raises(ValueError, match="session identity"):
        scoped.read_events(session_scope_id(SESSION))


def test_session_events_are_stripped_from_any_event_list():
    """DEFENCE IN DEPTH, and tested directly because the writer makes it
    otherwise unreachable.

    `session_lifecycle` always writes session rows under a SESSION: identity,
    so a group read never contains one -- a negative control proved this
    filter was unexercised through the journal. It exists for the case the
    writer does not control: a hand-written or operator-corrected row landing
    a session event on a real group id. A fold handed one would treat it as an
    unknown no-op, which is the incidental behaviour this module refuses to
    depend on.
    """
    class _E:
        def __init__(self, t):
            self.event_type = t

    mixed = [_E("MINTED"), _E(SESSION_EVENT_TYPE), _E("FILL_OBSERVED")]
    kept = [e.event_type for e in position_group_events(mixed)]
    assert kept == ["MINTED", "FILL_OBSERVED"]
    assert [e.event_type for e in session_events(mixed)] == [SESSION_EVENT_TYPE]


def test_the_scoped_journal_strips_session_events_from_a_group_read(tmp_path):
    """The same filter through the real journal. Passes trivially today
    because the writer keeps the namespaces apart -- which is why the direct
    test above is the one that proves the filter works."""
    scoped = PositionGroupScopedJournal(_journal_with_both(tmp_path))
    types = {e.event_type for e in scoped.read_events(GROUP)}
    assert SESSION_EVENT_TYPE not in types


def test_the_scoped_journal_delegates_everything_else(tmp_path):
    j = _journal_with_both(tmp_path)
    scoped = PositionGroupScopedJournal(j)
    assert scoped.find_mint_by_plan_id("p") is not None


def test_the_composition_root_scopes_the_journal_before_the_frozen_package():
    """STRUCTURAL, as AST. `LiveRiskContextProvider` is inside byte-frozen
    bujji/trading_brain/ and enumerates groups itself; the filtering therefore
    has to happen where it is CONSTRUCTED."""
    import ast
    import inspect

    import bujji.production_runtime.trading_brain_composition_root as mod

    tree = ast.parse(inspect.getsource(mod))
    wraps = [n for n in ast.walk(tree)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)
             and n.func.id == "PositionGroupScopedJournal"]
    assert wraps, "the journal reaches the frozen package unscoped"

    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
                and call.func.id == "LiveRiskContextProvider"):
            continue
        passed = {k.arg: ast.unparse(k.value) for k in call.keywords}
        assert passed.get("journal") == "scoped_journal", (
            f"LiveRiskContextProvider receives journal={passed.get('journal')!r}, "
            f"not the position-scoped view")


# --------------------------------------------------------------------------
# BOUNDARY 4: margin projection.
# --------------------------------------------------------------------------

def test_a_session_row_is_never_projected_for_margin(tmp_path):
    """Through the SCOPED journal -- the contract -- rather than relying on
    the fold."""
    from bujji.trading_brain.risk_governor.position_group_fold import fold
    from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
        project_whole_book_to_margin_legs)

    raw = _journal_with_both(tmp_path)
    scoped = PositionGroupScopedJournal(raw)

    # What the frozen consumer WOULD have seen without the contract.
    unfiltered = raw.read_all_group_ids()
    assert session_scope_id(SESSION) in unfiltered

    # What it sees now.
    states = [fold(scoped.read_events(g)) for g in scoped.read_all_group_ids()]
    assert states, "the real position group must still be projected"
    assert all(st.position_group_id != session_scope_id(SESSION) for st in states), (
        "a session identity reached margin projection")

    # And an empty book still projects cleanly -- the positive control that
    # keeps the assertion above from passing vacuously.
    assert project_whole_book_to_margin_legs([], {}, {}, {}, "OPTIDX", "MARGIN") == []


def test_the_incidental_fold_protection_still_holds_as_a_second_line(tmp_path):
    """Asserted so its loss is noticed -- but nothing depends on it. If this
    ever fails while the tests above pass, the contract saved us."""
    from bujji.trading_brain.risk_governor.position_group_fold import fold
    from bujji.trading_brain.risk_governor.whole_book_margin_provider import (
        _ACTIVE_LIFECYCLE_STATES)

    j = _journal_with_both(tmp_path)
    state = fold(j.read_events(session_scope_id(SESSION)))
    assert state.lifecycle_state not in _ACTIVE_LIFECYCLE_STATES


# --------------------------------------------------------------------------
# BOUNDARY 5: the restart guard.
# --------------------------------------------------------------------------

def test_the_restart_guard_ignores_session_rows(tmp_path):
    """`prior_fills` decides whether a restarted process may trade. A session
    row carries no fill, but 'carries no fill because of how a fold works' is
    not a reason for a gate this important."""
    from bujji.production_runtime.prior_fills import group_ids_filled_on

    j = _journal_with_both(tmp_path)
    assert group_ids_filled_on(j, "2026-08-24") == []


# --------------------------------------------------------------------------
# The ratchet: nothing in the runtime enumerates unfiltered.
# --------------------------------------------------------------------------

def test_no_runtime_module_calls_read_all_group_ids_directly():
    """The sanctioned enumeration is position_group_scope.position_group_ids.
    A direct call is how session rows re-enter a position-group path."""
    import ast

    repo = pathlib.Path(__file__).resolve().parent.parent
    allowed = {"bujji/production_runtime/position_group_scope.py"}
    offenders = []
    for path in list((repo / "bujji" / "production_runtime").rglob("*.py")) + \
            [repo / "bujji_options_os_runner.py"]:
        rel = str(path.relative_to(repo))
        if rel in allowed:
            continue
        try:
            tree = ast.parse(path.read_text())
        except Exception:
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "read_all_group_ids"):
                offenders.append(f"{rel}:{node.lineno}")
    assert offenders == [], (
        f"unfiltered enumeration in the runtime: {offenders} -- use "
        f"position_group_scope.position_group_ids")


# --------------------------------------------------------------------------
# THE WRITER-SIDE INVARIANT.
#
# Read-side filtering alone means a malformed row is ACCEPTED into the durable
# record and then quietly skipped by every reader -- the same shape as the
# defects this campaign keeps finding: something wrong is written, nothing
# refuses it, and a filter makes it invisible instead of loud.
# --------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def _clean_violations():
    reset_scope_violations()
    yield
    reset_scope_violations()


def test_a_session_event_on_a_real_group_identity_is_refused(tmp_path):
    """The session's own history would land inside an exposure group, where
    every read-side filter strips it -- so the transition would be silently
    lost while the group carried a row meaning nothing to it."""
    j = PositionGroupJournal(str(tmp_path / "w1.db"))
    with pytest.raises(SessionScopeViolation, match="position group"):
        append_scoped_event(
            j, GROUP, SESSION_EVENT_TYPE, f"{SESSION}:bad",
            {"session_id": SESSION, "prior_state": "A", "next_state": "B",
             "cause": "c", "evidence_ref": "e"}, clock=CLOCK, logger=LOG)


def test_a_position_event_on_a_session_identity_is_refused(tmp_path):
    """Worse in the other direction: a fill recorded against a session
    identity is invisible to margin, reconciliation AND closure. Exposure no
    boundary can see is worse than exposure recorded plainly."""
    j = PositionGroupJournal(str(tmp_path / "w2.db"))
    with pytest.raises(SessionScopeViolation, match="invisible"):
        append_scoped_event(
            j, session_scope_id(SESSION), "FILL_OBSERVED", "bad:fill",
            {"client_order_id": "c", "cumulative_filled_quantity_after": 65,
             "delta_quantity": 65, "delta_cost_basis_status": "DERIVED"},
            clock=CLOCK, logger=LOG)


def test_a_refused_write_never_reaches_the_journal(tmp_path):
    """Refused means refused -- not written-then-filtered."""
    j = PositionGroupJournal(str(tmp_path / "w3.db"))
    with pytest.raises(SessionScopeViolation):
        append_scoped_event(
            j, GROUP, SESSION_EVENT_TYPE, f"{SESSION}:bad",
            {"session_id": SESSION, "prior_state": "A", "next_state": "B",
             "cause": "c", "evidence_ref": "e"}, clock=CLOCK, logger=LOG)
    assert j.read_all_group_ids() == [], "a refused event was persisted anyway"


def test_a_refused_write_is_recorded_as_a_fact(tmp_path):
    """The exception carries the failure to the caller; the record carries it
    to the session's evidence, so a rejected write is something an operator
    can read afterwards rather than a log line that scrolled away."""
    j = PositionGroupJournal(str(tmp_path / "w4.db"))
    with pytest.raises(SessionScopeViolation):
        append_scoped_event(
            j, GROUP, SESSION_EVENT_TYPE, f"{SESSION}:bad",
            {"session_id": SESSION, "prior_state": "A", "next_state": "B",
             "cause": "c", "evidence_ref": "e"}, clock=CLOCK, logger=LOG)

    violations = scope_violations()
    assert len(violations) == 1
    assert violations[0]["event_type"] == SESSION_EVENT_TYPE
    assert violations[0]["position_group_id"] == GROUP
    assert violations[0]["detail"]


def test_both_well_formed_combinations_are_accepted(tmp_path):
    """A guard that refuses everything is not a guard."""
    j = PositionGroupJournal(str(tmp_path / "w5.db"))
    append_scoped_event(
        j, session_scope_id(SESSION), SESSION_EVENT_TYPE, f"{SESSION}:ok",
        {"session_id": SESSION, "prior_state": "INITIALIZING",
         "next_state": "ANALYSING_MARKET", "cause": "session_start",
         "evidence_ref": "startup"}, clock=CLOCK, logger=LOG)
    append_scoped_event(
        j, GROUP, "MINTED", f"{GROUP}:ok",
        {"plan_id": "p", "strategy_id": "S", "underlying": "NIFTY"},
        clock=CLOCK, logger=LOG)

    assert scope_violations() == []
    assert position_group_ids(j) == [GROUP]
    assert len(session_events(j.read_events(session_scope_id(SESSION)))) == 1


@pytest.mark.parametrize("group_id, event_type", [
    ("PG-REAL", "FILL_OBSERVED"),
    ("PG-REAL", "MINTED"),
    ("SESSION:S1", "SESSION_TRANSITION"),
])
def test_consistent_scopes_pass_the_assertion(group_id, event_type):
    assert_scope_consistent(group_id, event_type)


@pytest.mark.parametrize("group_id, event_type", [
    ("PG-REAL", "SESSION_TRANSITION"),
    ("SESSION:S1", "FILL_OBSERVED"),
    ("SESSION:S1", "MINTED"),
    ("SESSION:S1", "TARGET_GROUP_REDUCTION_APPLIED"),
])
def test_inconsistent_scopes_fail_the_assertion(group_id, event_type):
    with pytest.raises(SessionScopeViolation):
        assert_scope_consistent(group_id, event_type)


def test_session_scoped_writers_go_through_the_sanctioned_append():
    """THE WRITER-SIDE RATCHET.

    `assert_scope_consistent` only protects what actually flows through
    `append_scoped_event`. A module that reaches `journal.append_event`
    directly bypasses the namespace invariant entirely -- and the modules that
    write under a SESSION: identity are exactly the ones where a misdirected
    event becomes exposure no boundary can see.

    Scoped to the modules that write session-scoped events. It is a ratchet on
    the ones that can do the specific harm, not a blanket ban: the entry path
    writes only to real position groups and is covered by its own tests.
    """
    import ast

    repo = pathlib.Path(__file__).resolve().parent.parent
    must_use_sanctioned_append = [
        "bujji/production_runtime/exit_lifecycle.py",
        "bujji/production_runtime/orphan_exposure.py",
    ]
    offenders = []
    for rel in must_use_sanctioned_append:
        path = repo / rel
        assert path.exists(), f"{rel} no longer exists; this ratchet is stale"
        for node in ast.walk(ast.parse(path.read_text())):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr in ("append_event", "append_linked_events")):
                offenders.append(f"{rel}:{node.lineno}:{node.func.attr}")
    assert offenders == [], (
        f"session-scoped writer bypasses the namespace invariant: {offenders} "
        f"-- use position_group_scope.append_scoped_event, which is the only "
        f"append that checks scope")
