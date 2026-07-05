"""E1/E2 — broker auth/session failures are detected distinctly from generic
transient errors, never blindly retried, and the operator is alerted with a
specific, actionable status rather than a generic "something failed."

Covers:
  - FYERS response classifier (`_raise_if_auth_error`) in isolation.
  - ExecutionEngine: AuthenticationError is never retried (no backoff burned),
    unlike a generic error which IS retried per the configured attempts.
  - Orchestrator: entry / in-position / exit / square-off / recovery paths all
    handle AuthenticationError distinctly — the position is never lost, no
    duplicate action is taken, and `status.auth_expired` is set and clears
    once the broker starts succeeding again.
  - Replay: a token "expiring" mid-replay doesn't crash the replay engine and
    is flagged distinctly, exercising the exact live decision path.
"""
import pytest

from bujji.broker.errors import AuthenticationError
from bujji.broker.fyers import FyersBroker
from bujji.broker.paper import PaperBroker
from bujji.core.enums import Side, State
from bujji.core.models import OptionContract
from bujji.core.enums import OptionType
from bujji.core.models import OrderRequest
from bujji.execution.engine import ExecutionEngine, ExecutionError
from tests.conftest import c
from tests.test_tier1_capital_protection import build_orch, _enter_position, _fast_broker_cfg


# ---------------------------------------------------------------------- #
# FYERS response classifier (pure, no transport needed)
# ---------------------------------------------------------------------- #
def _fyers(config, logger):
    return FyersBroker(config.broker, logger)


def test_classifier_http_401_403(config, logger):
    b = _fyers(config, logger)
    with pytest.raises(AuthenticationError):
        b._raise_if_auth_error({}, http_status=401)  # noqa: SLF001
    with pytest.raises(AuthenticationError):
        b._raise_if_auth_error({}, http_status=403)  # noqa: SLF001


def test_classifier_known_fyers_code(config, logger):
    b = _fyers(config, logger)
    with pytest.raises(AuthenticationError):
        b._raise_if_auth_error({"code": -8, "message": "bad token"})  # noqa: SLF001


def test_classifier_keyword_fallback(config, logger):
    b = _fyers(config, logger)
    with pytest.raises(AuthenticationError):
        b._raise_if_auth_error(  # noqa: SLF001
            {"s": "error", "message": "Your session has expired, please login again"}
        )


def test_classifier_does_not_misfire_on_unrelated_error(config, logger):
    b = _fyers(config, logger)
    b._raise_if_auth_error(  # noqa: SLF001
        {"s": "error", "code": -99, "message": "insufficient margin"}
    )  # Must not raise.


def test_classifier_passes_through_success(config, logger):
    b = _fyers(config, logger)
    b._raise_if_auth_error({"s": "ok", "ltp": 100.5})  # noqa: SLF001 - no raise.


# ---------------------------------------------------------------------- #
# ExecutionEngine: no retry / no backoff burned on auth failure
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_execution_engine_does_not_retry_auth_error(config, logger):
    _fast_broker_cfg(config)
    config.broker.retry_attempts = 5
    broker = PaperBroker()
    broker.simulate_auth_expiry()
    engine = ExecutionEngine(broker, config, logger)

    with pytest.raises(AuthenticationError):
        await engine.get_spot("NIFTY")
    assert broker.auth_error_calls == 1  # Exactly one attempt, no retries.


@pytest.mark.asyncio
async def test_execution_engine_still_retries_generic_errors(config, logger):
    """Regression: a plain (non-auth) error must still use the full retry
    budget, proving the auth short-circuit didn't break normal resilience."""
    _fast_broker_cfg(config)
    config.broker.retry_attempts = 3

    calls = {"n": 0}

    class FlakyBroker(PaperBroker):
        async def get_spot(self, underlying: str) -> float:
            calls["n"] += 1
            raise RuntimeError("transient network blip")

    engine = ExecutionEngine(FlakyBroker(), config, logger)
    with pytest.raises(ExecutionError):
        await engine.get_spot("NIFTY")
    assert calls["n"] == 3  # All 3 attempts used, unlike the auth case.


@pytest.mark.asyncio
async def test_place_order_auth_error_not_retried(config, logger):
    _fast_broker_cfg(config)
    config.broker.retry_attempts = 5
    broker = PaperBroker()
    broker.simulate_auth_expiry()
    engine = ExecutionEngine(broker, config, logger)
    contract = OptionContract("NIFTY22000PE", "NIFTY", 22000, OptionType.PE,
                              "WEEKLY", 75)
    req = OrderRequest(contract, Side.SELL, 75, "CID-AUTH")
    with pytest.raises(AuthenticationError):
        await engine.submit_and_confirm(req)
    assert broker.auth_error_calls == 1


# ---------------------------------------------------------------------- #
# Orchestrator: entry path
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_entry_blocked_by_auth_error_rolls_back_safely(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()

    broker.simulate_auth_expiry()
    await orch.on_candle(c(9, 15, 22000, 22010, 21990, 22005, vol=1000))
    await orch.on_candle(c(9, 20, 22006, 22080, 22005, 22079, vol=1000))

    assert orch.state is State.READY          # Rolled back, not stuck/crashed.
    assert not orch.has_open_position()        # No position was created.
    assert status.auth_expired is True
    assert status.healthy is False
    assert "auth_expired" in status.health_detail


@pytest.mark.asyncio
async def test_auth_flag_self_clears_once_broker_recovers(config, logger, tmp_path):
    """Self-clear is exercised via the in-position get_ltp path, not entry
    retry: SignalEngine._signalled is set at signal-generation time regardless
    of whether the resulting order succeeds, so a failed entry permanently
    forfeits that day's one trade (verified, documented behavior — see
    CHAOS_TESTING_PLAN.md H1) rather than being retried on a later candle."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    assert orch.has_open_position()

    broker.simulate_auth_expiry()
    await orch.on_candle(c(9, 25, 22079, 22150, 22078, 22149, vol=1000))
    assert status.auth_expired is True

    # Operator refreshes the token; broker starts succeeding again.
    broker.simulate_auth_expiry(False)
    await orch.on_candle(c(9, 30, 22149, 22200, 22148, 22199, vol=1000))
    assert status.auth_expired is False
    assert status.healthy is True
    assert orch.has_open_position()  # Still holding; only the auth flag cleared.


# ---------------------------------------------------------------------- #
# Orchestrator: in-position path — position must never be lost
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_in_position_auth_error_preserves_position(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)
    assert orch.has_open_position()

    broker.simulate_auth_expiry()
    await orch.on_candle(c(9, 25, 22079, 22150, 22078, 22149, vol=1000))

    assert orch.state is State.IN_POSITION     # Untouched, not abandoned.
    assert orch.has_open_position()
    assert status.auth_expired is True
    assert status.healthy is False


@pytest.mark.asyncio
async def test_exit_auth_error_keeps_position_and_retries(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    broker.simulate_auth_expiry()
    # A candle that would trigger an exit (loses VWAP).
    await orch.on_candle(c(9, 25, 22078, 22079, 21950, 21951, vol=1000))

    assert orch.state is State.IN_POSITION     # Exit failed -> stays open.
    assert orch.has_open_position()
    assert status.auth_expired is True
    assert not await_positions_would_be_empty(broker)  # Broker still holds it.


def await_positions_would_be_empty(broker: PaperBroker) -> bool:
    return len(broker._positions) == 0  # noqa: SLF001 - test introspection.


# ---------------------------------------------------------------------- #
# Orchestrator: square-off / end-of-day best-effort under auth failure
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_square_off_auth_error_on_price_lookup_still_attempts_exit(
    config, logger, tmp_path
):
    """get_ltp/get_spot failing with auth error falls back to stale prices for
    the square-off attempt; the exit order itself will also fail (same broker
    state), so the position is correctly preserved, not falsely closed."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    broker.simulate_auth_expiry()
    ok = await orch.square_off("test_forced")
    assert ok is False                          # Could not actually flatten.
    assert orch.has_open_position()             # Preserved, not abandoned.
    assert status.auth_expired is True


@pytest.mark.asyncio
async def test_square_off_succeeds_after_auth_recovers(config, logger, tmp_path):
    _fast_broker_cfg(config)
    broker = PaperBroker()
    orch, status = build_orch(config, logger, broker, tmp_path)
    await orch.startup()
    await _enter_position(orch)

    broker.simulate_auth_expiry()
    assert await orch.square_off("test_forced") is False

    broker.simulate_auth_expiry(False)  # Token refreshed.
    assert await orch.square_off("test_forced_retry") is True
    assert not orch.has_open_position()
    assert status.auth_expired is False


# ---------------------------------------------------------------------- #
# Recovery: auth failure during startup reconciliation
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_startup_auth_failure_propagates_distinctly(config, logger, tmp_path):
    """An invalid token from the very start must fail fast and unambiguously
    (fail-closed) rather than silently proceeding as if flat/clean."""
    _fast_broker_cfg(config)
    broker = PaperBroker()
    broker.simulate_auth_expiry()
    orch, status = build_orch(config, logger, broker, tmp_path)
    with pytest.raises(AuthenticationError):
        await orch.startup()


# ---------------------------------------------------------------------- #
# Replay: token expiring mid-replay through the identical live decision path
# ---------------------------------------------------------------------- #
@pytest.mark.asyncio
async def test_replay_survives_mid_replay_auth_expiry(config, logger, tmp_path):
    from bujji.replay.engine import ReplayEngine

    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    _fast_broker_cfg(config)

    engine = ReplayEngine(config, logger)
    await engine.orchestrator.startup()

    broker = engine._broker  # noqa: SLF001 - test introspection.
    candles = [
        c(9, 15, 22000, 22010, 21990, 22005, vol=1000),
        c(9, 20, 22006, 22080, 22005, 22079, vol=1000),
    ]
    for candle in candles:
        broker.set_market(candle.close)
        await engine.orchestrator.on_candle(candle)
    assert engine.orchestrator.has_open_position()

    # Token "expires" mid-replay.
    broker.simulate_auth_expiry()
    next_candle = c(9, 25, 22079, 22150, 22078, 22149, vol=1000)
    broker.set_market(next_candle.close)
    await engine.orchestrator.on_candle(next_candle)  # Must not raise/crash.

    assert engine.orchestrator.has_open_position()  # Preserved through the fault.
    assert engine.status.auth_expired is True
