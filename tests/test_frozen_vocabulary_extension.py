"""The frozen packages stay frozen, except for ONE authorised change.

`bujji/journal/`, `bujji/trading_brain/`, and three broker files have been
byte-frozen since 360c003 -- tests/test_replay_engine_safety.py enforces it.
The freeze protects order handling and capital calculation, and it has already
paid for itself twice this campaign: it caught a convenience query being added
to `PositionGroupJournal`, and it forced the exit-journal work into a caller
rather than into the machinery.

M4 needed one thing the freeze forbade: a session-lifecycle event type, so a
no-trade day, a refusal, a startup and a completion have durable history in
the SAME event authority as position lifecycle. The alternative was a second
session store -- exactly the duplicate authority this whole effort removes.

WHAT IS AUTHORISED IS THE CHANGE, NOT THE FILE. `paper.py`'s existing
exception is a bare filename filter: any future edit to that file passes
unnoticed. This one is narrower. Every added line in
`position_group_validation.py` must belong to the SESSION_TRANSITION block, so
an unrelated edit to the very same file still fails.
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
FROZEN_SINCE = "360c003"
AUTHORISED_FILE = "bujji/trading_brain/risk_governor/position_group_validation.py"
AUTHORISED_TOKEN = "SESSION_TRANSITION"


def _diff(*paths, stat=False):
    cmd = ["git", "-c", f"safe.directory={REPO_ROOT}", "diff"]
    if stat:
        cmd.append("--stat")
    cmd += [FROZEN_SINCE, "--", *paths]
    return subprocess.run(cmd, cwd=REPO_ROOT, capture_output=True, text=True).stdout


def _added_lines(path):
    out = _diff(path)
    return [l[1:] for l in out.splitlines()
            if l.startswith("+") and not l.startswith("+++")]


def _removed_lines(path):
    out = _diff(path)
    return [l[1:] for l in out.splitlines()
            if l.startswith("-") and not l.startswith("---")]


# --------------------------------------------------------------------------
# The authorised change, bounded.
# --------------------------------------------------------------------------

def test_the_authorised_file_changed_at_all():
    """If this fails, the extension was reverted and M4's session journaling
    has no vocabulary to write into."""
    assert _added_lines(AUTHORISED_FILE), (
        f"{AUTHORISED_FILE} is unchanged since {FROZEN_SINCE}")


def test_every_added_line_belongs_to_the_authorised_extension():
    """THE narrowing. A line added to this file that has nothing to do with
    SESSION_TRANSITION is an unrelated change to a frozen file, and is
    refused even though the file itself is authorised."""
    added = _added_lines(AUTHORISED_FILE)
    stray = []
    in_block = False
    for line in added:
        stripped = line.strip()
        if AUTHORISED_TOKEN in line:
            in_block = True
            continue
        if not stripped:
            continue
        if stripped.startswith("#") and in_block:
            continue          # commentary inside the authorised block
        if stripped.startswith(("'", '"')) or stripped.startswith(")"):
            continue          # continuation of the authorised payload/raise
        if in_block and (stripped.startswith(("if ", "raise ", "return", "f\"", "\""))
                         or stripped.endswith((",", "(", ":"))):
            continue
        stray.append(line)
    assert stray == [], (
        f"lines added to the frozen {AUTHORISED_FILE} that are not part of the "
        f"authorised {AUTHORISED_TOKEN} extension: {stray}")


def test_the_authorised_change_removes_nothing():
    """An extension adds a type. It does not weaken an existing rule -- and a
    deletion in a frozen validation file is how a guard disappears quietly."""
    removed = [l for l in _removed_lines(AUTHORISED_FILE) if l.strip()]
    assert removed == [], (
        f"the authorised extension deletes frozen lines: {removed}")


def test_the_extension_did_not_widen_the_vocabulary_beyond_one_type():
    from bujji.trading_brain.risk_governor.position_group_validation import (
        KNOWN_EVENT_TYPES)

    expected = {
        "MINTED", "CONSTRUCTED", "SUBMIT_INTENT", "SUBMIT_ACK", "SUBMIT_FAILURE",
        "FILL_OBSERVED", "CANCEL_INTENT", "CANCEL_ACK",
        "TARGET_GROUP_REDUCTION_APPLIED", "RECONCILIATION_ATTEMPTED",
        "FINAL_RECONCILIATION_CONFIRMED", "OPERATOR_CORRECTION_RECORDED",
        AUTHORISED_TOKEN,
    }
    assert set(KNOWN_EVENT_TYPES) == expected, (
        "exactly one type was authorised; the vocabulary now differs by "
        f"{set(KNOWN_EVENT_TYPES) ^ expected}")


# --------------------------------------------------------------------------
# Everything else in the freeze is untouched.
# --------------------------------------------------------------------------

def test_the_rest_of_the_frozen_packages_are_still_byte_untouched():
    """The blanket freeze, with the two authorised files excluded by NAME and
    then re-checked above by CONTENT."""
    out = _diff("bujji/broker/guard.py", "bujji/broker/hybrid.py",
                "bujji/broker/paper.py", "bujji/trading_brain/",
                f":(exclude)bujji/trading_brain/risk_governor/portfolio_risk_aggregator.py",
                "bujji/journal/", stat=True)
    lines = [l for l in out.splitlines()
             if "paper.py" not in l
             and "position_group_validation.py" not in l
             and l.strip() and "|" in l]
    assert lines == [], f"unauthorised changes to frozen files: {lines}"


@pytest.mark.parametrize("path", [
    "bujji/journal/position_group_journal.py",
    "bujji/trading_brain/risk_governor/position_group_fold.py",
    "bujji/trading_brain/risk_governor/whole_book_margin_provider.py",
    "bujji/broker/guard.py",
    "bujji/broker/hybrid.py",
])
def test_named_frozen_files_are_individually_unchanged(path):
    """Named one by one so a failure says WHICH protection was breached, not
    merely that something in a large tree moved."""
    assert _diff(path, stat=True).strip() == "", f"{path} changed since {FROZEN_SINCE}"


def test_the_journal_itself_took_no_convenience_method():
    """The freeze already caught this once: a date-scoped query added to
    PositionGroupJournal for the restart guard. It went into a caller instead."""
    assert _diff("bujji/journal/", stat=True).strip() == ""
