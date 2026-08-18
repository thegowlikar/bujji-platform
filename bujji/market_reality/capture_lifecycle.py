"""Capture-event lifecycle tracker — Phase 17F.5 gap closure.

THE GAP THIS CLOSES: `CaptureEvent` (Phase 17F.0.1) and
`RawObservationStore.append_capture_event()` (Phase 17E) have existed
since early in this project, fully implemented and tested -- but the
17F.5 completeness audit found that NOTHING actually emits one. Every
collector this project has built so far either doesn't run live yet, or
(the futures depth poller) runs in DISCOVERY mode, which deliberately
touches no store at all. A capture run without a capture-event emitter
would produce a log that LOOKS complete and is not -- a gap becomes
silently indistinguishable from "nothing happened," which is exactly the
failure mode Phase 17F.0.1 built `CaptureEvent` to prevent. This module
is the missing piece that actually turns "the collector's connection
just broke" into a permanent, replayable fact.

WHAT THIS MODULE IS NOT: it does not connect to a broker, read a
websocket, or classify an exception. Deliberately -- Layer 0 must never
import `bujji.broker` (enforced by `test_market_reality_safety.py`), and
a collector script already knows exactly what happened when it catches
an exception. This module's only job is the STATEFUL PART a collector
would otherwise have to reimplement itself: deciding whether a failure
is new (emit) or ongoing (do not re-emit every poll cycle), and linking
a recovery back to the failure it closes.

WHY OPEN/CLOSE PAIRING, NOT A DURATION FIELD: Layer 0 is immutable
(Phase 17F.0.1's own design note) -- a "gap record" cannot be opened when
a disconnect starts and closed when it ends, because closing it would be
a mutation the store forbids. Instead each condition is a POINT event,
and `RECONNECT_RECOVERED` links back via `related_event_id`. An interval
is reconstructed later by reading the pair; an UNCLOSED interval (process
killed before recovery) is the honest representation of a gap whose end
was genuinely never observed -- this tracker does not paper over that by
inventing a synthetic recovery.

IDEMPOTENT WHILE OPEN, DELIBERATELY: a collector polling every 60 seconds
against a broken connection would otherwise emit a fresh DISCONNECT
event every single cycle, flooding the log with duplicates that all
describe the same, still-ongoing fact. `record_condition()` emits once
per condition and stays silent (returns None, not an error) on repeated
calls until `record_recovery()` closes it -- exactly one event per real
transition, matching `CaptureEvent`'s own "same discipline `observation_id`
provides" content-identity philosophy from a different angle: identity
here is "this open condition," not "this payload."

No wall-clock is read here, same discipline as every other Layer 0
module -- every timestamp is supplied by the caller.
"""
from __future__ import annotations

from typing import Optional, Tuple

from .capture_events import build_capture_event
from .models import AppendResult
from .taxonomy import (
    REASON_AUTH_FAILURE,
    REASON_COLLECTOR_RESTART,
    REASON_DISCONNECT,
    REASON_QUEUE_OVERFLOW,
    REASON_RATE_LIMIT_SKIP,
    REASON_RECONNECT_RECOVERED,
    REASON_SHUTDOWN_DRAIN_INCOMPLETE,
)

# Conditions that represent an ONGOING state -- something that started at
# one moment and is only meaningfully "over" when a recovery is recorded.
# A repeated failure of one of these while already open is the SAME fact,
# not a new one.
OPENING_REASONS = (REASON_DISCONNECT, REASON_AUTH_FAILURE, REASON_SHUTDOWN_DRAIN_INCOMPLETE)

# Conditions that are each, individually, a complete fact the instant they
# happen -- no open/close pairing applies, and none is ever deduplicated
# against a prior one.
POINT_REASONS = (REASON_QUEUE_OVERFLOW, REASON_RATE_LIMIT_SKIP, REASON_COLLECTOR_RESTART)


class CaptureLifecycleTracker:
    """Per-(source, access_method) capture-condition state, held in memory
    only. A restart of the collector process starts a fresh tracker with
    no open condition -- which is honest: this tracker's in-memory state
    is not itself Layer 0 fact, only the events it emits are. If a
    collector crashes mid-disconnect, the DISCONNECT event it already
    emitted stays correctly unclosed in the permanent log (see the module
    docstring); a restarted tracker simply has nothing to close, and a
    fresh `record_condition()` call afterward would correctly start a NEW
    condition rather than misattribute itself to the old one.
    """

    def __init__(self, *, store, source: str, access_method: str) -> None:
        self._store = store
        self._source = source
        self._access_method = access_method
        self._open_event_id: Optional[str] = None
        self._open_reason: Optional[str] = None

    @property
    def has_open_condition(self) -> bool:
        return self._open_event_id is not None

    @property
    def open_reason(self) -> Optional[str]:
        return self._open_reason

    def record_condition(
        self,
        *,
        reason: str,
        event_time: str,
        knowledge_time: str,
        detail: Optional[str] = None,
        affected_instruments: Tuple[str, ...] = (),
        count: Optional[int] = None,
        certification_status: Optional[str] = None,
    ) -> Optional[AppendResult]:
        """Record an ONGOING condition (disconnect / auth failure /
        shutdown-drain-incomplete). Emits once; a repeated call while the
        SAME condition is still open is a silent no-op (returns None) --
        not an error, since "still disconnected" is not a new fact.

        A caller wanting to record a genuinely NEW condition of a
        different reason while one is already open should call
        `record_recovery()` first -- this method does not implicitly
        close/replace an open condition, since that decision belongs to
        the caller, who knows whether the two are actually related.
        """
        if reason not in OPENING_REASONS:
            raise ValueError(
                f"record_condition() only accepts an opening reason "
                f"{OPENING_REASONS!r}, got {reason!r} -- use "
                f"record_point_event() for a standalone occurrence."
            )
        if self._open_event_id is not None:
            return None  # Already recorded; this is the same ongoing fact.

        event = build_capture_event(
            reason=reason, event_time=event_time, knowledge_time=knowledge_time,
            source=self._source, access_method=self._access_method,
            affected_instruments=affected_instruments, count=count,
            certification_status=certification_status, detail=detail,
        )
        result = self._store.append_capture_event(event)
        self._open_event_id = event.event_id
        self._open_reason = reason
        return result

    def record_recovery(
        self,
        *,
        event_time: str,
        knowledge_time: str,
        detail: Optional[str] = None,
        certification_status: Optional[str] = None,
    ) -> Optional[AppendResult]:
        """Close the open condition (if any) with a RECONNECT_RECOVERED
        event linked via `related_event_id`. A no-op (returns None, not
        an error) if nothing was open -- a "recovery" with no recorded
        failure is not itself a fact worth asserting; it would imply a
        disconnect this tracker never saw."""
        if self._open_event_id is None:
            return None

        event = build_capture_event(
            reason=REASON_RECONNECT_RECOVERED, event_time=event_time,
            knowledge_time=knowledge_time, source=self._source,
            access_method=self._access_method, related_event_id=self._open_event_id,
            certification_status=certification_status, detail=detail,
        )
        result = self._store.append_capture_event(event)
        self._open_event_id = None
        self._open_reason = None
        return result

    def record_point_event(
        self,
        *,
        reason: str,
        event_time: str,
        knowledge_time: str,
        count: Optional[int] = None,
        detail: Optional[str] = None,
        affected_instruments: Tuple[str, ...] = (),
        certification_status: Optional[str] = None,
    ) -> AppendResult:
        """Record a standalone point event (queue overflow / rate-limit
        skip / collector restart). Always emitted -- each occurrence is a
        distinct, real fact, never deduplicated against a prior one (a
        second queue overflow five minutes after the first is not the
        same overflow)."""
        if reason not in POINT_REASONS:
            raise ValueError(
                f"record_point_event() only accepts a point reason "
                f"{POINT_REASONS!r}, got {reason!r} -- use "
                f"record_condition() for an ongoing disconnect/auth-failure/"
                f"shutdown-drain-incomplete condition."
            )
        event = build_capture_event(
            reason=reason, event_time=event_time, knowledge_time=knowledge_time,
            source=self._source, access_method=self._access_method,
            affected_instruments=affected_instruments, count=count,
            certification_status=certification_status, detail=detail,
        )
        return self._store.append_capture_event(event)
