"""Backup rotation for the FYERS token promote script.

Every file this touches contains a WORKING CREDENTIAL, and the function's
only job is to delete things. So these tests are mostly about what it must
refuse to delete: unrecognised names, unparseable timestamps, and anything
that is not exactly `.env.bak-YYYYMMDDTHHMMSS`.

Ordering is asserted against the timestamp in the NAME rather than mtime,
because copying or restoring a backup rewrites mtime and would silently
reorder the pile -- pruning the wrong five.
"""
from __future__ import annotations

import importlib.util
import os
import time
from pathlib import Path

import pytest

SCRIPT = Path("/opt/bujji/app/scripts/promote_fyers_token.py")
pytestmark = pytest.mark.skipif(not SCRIPT.exists(), reason="VPS-only script")


def _mod():
    spec = importlib.util.spec_from_file_location("_promote_under_test", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _backup(directory: Path, stamp: str, body: str = "FYERS_ACCESS_TOKEN=x\n") -> Path:
    p = directory / f".env.bak-{stamp}"
    p.write_text(body)
    return p


class TestKeepsTheNewest:
    def test_prunes_down_to_the_limit(self, tmp_path):
        for i in range(8):
            _backup(tmp_path, f"2026081{i}T090000")
        kept, removed, skipped = _mod().rotate_backups(tmp_path, keep=5)
        assert len(kept) == 5 and len(removed) == 3 and skipped == []
        assert len(list(tmp_path.glob(".env.bak-*"))) == 5

    def test_keeps_the_newest_not_an_arbitrary_five(self, tmp_path):
        for stamp in ("20260810T090000", "20260814T090000", "20260812T090000",
                      "20260818T090000", "20260811T090000", "20260816T090000"):
            _backup(tmp_path, stamp)
        kept, removed, _ = _mod().rotate_backups(tmp_path, keep=2)
        assert {p.name for p in kept} == {".env.bak-20260818T090000",
                                          ".env.bak-20260816T090000"}
        assert len(removed) == 4

    def test_ordering_uses_the_name_not_mtime(self, tmp_path):
        """A restore or a copy rewrites mtime. If rotation trusted it, the
        oldest backup could look newest and the real ones would be pruned."""
        old = _backup(tmp_path, "20260101T000000")
        new = _backup(tmp_path, "20260818T000000")
        # Make the OLDEST file the most recently touched.
        os.utime(old, (time.time() + 10_000, time.time() + 10_000))
        kept, removed, _ = _mod().rotate_backups(tmp_path, keep=1)
        assert kept == [new], "mtime won over the name -- wrong file kept"
        assert removed == [old]

    def test_under_the_limit_deletes_nothing(self, tmp_path):
        for i in range(3):
            _backup(tmp_path, f"2026081{i}T090000")
        kept, removed, skipped = _mod().rotate_backups(tmp_path, keep=5)
        assert len(kept) == 3 and removed == [] and skipped == []

    def test_empty_directory_is_fine(self, tmp_path):
        assert _mod().rotate_backups(tmp_path, keep=5) == ([], [], [])


class TestRefusesToTouchAnythingElse:
    def test_the_live_env_file_is_never_a_candidate(self, tmp_path):
        """The whole point of the strict pattern."""
        live = tmp_path / ".env"
        live.write_text("FYERS_ACCESS_TOKEN=real\n")
        for i in range(8):
            _backup(tmp_path, f"2026081{i}T090000")
        _mod().rotate_backups(tmp_path, keep=1)
        assert live.exists() and live.read_text() == "FYERS_ACCESS_TOKEN=real\n"

    @pytest.mark.parametrize("name", [
        ".env.bak-before-migration",   # a human's own backup
        ".env.bak",                    # no stamp at all
        ".env.bak-20260818",           # date only, no time
        ".env.bak-20260818T09000",     # one digit short
        "env.bak-20260818T090000",     # missing the leading dot
        ".env.backup-20260818T090000",
    ])
    def test_names_outside_the_exact_pattern_survive(self, tmp_path, name):
        """A glob like `.env.bak-*` would eat several of these."""
        stray = tmp_path / name
        stray.write_text("do not delete me\n")
        for i in range(8):
            _backup(tmp_path, f"2026081{i}T090000")
        _mod().rotate_backups(tmp_path, keep=1)
        assert stray.exists(), f"{name} was deleted"

    def test_an_impossible_timestamp_is_skipped_not_deleted(self, tmp_path):
        """Matches the shape, cannot be a real date. Unrecognised is a
        reason to stop, not a reason to guess."""
        weird = _backup(tmp_path, "20261345T990000")
        for i in range(8):
            _backup(tmp_path, f"2026081{i}T090000")
        kept, removed, skipped = _mod().rotate_backups(tmp_path, keep=1)
        assert weird.exists()
        assert weird in skipped
        assert weird not in removed

    def test_directories_are_ignored(self, tmp_path):
        d = tmp_path / ".env.bak-20260818T090000"
        d.mkdir()
        kept, removed, skipped = _mod().rotate_backups(tmp_path, keep=0)
        assert d.is_dir() and removed == []


class TestWiredIntoThePromote:
    def test_rotation_runs_after_the_backup_is_written(self):
        """Pruning before the new backup exists could leave nothing to fall
        back to if the write then failed."""
        source = SCRIPT.read_text()
        backup_at = source.index("shutil.copy2(TARGET, backup)")
        rotate_at = source.index("rotate_backups(TARGET.parent")
        assert backup_at < rotate_at

    def test_check_mode_never_rotates(self):
        """--check writes nothing, so it must delete nothing either."""
        source = SCRIPT.read_text()
        check_return = source.index("if args.check:")
        rotate_at = source.index("rotate_backups(TARGET.parent")
        assert check_return < rotate_at

    def test_the_default_limit_is_five(self):
        assert _mod().BACKUPS_TO_KEEP == 5


class TestUnmanagedLookAlikes:
    """Rotation's strict pattern is right, but it makes look-alikes
    INVISIBLE -- never counted, never pruned, holding a live credential.
    Found for real on 2026-08-18: four such files, three of which a
    `.env.bak-*` glob does not even match."""

    def test_reports_the_dot_separated_form(self, tmp_path):
        for name in (".env.bak.pre-refresh", ".env.bak.pre-sync", ".env.bak.premarket"):
            (tmp_path / name).write_text("FYERS_ACCESS_TOKEN=old\n")
        found = {p.name for p in _mod().unmanaged_backups(tmp_path)}
        assert found == {".env.bak.pre-refresh", ".env.bak.pre-sync", ".env.bak.premarket"}

    def test_reports_a_stamp_missing_its_T_separator(self, tmp_path):
        (tmp_path / ".env.bak-20260815224954").write_text("FYERS_ACCESS_TOKEN=old\n")
        assert [p.name for p in _mod().unmanaged_backups(tmp_path)] == [
            ".env.bak-20260815224954"]

    def test_managed_backups_are_not_reported(self, tmp_path):
        _backup(tmp_path, "20260818T090000")
        assert _mod().unmanaged_backups(tmp_path) == []

    def test_the_live_env_file_is_not_reported(self, tmp_path):
        (tmp_path / ".env").write_text("FYERS_ACCESS_TOKEN=real\n")
        assert _mod().unmanaged_backups(tmp_path) == []

    def test_reporting_never_deletes(self, tmp_path):
        stray = tmp_path / ".env.bak.premarket"
        stray.write_text("FYERS_ACCESS_TOKEN=old\n")
        _mod().unmanaged_backups(tmp_path)
        assert stray.exists()
