"""VWAP Audit subsystem tests.

Two things certified here:
  1. VwapQuality (the spot-index, volume-weighted quality snapshot) still
     works correctly in isolation — it's dead code for the straddle
     strategy's actual decisions, but kept for backward compatibility and
     is tested here on its own merits.
  2. VwapAuditRecord / the live dashboard payload now reports
     PremiumVwapQuality — this strategy's ACTUAL live indicator — not the
     unused spot-VWAP quality, and it never alters any trading decision.
"""
import pytest

from bujji.signal.indicators import PremiumVwapTracker, VwapTracker
from bujji.signal.vwap_audit import PremiumVwapQuality, VwapAuditRecord, VwapQuality
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


def test_premium_vwap_quality_not_ready_before_entry():
    tracker = PremiumVwapTracker()
    q = PremiumVwapQuality.from_tracker(tracker)
    assert q.ready is False
    assert q.candles_used == 0
    assert q.value == 0.0


def test_premium_vwap_quality_ready_after_entry_seed():
    tracker = PremiumVwapTracker()
    tracker.update(240.0)  # Entry seed.
    q = PremiumVwapQuality.from_tracker(tracker)
    assert q.ready is True
    assert q.candles_used == 1
    assert q.value == 240.0

    tracker.update(230.0)
    q2 = PremiumVwapQuality.from_tracker(tracker)
    assert q2.candles_used == 2
    assert q2.value == 235.0  # (240 + 230) / 2, equal-weight.


def test_audit_record_log_has_all_required_fields():
    tracker = PremiumVwapTracker()
    tracker.update(240.0)
    rec = VwapAuditRecord(
        timestamp=c(9, 15, 0, 0, 0, 0).timestamp,
        strategy_state="IN_POSITION", trade_state="IN_POSITION",
        decision="HOLD:premium_below_vwap",
        quality=PremiumVwapQuality.from_tracker(tracker),
    )
    log = rec.to_log()
    for key in ("timestamp", "strategy_state", "trade_state", "decision",
                "vwap_value", "candles_used", "ready"):
        assert key in log


@pytest.mark.asyncio
async def test_audit_emitted_every_cycle_and_noninvasive(config, logger, tmp_path):
    """Audit populates status each cycle without changing the decision path,
    and reports the ACTUAL premium VWAP the strategy uses — not a dummy."""
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

    # 09:15 candle: audit emitted but no entry yet (before trading_start) —
    # the premium VWAP is correctly reported as not-ready (no position exists).
    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    assert status.market_data_health is not None
    assert len(status.vwap_audit_history) == 1
    assert status.market_data_health["quality"]["ready"] is False

    # 09:20 candle: straddle entered, audit now reports the seeded, ready
    # premium VWAP — no more misleading "always unreliable" banner.
    await orch.on_candle(c(9, 20, 22000, 22010, 21990, 22005, vol=1000))
    assert orch.state is State.IN_POSITION
    assert len(status.vwap_audit_history) == 2
    assert status.vwap_audit_history[-1]["decision"].startswith("ENTER")
    assert status.market_data_health["quality"]["ready"] is True
    assert status.market_data_health["quality"]["value"] > 0
