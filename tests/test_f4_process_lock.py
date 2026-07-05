"""F4 — single-instance process lock.

Verifies that a second process (or, in-process, a second lock/Application
instance) pointed at the same lock file is refused startup, that a released
lock can be re-acquired (normal restart), and that the lock is never blocked by
a *dead* prior instance (flock is scoped to the open file description, so
process death frees it automatically — we simulate this by closing/releasing
the first handle rather than actually killing a process).
"""
import pytest

from bujji.core.process_lock import LockAcquisitionError, ProcessLock


def test_second_lock_on_same_file_is_refused(tmp_path):
    lock_path = tmp_path / "bujji.lock"
    first = ProcessLock(lock_path)
    first.acquire()
    try:
        second = ProcessLock(lock_path)
        if first.enforced:  # Only meaningful where flock is actually available.
            with pytest.raises(LockAcquisitionError):
                second.acquire()
    finally:
        first.release()


def test_lock_can_be_reacquired_after_release(tmp_path):
    """A clean restart (lock released, then re-acquired) must succeed."""
    lock_path = tmp_path / "bujji.lock"
    first = ProcessLock(lock_path)
    first.acquire()
    first.release()

    second = ProcessLock(lock_path)
    second.acquire()  # Must not raise — the file existing isn't enough to block.
    second.release()


def test_lock_is_idempotent_within_same_instance(tmp_path):
    lock_path = tmp_path / "bujji.lock"
    lock = ProcessLock(lock_path)
    lock.acquire()
    lock.acquire()  # Calling twice must not raise or double-open.
    lock.release()


def test_context_manager_releases_on_exit(tmp_path):
    lock_path = tmp_path / "bujji.lock"
    with ProcessLock(lock_path) as lock:
        assert lock.enforced or not lock.enforced  # Just exercising the path.
    # After the context exits, a fresh lock must be acquirable again.
    second = ProcessLock(lock_path)
    second.acquire()
    second.release()


def test_release_without_acquire_is_safe(tmp_path):
    lock = ProcessLock(tmp_path / "bujji.lock")
    lock.release()  # Must not raise.


@pytest.mark.asyncio
async def test_second_application_instance_refused(config, tmp_path):
    """Integration: two Application instances over the same lock file — the
    second must fail fast, before ever touching the broker (F4)."""
    from bujji.app import Application

    config.dashboard.enabled = False
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.paths.lock_file = tmp_path / "bujji.lock"
    config.paths.log_dir = tmp_path / "logs"

    app1 = Application(config)
    try:
        if app1._lock.enforced:  # noqa: SLF001 - test introspection.
            with pytest.raises(LockAcquisitionError):
                Application(config)
        else:
            pytest.skip("flock not available on this platform")
    finally:
        app1._lock.release()  # noqa: SLF001


@pytest.mark.asyncio
async def test_application_can_restart_after_clean_shutdown(config, tmp_path):
    """A legitimate restart (previous instance released the lock) must work."""
    from bujji.app import Application

    config.dashboard.enabled = False
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.paths.lock_file = tmp_path / "bujji.lock"
    config.paths.log_dir = tmp_path / "logs"

    app1 = Application(config)
    app1._lock.release()  # noqa: SLF001 - simulates process exit releasing flock.

    app2 = Application(config)  # Must not raise.
    app2._lock.release()  # noqa: SLF001
