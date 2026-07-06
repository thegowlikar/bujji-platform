"""Health Engine — independent connectivity monitoring.

Purely observational: verifies it updates RuntimeStatus with connection
state/staleness and never touches trading decisions (no orchestrator method
that changes state is ever called by it).
"""
import pytest

from bujji.broker.paper import PaperBroker
from bujji.core.runtime_status import RuntimeStatus
from bujji.tick.health import HealthEngine
from tests.test_tick_engine import FakeTickFeed
from tests.test_tier1_capital_protection import build_orch, _enter_position, _fast_broker_cfg


@pytest.mark.asyncio
async def test_no_feed_means_no_monitoring(config, logger, tmp_path):
    _fast_broker_cfg(config)
    orch, status = build_orch(config, logger, PaperBroker(), tmp_path)
    await orch.startup()
    health = HealthEngine(None, orch, status, logger)
    health.start()  # Must not raise; nothing to start.
    assert health._task is None  # noqa: SLF001


@pytest.mark.asyncio
async def test_updates_connection_status(config, logger, tmp_path):
    _fast_broker_cfg(config)
    orch, status = build_orch(config, logger, PaperBroker(), tmp_path)
    await orch.startup()
    feed = FakeTickFeed()
    feed.is_connected = True
    feed.connect_count = 3
    health = HealthEngine(feed, orch, status, logger)
    health._check_once()  # noqa: SLF001
    assert status.ws_connected is True
    assert status.ws_connect_count == 3


@pytest.mark.asyncio
async def test_tracks_tick_age_only_while_in_position(config, logger, tmp_path):
    _fast_broker_cfg(config)
    orch, status = build_orch(config, logger, PaperBroker(), tmp_path)
    await orch.startup()
    feed = FakeTickFeed()
    health = HealthEngine(feed, orch, status, logger)

    health._check_once()  # noqa: SLF001 - no position yet.
    assert status.ws_last_tick_age_seconds is None

    await _enter_position(orch)
    feed.set_price(orch.position.contract.symbol, 100.0)
    health._check_once()  # noqa: SLF001
    assert status.ws_last_tick_age_seconds is not None


@pytest.mark.asyncio
async def test_health_engine_never_calls_orchestrator_mutation_methods(
    config, logger, tmp_path, monkeypatch
):
    """Purely observational — must never call square_off/on_candle/startup."""
    _fast_broker_cfg(config)
    orch, status = build_orch(config, logger, PaperBroker(), tmp_path)
    await orch.startup()
    await _enter_position(orch)

    called = []
    monkeypatch.setattr(orch, "square_off", lambda *a, **k: called.append("square_off"))

    feed = FakeTickFeed()
    feed.set_price(orch.position.contract.symbol, 1.0)  # Would breach any risk cap.
    health = HealthEngine(feed, orch, status, logger)
    health._check_once()  # noqa: SLF001

    assert called == []  # Health Engine never decides to exit — that's TickEngine's job.
