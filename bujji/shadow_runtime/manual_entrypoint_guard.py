"""Manual Entrypoint Guard -- Shadow Runtime, Phase 19.14.1.

Phase 19.14.0's own commissioning audit found two legacy/manual
launcher scripts (`scripts/run_shadow_live_observatory.py`,
`run_live_shadow.py`) that can each independently reach the broker/
capture path if run by hand, without sharing
`bujji.core.process_lock.ProcessLock` (Phase 19.12) with the
authoritative daily runtime. Per that phase's own "prove no duplicate
runtime can exist" requirement, and this phase's explicit instruction
to prefer "explicit lock acquisition, refusal when authoritative
runtime owns the session" over building a second scheduler: this
module is the smallest safe fix -- a single, reusable refusal check
both scripts call at their own entrypoint, before any broker
connection.

NOT a second locking mechanism: reuses `ProcessLock` itself as a
non-blocking probe (attempt-then-immediately-release), the exact same
technique `bujji.shadow_runtime.status._lock_is_live` already
established in Phase 19.12. NOT a scheduler: this module starts
nothing, waits for nothing, and retries nothing -- it only answers
"does the authoritative daily runtime currently own the session," once,
at the moment a manual script starts.
"""
from __future__ import annotations

import os

DEFAULT_LOCK_PATH = "data/daily_intelligence.lock"


class AuthoritativeRuntimeActiveError(Exception):
    """Raised when the authoritative daily runtime currently holds the
    shared lock -- a manual/legacy script must refuse to start, not
    silently proceed and risk a duplicate capture or broker session."""


def refuse_if_authoritative_runtime_active(lock_path: str = DEFAULT_LOCK_PATH) -> None:
    """Call this before any broker connection or capture step in a
    manual/legacy script. Raises `AuthoritativeRuntimeActiveError` if
    the authoritative daily runtime's lock is currently held by a live
    process; returns silently (does nothing else) if the lock is absent
    or acquirable."""
    if not os.path.exists(lock_path):
        return  # nothing holds it -- safe to proceed.

    from bujji.core.process_lock import LockAcquisitionError, ProcessLock

    probe = ProcessLock(lock_path)
    try:
        probe.acquire()
    except LockAcquisitionError as exc:
        raise AuthoritativeRuntimeActiveError(
            f"The authoritative Bujji daily intelligence runtime currently owns {lock_path!r} -- "
            "refusing to start this manual/legacy script to avoid a duplicate capture or broker "
            "session. Wait for the daily runtime to finish, or stop it first if you intend to run "
            "this manually instead."
        ) from exc
    else:
        probe.release()  # was acquirable -- this is a probe, not real ownership; release immediately.
