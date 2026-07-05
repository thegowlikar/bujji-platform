"""VWAP Audit subsystem tests.

Verify that a quality record is emitted every cycle, carries the required
fields, reflects real vs fallback correctly, and — critically — does not alter
any trading decision.
"""
import pytest

from bujji.signal.indicators import VwapTracker
from bujji.signal.vwap_audit import VwapAuditRecord, VwapQuality
from bujji.core.enums import State
from tests.conftest import c


def test_quality_snapshot_real_volume():
    vt = VwapTracker(allow_equal_weight_fallback=False)
    vt.update(c(9, 15, 100, 110, 90, 105, vol=1000))
    q = VwapQuality.from_tracker(vt)
    assert q.is_real and not q.using_fallback and q.trading_permitted
    assert q.candles_used == 1 and q.cumulative_volume == 1000
    assert q.fallback_reason is None


def test_quality_snapshot_no_volume_disabled():
    vt = VwapTracker(allow_equal_weight_fallback=False)
    vt.update(c(9, 15, 100, 110, 90, 105, vol=0))
    q = VwapQuality.from_tracker(vt)
    assert not q.is_real and not q.using_fallback
    assert not q.trading_permitted  # Trading disabled without real volume.
    assert q.fallback_reason == "no_real_volume__trading_disabled_fallback_off"


def test_quality_snapshot_fallback_enabled():
    vt = VwapTracker(allow_equal_weight_fallback=True)
    vt.update(c(9, 15, 100, 120, 90, 105, vol=0))
    q = VwapQuality.from_tracker(vt)
    assert q.using_fallback and q.trading_permitted and not q.is_real
    assert q.fallback_reason == "no_real_volume__equal_weight_fallback_enabled"


def test_audit_record_log_has_all_required_fields():
    vt = VwapTracker()
    vt.update(c(9, 15, 100, 110, 90, 105, vol=1000))
    rec = VwapAuditRecord(
        timestamp=c(9, 15, 0, 0, 0, 0).timestamp,
        strategy_state="IN_POSITION", trade_state="IN_POSITION",
        decision="HOLD:thesis_intact", quality=VwapQuality.from_tracker(vt),
    )
    log = rec.to_log()
    for key in ("timestamp", "strategy_state", "trade_state", "decision",
                "vwap_value", "candles_used", "cumulative_volume",
                "vwap_is_real", "vwap_using_fallback", "vwap_fallback_reason",
                "trading_permitted"):
        assert key in log


@pytest.mark.asyncio
async def test_audit_emitted_every_cycle_and_noninvasive(config, logger, tmp_path):
    """Audit populates status each cycle without changing the decision path."""
    from bujji.broker.paper import PaperBroker
    from bujji.core.orchestrator import Orchestrator
    from bujji.core.runtime_status import RuntimeStatus
    from bujji.core.session_state import SessionStore
    from bujji.execution.engine import ExecutionEngine
    from bujji.journal.journal import TradeJournal
    from bujji.signal.engine import SignalEngine
    from bujji.trade.manager import TradeManager

    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"

    status = RuntimeStatus()
    execn = ExecutionEngine(PaperBroker(), config, logger)
    orch = Orchestrator(
        config, logger, SignalEngine(config, logger), TradeManager(config, logger),
        execn, TradeJournal(config.paths.journal_csv, config.paths.database),
        SessionStore(config.paths.state_file), status,
    )
    await orch.startup()

    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    assert status.market_data_health is not None
    assert len(status.vwap_audit_history) == 1
    assert status.market_data_health["quality"]["is_real"] is True

    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))
    # Trading proceeded normally (entry) AND an audit was recorded.
    assert orch.state is State.IN_POSITION
    assert len(status.vwap_audit_history) == 2
    assert status.vwap_audit_history[-1]["decision"].startswith("ENTER")
