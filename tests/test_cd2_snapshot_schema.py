"""C_D2 — corrupted/schema-mismatched session snapshot must never crash
recovery, and a real open position must still be found and flattened via
broker reconciliation regardless of what the local snapshot says.
"""
import json

import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.enums import Side, State
from bujji.core.orchestrator import Orchestrator
from bujji.core.position_codec import (
    PositionSchemaError,
    position_from_dict,
    position_to_dict,
)
from bujji.core.runtime_status import RuntimeStatus
from bujji.core.session_state import SessionStore
from bujji.execution.engine import ExecutionEngine
from bujji.journal.journal import TradeJournal
from bujji.signal.engine import SignalEngine
from bujji.trade.manager import TradeManager
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch, _enter_position


# ---------------------------------------------------------------------- #
# position_codec: schema-mismatch handling in isolation
# ---------------------------------------------------------------------- #
def test_missing_top_level_key_raises_schema_error():
    with pytest.raises(PositionSchemaError):
        position_from_dict({"contract": {}, "direction": "BULLISH"})  # No orb, etc.


def test_missing_nested_contract_key_raises_schema_error():
    good = _valid_position_dict()
    del good["contract"]["symbol"]
    with pytest.raises(PositionSchemaError):
        position_from_dict(good)


def test_wrong_type_for_contract_raises_schema_error():
    good = _valid_position_dict()
    good["contract"] = "not-a-dict"  # e.g. a future format change.
    with pytest.raises(PositionSchemaError):
        position_from_dict(good)


def test_invalid_enum_value_raises_schema_error():
    good = _valid_position_dict()
    good["direction"] = "SIDEWAYS"  # Not a real Direction value.
    with pytest.raises(PositionSchemaError):
        position_from_dict(good)


def test_garbage_quantity_raises_schema_error():
    good = _valid_position_dict()
    good["quantity"] = "not-a-number"
    with pytest.raises(PositionSchemaError):
        position_from_dict(good)


def test_numeric_as_string_is_tolerated():
    """A schema mismatch is a hard failure; a numeric-as-string value (a common
    JSON round-trip artifact) is NOT — it should coerce, not raise."""
    good = _valid_position_dict()
    good["quantity"] = "75"
    good["entry_price"] = "120.5"
    pos = position_from_dict(good)
    assert pos.quantity == 75
    assert pos.entry_price == 120.5


def test_valid_dict_still_roundtrips():
    good = _valid_position_dict()
    pos = position_from_dict(good)
    assert pos.contract.symbol == "NIFTY22000PE"
    assert position_to_dict(pos)["contract"]["symbol"] == "NIFTY22000PE"


def _valid_position_dict() -> dict:
    return {
        "contract": {
            "symbol": "NIFTY22000PE", "underlying": "NIFTY", "strike": 22000,
            "option_type": "PE", "expiry": "WEEKLY", "lot_size": 75,
        },
        "direction": "BULLISH",
        "entry_side": "SELL",
        "quantity": 75,
        "entry_price": 120.0,
        "entry_spot": 22050.0,
        "entry_time": "2026-07-05T09:20:00",
        "orb": {
            "high": 110.0, "low": 90.0,
            "start": "2026-07-05T09:15:00", "end": "2026-07-05T09:20:00",
        },
        "thesis": None,
    }


# ---------------------------------------------------------------------- #
# SessionStore.load(): invalid JSON is already handled (regression guard)
# ---------------------------------------------------------------------- #
def test_session_store_load_survives_truncated_json(tmp_path):
    path = tmp_path / "session_state.json"
    path.write_text("{not valid json")
    snap = SessionStore(path).load()
    assert snap.position is None
    assert snap.state == "WAITING"


def test_session_store_load_survives_schema_mismatched_but_valid_json(tmp_path):
    """Valid JSON, but the top-level shape doesn't match SessionSnapshot at
    all (e.g. a list, or unexpected extra required-looking keys)."""
    path = tmp_path / "session_state.json"
    path.write_text(json.dumps(["not", "a", "dict"]))
    snap = SessionStore(path).load()
    assert snap.position is None  # Falls back to a clean snapshot, not a crash.


# ---------------------------------------------------------------------- #
# End-to-end: corrupted position field inside an otherwise-valid snapshot
# must not crash Orchestrator.startup(), and must still discover + flatten
# the real broker position (defense-in-depth via reconciliation).
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_recovery_survives_corrupt_position_and_flattens_real_position(
    config, logger, tmp_path
):
    broker = PaperBroker()
    a, _ = build_orch(config, logger, broker, tmp_path)
    await a.startup()
    await _enter_position(a)
    assert a.state is State.IN_POSITION

    # Corrupt the ON-DISK snapshot's position payload directly (simulates a
    # hand-edit, partial write survived by JSON parsing, or a future field
    # rename) while the broker still genuinely holds the short.
    state_path = config.paths.state_file
    raw = json.loads(state_path.read_text())
    assert raw["position"] is not None
    raw["position"]["quantity"] = "not-a-number"  # Schema-breaking edit.
    state_path.write_text(json.dumps(raw))

    # A fresh orchestrator (simulated restart) must not crash, and must still
    # find and flatten the real broker position via reconciliation.
    b, status = build_orch(config, logger, broker, tmp_path)
    await b.startup()  # Must not raise.

    assert b.state is State.DONE_FOR_DAY
    assert not b.has_open_position()
    assert not await broker.get_open_positions()  # Truly flattened.
    assert status.healthy is False  # Operator is alerted to the corruption.
    assert "corrupt" in (status.health_detail or "")


@pytest.mark.asyncio
async def test_recovery_survives_corrupt_position_when_already_flat(
    config, logger, tmp_path
):
    """Corrupt snapshot + broker already flat -> clean recovery, no crash,
    no false resume, no false orphan-flatten action needed."""
    broker = PaperBroker()
    a, _ = build_orch(config, logger, broker, tmp_path)
    await a.startup()
    await _enter_position(a)

    state_path = config.paths.state_file
    raw = json.loads(state_path.read_text())
    raw["position"]["contract"] = "not-a-dict"
    state_path.write_text(json.dumps(raw))

    # Broker position already closed by the time of "restart".
    broker._positions.clear()  # noqa: SLF001

    b, status = build_orch(config, logger, broker, tmp_path)
    await b.startup()  # Must not raise.
    assert b.state is State.DONE_FOR_DAY
    assert not b.has_open_position()


# ---------------------------------------------------------------------- #
# Replay path: the safety fix is not bypassed by the replay tooling — the
# Replay Engine builds the identical Orchestrator/SessionStore stack.
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_replay_startup_survives_corrupt_snapshot(config, logger, tmp_path):
    from bujji.replay.engine import ReplayEngine

    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"

    # First replay run: ORB + breakout, leaving a position open in its journal
    # bookkeeping via the same on-disk snapshot path.
    engine1 = ReplayEngine(config, logger)
    await engine1.run([
        c(9, 15, 22000, 22010, 21990, 22005, vol=1000),
        c(9, 20, 22006, 22080, 22005, 22079, vol=1000),
    ])
    assert engine1.orchestrator.has_open_position()

    # Corrupt the snapshot the same way an operator/disk fault might.
    raw = json.loads(config.paths.state_file.read_text())
    raw["position"]["orb"] = "not-a-dict"
    config.paths.state_file.write_text(json.dumps(raw))

    # A fresh ReplayEngine (its own fresh, flat ReplayBroker) must still start
    # cleanly — corruption must not propagate as a crash through replay tooling.
    engine2 = ReplayEngine(config, logger)
    await engine2.orchestrator.startup()  # Must not raise.
    assert engine2.orchestrator.state is State.DONE_FOR_DAY
    assert not engine2.orchestrator.has_open_position()
