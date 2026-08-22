"""The suite's answer must not depend on whether it has been run before.

THE DEFECT. Two modules gated themselves on
`os.path.exists(".../historical_observations.db")` and then asserted on its
CONTENT. But `HistoricalObservationStore.__init__` does:

    directory.mkdir(parents=True, exist_ok=True)
    sqlite3.connect(self._path)
    conn.executescript(_SCHEMA)

so merely CONSTRUCTING the store at a path creates a valid, schema'd,
completely EMPTY database and leaves it there.

Observed on this repo, twice, on an unmodified tree:

    run 1 (clean data/)   120 failed, 8078 passed, 20 skipped
    run 2 (same commit)   123 failed, 8078 passed, 17 skipped

Three tests moved from SKIPPED to FAILED because a sibling had manufactured
the file they gate on. I confirmed the failures were not mine by stashing every
campaign change and reproducing them on pristine 519199a.

That matters beyond tidiness: "the suite is green" is used as a deployment
gate for a system that trades. A gate whose answer depends on run order is not
a gate.

THE INVARIANT: an empty store is not the production store. The guard requires
real rows, and -- equally important -- never constructs the store itself, so
it can never become the polluter it exists to detect.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bujji.historical_reality.store import HistoricalObservationStore  # noqa: E402
from bujji.historical_reality.capture import build_historical_observation  # noqa: E402
from bujji.market_observation import taxonomy as moc_taxonomy  # noqa: E402
from tests._real_store_guard import (  # noqa: E402
    production_store_has_data, skip_reason,
)


def _store_path(tmp_path: Path) -> Path:
    return tmp_path / "data" / "historical_reality" / "normalized" / "historical_observations.db"


class TestAnEmptyStoreIsNotTheProductionStore:
    def test_an_absent_store_is_unavailable(self, tmp_path):
        assert production_store_has_data(_store_path(tmp_path)) is False

    def test_a_store_created_but_never_written_is_unavailable(self, tmp_path):
        """THE regression. This is exactly what a sibling test leaves behind:
        constructing the store is enough to create the file."""
        path = _store_path(tmp_path)
        store = HistoricalObservationStore(str(path))
        store.close()

        assert path.exists(), (
            "premise broken: constructing the store no longer creates the "
            "file, so this test is no longer testing the real hazard")
        assert production_store_has_data(path) is False, (
            "an empty leftover satisfied the availability check -- the "
            "run-order dependence is back")

    def test_a_store_with_real_data_IS_available(self, tmp_path):
        """The guard must not become a wall that skips everything forever."""
        path = _store_path(tmp_path)
        store = HistoricalObservationStore(str(path))
        store.write(build_historical_observation(
            instrument_identity="NSE:NIFTY50-INDEX", instrument_type="SPOT",
            resolution=moc_taxonomy.RESOLUTION_DAILY,
            timestamp="2020-03-23T09:15:00+05:30",
            payload={"open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5, "volume": None},
            source="fyers_historical",
            access_method="direct_sdk_fyers_historical_rest",
            source_epoch=1584936900, source_symbol="NSE:NIFTY50-INDEX",
            raw_artifact_ref="x", ingestion_run_id="RUN-x",
            retrieved_at="2020-03-23T09:15:00+05:30",
            certification_status="CERTIFIED_AVAILABLE", certification_ref="ref",
        ))
        store.close()
        assert production_store_has_data(path) is True


class TestTheGuardIsNotThePolluter:
    def test_checking_an_absent_store_creates_nothing(self, tmp_path):
        """If the guard opened the store to inspect it, the FIRST call would
        manufacture the file and the SECOND would report it available -- the
        detector would have become the defect."""
        path = _store_path(tmp_path)

        assert production_store_has_data(path) is False
        assert not path.exists(), "the guard CREATED the store it was asked about"
        assert not path.parent.exists(), "the guard created the directory tree"

        assert production_store_has_data(path) is False
        assert not path.exists()

    def test_the_guard_is_idempotent(self, tmp_path):
        path = _store_path(tmp_path)
        results = [production_store_has_data(path) for _ in range(5)]
        assert results == [False] * 5, (
            f"repeated checks changed their own answer: {results}")


class TestTheSkipReasonIsActionable:
    def test_absent_and_empty_are_distinguished(self, tmp_path):
        path = _store_path(tmp_path)
        assert "not present" in skip_reason(path)

        HistoricalObservationStore(str(path)).close()
        reason = skip_reason(path)
        assert "holds no observations" in reason
        assert "left behind by another test" in reason, (
            "a skip that does not name its cause sends the reader hunting")
