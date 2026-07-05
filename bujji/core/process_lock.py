"""Single-instance process lock (F4).

Prevents two orchestrators from ever running against the same session state
and broker account simultaneously. A double-start — a systemd restart race, an
operator manually launching a second instance without checking, a stuck old
process not yet reaped — would otherwise let two independent processes each
believe they own the day's one trade, both poll the broker, and potentially
place duplicate/conflicting orders while stomping on each other's writes to the
shared session snapshot. This is a startup-time gate; it carries no trading
logic.

Uses an OS-level advisory lock (`flock`) on a dedicated lock file, scoped to
the *open file description* rather than a PID recorded in a file. This matters
operationally: a stale PID file (from a crashed process) can wedge a legitimate
restart forever if a human has to notice and delete it. `flock` has no such
failure mode — the moment the process holding the lock dies for any reason
(clean exit, crash, `kill -9`, OOM), the OS releases the lock automatically, so
a genuine restart is never blocked by a *dead* prior instance, only by a truly
*live* second one.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import IO, Optional

try:
    import fcntl
    _HAS_FLOCK = True
except ImportError:  # pragma: no cover - non-POSIX platforms (e.g. Windows).
    fcntl = None  # type: ignore[assignment]
    _HAS_FLOCK = False


class LockAcquisitionError(RuntimeError):
    """Raised when another live instance already holds the lock."""


class ProcessLock:
    """Exclusive, advisory, crash-safe single-instance lock."""

    def __init__(self, path: Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._fh: Optional[IO[str]] = None

    @property
    def enforced(self) -> bool:
        """Whether this platform can actually enforce the lock."""
        return _HAS_FLOCK

    def acquire(self) -> None:
        """Acquire the lock or raise :class:`LockAcquisitionError`.

        Safe to call multiple times from the same instance (idempotent no-op
        once held). Must be called once per process, as early in startup as
        possible — before connecting to the broker — so a duplicate instance
        fails fast without ever touching live trading infrastructure.
        """
        if self._fh is not None:
            return  # Already held by this instance.
        if not _HAS_FLOCK:
            # Best-effort platform: do not fabricate a guarantee we cannot
            # enforce, but do not block startup on a platform without flock
            # either (e.g. local Windows development).
            return
        fh = open(self._path, "w", encoding="utf-8")
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            fh.close()
            raise LockAcquisitionError(
                f"Another instance already holds the lock at '{self._path}'. "
                f"Refusing to start a second instance against the same "
                f"session state / broker account. If you are certain no other "
                f"instance is running, check for a stuck process before "
                f"removing this file."
            ) from exc
        fh.write(str(os.getpid()))
        fh.flush()
        self._fh = fh

    def release(self) -> None:
        """Release the lock. Safe to call even if never acquired."""
        if self._fh is None:
            return
        try:
            if _HAS_FLOCK:
                fcntl.flock(self._fh.fileno(), fcntl.LOCK_UN)
        finally:
            self._fh.close()
            self._fh = None

    def __enter__(self) -> "ProcessLock":
        self.acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.release()
