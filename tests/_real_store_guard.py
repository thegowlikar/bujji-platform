"""Is the REAL production observation store present, with real data in it?

WHY THIS EXISTS. Two test modules gated themselves on
`os.path.exists("data/historical_reality/normalized/historical_observations.db")`
and then asserted on its CONTENT. Existence is not availability:
`HistoricalObservationStore.__init__` does

    directory.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(self._path)
    ... executescript(_SCHEMA)

so merely CONSTRUCTING the store at that path creates a valid, schema'd,
completely EMPTY database. Any test that does so leaves the file behind.

The result was a suite whose answer depended on whether it had been run
before. Run 1 in a clean checkout: the file is absent, the tests SKIP, and
something creates it. Run 2 on the identical tree: the file now exists, the
tests un-skip, query an empty store, and FAIL. Same commit, same code, two
different results -- and "the tests are green" was being used as a deployment
gate.

THE RULE: an empty store is not the production store. This checks for real
rows, so a sibling test's leftover artifact can never satisfy it.

AND THIS MODULE MUST NEVER BE THE POLLUTER. It checks existence BEFORE opening
anything, and opens read-only through sqlite3 directly rather than through
HistoricalObservationStore, so calling it on an absent path creates nothing.
`test_real_store_guard_is_order_independent.py` proves that.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_STORE = (REPO_ROOT / "data" / "historical_reality" / "normalized"
                    / "historical_observations.db")


def production_store_path() -> Path:
    return PRODUCTION_STORE


# The real table name, read from bujji/historical_reality/store.py's _SCHEMA.
# An earlier version of this guard queried "observations" -- which does not
# exist -- so it returned 0 for EVERY store including a fully populated one,
# and would have skipped the tests it gates forever while looking correct. The
# positive control in test_real_store_guard_is_order_independent.py caught it.
_OBSERVATIONS_TABLE = "historical_observations"


def _row_count(path: Path) -> int:
    """Rows in the observations table, or 0 for anything unusable.

    Read-only and creation-free: `mode=ro` makes sqlite refuse to create the
    file rather than silently making an empty one.
    """
    try:
        uri = f"file:{path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=5.0)
    except sqlite3.Error:
        return 0
    try:
        cur = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
            (_OBSERVATIONS_TABLE,))
        if cur.fetchone() is None:
            return 0
        return int(conn.execute(
            f"SELECT COUNT(*) FROM {_OBSERVATIONS_TABLE}").fetchone()[0])
    except sqlite3.Error:
        return 0
    finally:
        conn.close()


def production_store_has_data(path: Path | None = None) -> bool:
    """True only if the store exists AND holds at least one observation."""
    target = Path(path) if path is not None else PRODUCTION_STORE
    if not target.exists():
        return False
    return _row_count(target) > 0


def skip_reason(path: Path | None = None) -> str:
    """Why the guard said no -- distinguishes absent from empty, so a skipped
    test says something an operator can act on."""
    target = Path(path) if path is not None else PRODUCTION_STORE
    if not target.exists():
        return (f"real production HistoricalObservationStore not present "
                f"({target}); this environment has no captured history")
    return (f"the store at {target} exists but holds no observations -- it is "
            f"an empty file left behind by another test constructing "
            f"HistoricalObservationStore at this path, not the production "
            f"store. Skipping rather than asserting against nothing.")
