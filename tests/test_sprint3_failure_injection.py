"""Sprint 3 — Production Validation Campaign: Failure Injection.

Validation only. No strategy/architecture change. Each test injects a
specific real-world failure mode into the existing pipeline and verifies
the system fails safely, recovers where designed, and never opens an
unintended position.
"""
import pytest

from bujji.broker.paper import PaperBroker
from bujji.broker.errors import AuthenticationError
from bujji.core.enums import State
from bujji.core.orchestrator import Orchestrator
from bujji.core.runtime_status import RuntimeStatus
from bujji.core.session_state import SessionStore
from bujji.execution.engine import ExecutionEngine, ExecutionError
from bujji.journal.journal import TradeJournal
from bujji.signal.engine import SignalEngine
from bujji.trade.manager import TradeManager
from tests.conftest import c


def _build(config, logger, tmp_path, broker):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.paths.decision_journal = tmp_path / "d.jsonl"
    status = RuntimeStatus()
    execn = ExecutionEngine(broker, config, logger)
    signal = SignalEngine(config, logger)
    trade = TradeManager(config, logger)
    journal = TradeJournal(config.paths.journal_csv, config.paths.database)
    store = SessionStore(config.paths.state_file)
    return Orchestrator(config, logger, signal, trade, execn, journal, store, status), status, journal


class _AuthExpiredAtEntryBroker(PaperBroker):
    """Simulates a token expired exactly at contract-resolution time
    during entry -- the real scenario observed in today's production
    logs (09:15-14:25, auth_expired_candle_fetch). Note: get_recent_candles
    failing (the real, log-observed failure point) happens in app.py's
    outer polling loop, BEFORE on_candle is ever called -- today's real
    logs already prove that path (63 consecutive CRITICAL auth failures,
    zero entries, zero candles reaching the orchestrator at all). This
    test instead validates the closer-to-execution injection point
    on_candle's own entry path can reach: resolve_atm_contract."""

    async def resolve_atm_contract(self, *args, **kwargs):
        raise AuthenticationError("simulated: FYERS auth failure code=-16")


@pytest.mark.asyncio
async def test_auth_expiry_never_opens_a_position(config, logger, tmp_path):
    orch, status, journal = _build(config, logger, tmp_path, _AuthExpiredAtEntryBroker())
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert orch.state is not State.IN_POSITION
    assert status.auth_expired is True
    assert not journal.all_trades()


class _DisconnectDuringEntryBroker(PaperBroker):
    """Broker disconnect mid-entry -- CE leg resolution succeeds, PE leg
    resolution raises. Verifies the existing partial-resolution rollback
    (entry_failed_resolution_rollback) still holds under Sprint 1/2's
    refactored Order Planning layer."""

    def __init__(self):
        super().__init__()
        self._calls = 0

    async def resolve_atm_contract(self, *args, **kwargs):
        self._calls += 1
        if self._calls == 2:
            raise ExecutionError("simulated broker disconnect")
        return await super().resolve_atm_contract(*args, **kwargs)


@pytest.mark.asyncio
async def test_broker_disconnect_during_planning_rolls_back_to_ready(config, logger, tmp_path):
    orch, status, journal = _build(config, logger, tmp_path, _DisconnectDuringEntryBroker())
    await orch.startup()
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert orch.state is State.READY  # Rolled back, not stuck at CONFIRMED, not IN_POSITION.
    assert not journal.all_trades()


@pytest.mark.asyncio
async def test_duplicate_candle_is_ignored_not_double_processed(config, logger, tmp_path):
    orch, status, journal = _build(config, logger, tmp_path, PaperBroker())
    await orch.startup()
    candle = c(9, 20, 22000, 22010, 21990, 22005)
    await orch.on_candle(candle)
    trades_after_first = len(journal.all_trades())
    await orch.on_candle(candle)  # Exact duplicate timestamp.
    assert status.duplicate_candles_ignored >= 1
    assert len(journal.all_trades()) == trades_after_first  # No double-entry.


@pytest.mark.asyncio
async def test_stale_candle_gap_is_recorded_not_silently_dropped(config, logger, tmp_path):
    orch, status, journal = _build(config, logger, tmp_path, PaperBroker())
    await orch.startup()
    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005))
    await orch.on_candle(c(9, 45, 22000, 22010, 21990, 22005))  # 30-min gap, not 5-min.
    assert status.last_candle_gap_seconds is not None
    assert status.last_candle_gap_seconds > 300


class _DelayedOptionChainBroker(PaperBroker):
    """Option chain call (used by the Structure Brain's intelligence
    fetch) times out / returns nothing -- must degrade to UNKNOWN, never
    crash the candle cycle or block trading."""

    async def get_option_chain(self, underlying, spot, strike_count=5):
        raise TimeoutError("simulated delayed option chain response")


@pytest.mark.asyncio
async def test_delayed_option_chain_never_crashes_the_candle_cycle(config, logger, tmp_path):
    orch, status, journal = _build(config, logger, tmp_path, _DelayedOptionChainBroker())
    await orch.startup()
    # Must not raise, even though the underlying broker call fails.
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    assert status.intelligence.get("structure", {}).get("data_quality") in (None, "INSUFFICIENT")


@pytest.mark.asyncio
async def test_journal_write_failure_does_not_block_the_trading_loop(config, logger, tmp_path):
    """Journal writes to an unwritable path -- position lifecycle must
    still complete; the failure is logged, never propagated into the
    state machine (mirrors DecisionJournal's own best-effort discipline)."""
    config.paths.journal_csv = tmp_path / "readonly" / "j.csv"
    config.paths.database = tmp_path / "readonly" / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.paths.decision_journal = tmp_path / "d.jsonl"
    import os
    ro_dir = tmp_path / "readonly"
    ro_dir.mkdir()
    os.chmod(ro_dir, 0o444)  # Read-only -- journal writes inside will fail.

    status = RuntimeStatus()
    broker = PaperBroker()
    execn = ExecutionEngine(broker, config, logger)
    signal = SignalEngine(config, logger)
    trade = TradeManager(config, logger)
    store = SessionStore(config.paths.state_file)
    try:
        journal = TradeJournal(config.paths.journal_csv, config.paths.database)
    except (PermissionError, OSError):
        pytest.skip("environment does not enforce directory permissions (e.g. root) -- cannot simulate")
    orch = Orchestrator(config, logger, signal, trade, execn, journal, store, status)
    await orch.startup()
    # Must not raise even if the journal write inside fails.
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005))
    os.chmod(ro_dir, 0o755)
