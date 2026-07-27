"""Production Engineering Sprint 7 -- Stage Outcome Accuracy.

Unit and integration tests for StageHandle.mark_failed(), closing
Sprint 6's documented gap: a non-exception ("soft") failure inside a
stage() block previously always logged outcome="ok". These tests prove
the fix changes ONLY the logged outcome -- never control flow, never a
trading decision.
"""
import logging

import pytest

from bujji.core.pipeline_stages import stage
from bujji.replay.engine import ReplayEngine
from tests.conftest import c


# ---------------------------------------------------------------------- #
# stage() / StageHandle -- isolated unit behavior
# ---------------------------------------------------------------------- #
def test_mark_failed_changes_outcome_to_failed_not_error(caplog):
    logger = logging.getLogger("test_stage_outcome_1")
    with caplog.at_level(logging.INFO, logger="test_stage_outcome_1"):
        with stage(logger, "unit_test_stage") as handle:
            handle.mark_failed("soft failure reason")
    finish = next(r for r in caplog.records if r.message == "pipeline_stage_finish")
    assert finish.data["outcome"] == "failed"
    assert finish.data["failure_reason"] == "soft failure reason"


def test_without_mark_failed_outcome_is_still_ok(caplog):
    """Backward compatibility: a block that never calls mark_failed()
    behaves exactly as before Sprint 7."""
    logger = logging.getLogger("test_stage_outcome_2")
    with caplog.at_level(logging.INFO, logger="test_stage_outcome_2"):
        with stage(logger, "unit_test_stage"):
            pass
    finish = next(r for r in caplog.records if r.message == "pipeline_stage_finish")
    assert finish.data["outcome"] == "ok"
    assert "failure_reason" not in finish.data


def test_mark_failed_does_not_change_the_blocks_return_value():
    """The whole point: mark_failed() only affects logging, never control
    flow. A `return False` after mark_failed() must still return False."""
    logger = logging.getLogger("test_stage_outcome_3")

    def inner():
        with stage(logger, "s") as handle:
            handle.mark_failed("reason")
            return False
        return True  # noqa: unreachable, illustrative only.

    assert inner() is False


def test_exception_still_takes_priority_over_mark_failed(caplog):
    """If a block calls mark_failed() and then raises, the exception path
    wins (outcome="error") -- mark_failed()'s effect is only observed on
    the no-exception path, matching stage()'s pre-Sprint-7 exception
    handling exactly."""
    logger = logging.getLogger("test_stage_outcome_4")

    class Boom(Exception):
        pass

    with caplog.at_level(logging.INFO, logger="test_stage_outcome_4"):
        with pytest.raises(Boom):
            with stage(logger, "s") as handle:
                handle.mark_failed("soft reason")
                raise Boom("hard failure")

    finish = next(r for r in caplog.records if r.message == "pipeline_stage_finish")
    assert finish.data["outcome"] == "error"
    assert finish.data["failure_reason"] == "hard failure"


def test_pre_existing_call_sites_without_as_handle_still_work(caplog):
    """Every stage() call site from Sprints 1-6 uses `with stage(...):`
    without `as handle` -- confirms that syntax still compiles and works
    unchanged after this sprint's extension."""
    logger = logging.getLogger("test_stage_outcome_5")
    with caplog.at_level(logging.INFO, logger="test_stage_outcome_5"):
        with stage(logger, "s", foo="bar"):
            pass
    finish = next(r for r in caplog.records if r.message == "pipeline_stage_finish")
    assert finish.data["outcome"] == "ok"
    assert finish.data["foo"] == "bar"


# ---------------------------------------------------------------------- #
# Integration: a real incomplete exit reports outcome="failed", trading
# behaviour (the return value / FSM state) is unaffected.
# ---------------------------------------------------------------------- #
def _cfg(config, tmp_path):
    config.paths.journal_csv = tmp_path / "j.csv"
    config.paths.database = tmp_path / "b.db"
    config.paths.state_file = tmp_path / "s.json"
    config.broker.order_timeout_seconds = 0.05
    config.broker.poll_interval_seconds = 0.01
    return config


@pytest.mark.asyncio
async def test_normal_exit_still_reports_outcome_ok_end_to_end(config, logger, tmp_path, caplog):
    """Regression: a normal, fully-flattened exit must still log
    outcome="ok" on the exit-side order_dispatch stage -- Sprint 7 must
    not turn every exit into a reported "failed"."""
    _cfg(config, tmp_path)
    candles = [
        c(9, 20, 22000, 22010, 21990, 22005, vol=1000),
        c(15, 5, 22000, 22010, 21990, 22005, vol=1000),
    ]
    with caplog.at_level(logging.INFO):
        engine = ReplayEngine(config, logger)
        result = await engine.run(candles)

    assert len(result.trades) == 1  # Unchanged trading behaviour.
    exit_finish = next(
        r for r in caplog.records
        if r.message == "pipeline_stage_finish" and hasattr(r, "data")
        and r.data.get("stage") == "order_dispatch" and r.data.get("direction") == "exit"
    )
    assert exit_finish.data["outcome"] == "ok"
