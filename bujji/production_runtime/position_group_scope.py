"""The boundary between session-scoped and position-scoped journal rows.

M4 put session lifecycle into the SAME durable event authority as position
lifecycle -- one journal, one transaction domain, no second store. Session rows
carry a `SESSION:<session_id>` identity in the `position_group_id` column and a
`SESSION_TRANSITION` event type.

WHY THIS MODULE EXISTS RATHER THAN A COMMENT.

A session-scoped identity happens to fold to LIFECYCLE_MINTED, because
`apply_event_to_state` does not recognise SESSION_TRANSITION and so leaves
`constructed=False`; and MINTED is not in
`whole_book_margin_provider._ACTIVE_LIFECYCLE_STATES`. That is a real property
and it is verified -- but it is INCIDENTAL. It holds because of how a fold
happens to treat an unknown type today, not because anything states that
session rows are not position groups. A future change to the fold, or one more
state added to the active set, would silently make session rows visible to
margin projection.

So exclusion is stated here, as a contract, and applied EXPLICITLY at every
boundary the enabled trading runtime uses to enumerate or reconstruct position
groups. The incidental property remains as a second line of defence, and
`tools/nc_frozen_authorisation.py` refuses any change that widens the active
set -- but nothing depends on it.

THE FROZEN BOUNDARIES ARE HANDLED BY INJECTION, NOT BY EDITING.
`live_risk_context_provider` and `governor_context_builder` are inside the
byte-frozen `bujji/trading_brain/` package and call `read_all_group_ids()`
themselves. `trading_brain_composition_root` -- which is NOT frozen -- now
hands them `PositionGroupScopedJournal` instead of the raw journal, so their
enumeration is filtered at the boundary without a frozen line changing.
"""
from __future__ import annotations

from typing import Any, Iterable, List

# The identity namespace session rows occupy in the position_group_id column.
SESSION_SCOPE_PREFIX = "SESSION:"

# The only event type that may carry a session-scoped identity.
SESSION_EVENT_TYPE = "SESSION_TRANSITION"


def session_scope_id(session_id: str) -> str:
    """The journal identity for a session's own lifecycle rows."""
    return f"{SESSION_SCOPE_PREFIX}{session_id}"


def is_session_scoped(group_id: Any) -> bool:
    """True for an identity that names a SESSION, not a position group.

    The single place this question is answered. Every enumeration below, and
    every caller in the enabled runtime, asks it here rather than testing a
    prefix inline -- so the namespace can never mean two things.
    """
    return isinstance(group_id, str) and group_id.startswith(SESSION_SCOPE_PREFIX)


def position_group_ids(journal) -> List[str]:
    """Every POSITION group in the journal, session identities removed.

    THE SANCTIONED ENUMERATION. `PositionGroupJournal.read_all_group_ids()`
    returns every distinct identity in the table, including session ones; it
    lives in the frozen package and cannot filter. Nothing in the enabled
    runtime may call it directly -- see
    tests/test_session_scope_is_excluded.py, which ratchets that.
    """
    return [g for g in journal.read_all_group_ids() if not is_session_scoped(g)]


def position_group_events(events: Iterable[Any]) -> List[Any]:
    """Only the events that describe POSITION lifecycle.

    A fold must never be handed a SESSION_TRANSITION: the fold is frozen and
    treats an unknown type as a no-op, which is harmless today and is exactly
    the incidental behaviour this module refuses to rely on.
    """
    return [e for e in events if getattr(e, "event_type", None) != SESSION_EVENT_TYPE]


def session_events(events: Iterable[Any]) -> List[Any]:
    """The complement: only session lifecycle. Used by the session state
    reconstruction, and by nothing that reasons about exposure."""
    return [e for e in events if getattr(e, "event_type", None) == SESSION_EVENT_TYPE]


class PositionGroupScopedJournal:
    """A read-through view of the journal that shows POSITION scope only.

    Handed to consumers inside the frozen package so their enumeration is
    filtered at the boundary. It delegates everything else untouched, and it
    deliberately exposes no write path: a caller that needs to append reaches
    the real journal.
    """

    def __init__(self, journal) -> None:
        self._journal = journal

    # -- the two reads that could see session scope --------------------- #
    def read_all_group_ids(self) -> List[str]:
        return position_group_ids(self._journal)

    def read_events(self, position_group_id: str) -> List[Any]:
        if is_session_scoped(position_group_id):
            # Asking a POSITION-scoped view for a session's events is a
            # category error, not an empty result to be worked around.
            raise ValueError(
                f"{position_group_id!r} is a session identity, not a position "
                f"group. Session lifecycle is read through "
                f"bujji.production_runtime.session_lifecycle, never through a "
                f"position-group boundary.")
        return position_group_events(self._journal.read_events(position_group_id))

    # -- everything else is the real journal ---------------------------- #
    def __getattr__(self, name: str) -> Any:
        return getattr(self._journal, name)

# ---------------------------------------------------------------------------
# THE WRITER-SIDE INVARIANT.
#
# Everything above filters on the READ side. Filtering alone means a malformed
# row is accepted into the durable record and then quietly skipped by every
# reader -- which is the same shape as the defects this campaign keeps finding:
# something wrong is written, nothing refuses it, and a downstream filter makes
# it invisible instead of loud.
#
# The namespace cannot be enforced inside `validate_event`: that function
# receives (event_type, payload, recorded_at, current_state) and never sees the
# position_group_id, and changing its signature is a frozen call site. So the
# invariant is enforced HERE, at the one sanctioned append, before the row
# reaches the journal at all.
# ---------------------------------------------------------------------------


class SessionScopeViolation(Exception):
    """An event whose type and identity namespace disagree.

    Never caught-and-continued by a caller: it means the writer confused
    session lifecycle with position lifecycle, and the two decide different
    things. Recorded durably by `record_scope_violation` so the attempt is not
    lost with the exception.
    """


def assert_scope_consistent(position_group_id, event_type) -> None:
    """Refuse an event whose identity and type belong to different scopes.

    BOTH DIRECTIONS MATTER, and for different reasons:

      SESSION_TRANSITION on a real group id -- the session's own history
        lands inside an exposure group. Every read-side filter above would
        strip it, so the session would silently lose a transition while the
        group carried a row that means nothing to it.

      any other type on a SESSION: id -- a fill, a reduction or a cancel
        recorded against a session identity. `position_group_ids` excludes
        that identity, so the row would be invisible to margin, to
        reconciliation, and to closure: real exposure in the journal that no
        position-group boundary can see.
    """
    session_id_shaped = is_session_scoped(position_group_id)
    if event_type == SESSION_EVENT_TYPE and not session_id_shaped:
        raise SessionScopeViolation(
            f"{SESSION_EVENT_TYPE} may only be written under a "
            f"{SESSION_SCOPE_PREFIX!r} identity; {position_group_id!r} is a "
            f"position group. Session history inside an exposure group is "
            f"stripped by every read-side filter and would be lost.")
    if event_type != SESSION_EVENT_TYPE and session_id_shaped:
        raise SessionScopeViolation(
            f"{event_type!r} may not be written under the session identity "
            f"{position_group_id!r}. Position events there are invisible to "
            f"margin, reconciliation and closure -- exposure no boundary can "
            f"see is worse than exposure recorded plainly.")


_scope_violations: List[dict] = []


def record_scope_violation(position_group_id, event_type, detail, logger=None) -> dict:
    """Keep the refused attempt. The exception carries the failure to the
    caller; this carries it to the session's evidence, so a rejected write is
    a fact an operator can read afterwards rather than a log line that scrolls
    away."""
    record = {"position_group_id": str(position_group_id),
              "event_type": str(event_type), "detail": str(detail)}
    _scope_violations.append(record)
    if logger is not None:
        logger.critical(
            "SCOPE VIOLATION -- refused to journal %s under %r: %s",
            event_type, position_group_id, detail)
    return record


def scope_violations() -> List[dict]:
    """Every refused write this process attempted. Surfaced in the session
    summary; a non-empty list makes the session unsafe."""
    return list(_scope_violations)


def reset_scope_violations() -> None:
    """Test-only. Production never clears this -- a violation that happened
    happened."""
    _scope_violations.clear()


def append_scoped_event(journal, position_group_id, event_type, idempotency_key,
                        payload, clock, logger=None):
    """THE ONE SANCTIONED APPEND. Enforces the namespace, then delegates.

    A caller that reaches `journal.append_event` directly bypasses this; the
    ratchet in tests/test_session_scope_is_excluded.py refuses that.
    """
    try:
        assert_scope_consistent(position_group_id, event_type)
    except SessionScopeViolation as exc:
        record_scope_violation(position_group_id, event_type, str(exc), logger)
        raise
    return journal.append_event(position_group_id, event_type, idempotency_key,
                                payload, clock=clock)
