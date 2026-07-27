"""BUJJI Options OS — Decision Pipeline stage instrumentation.

Production Engineering Sprint 1 (Decision Pipeline Refactor, Zero
Behavioural Change): this module adds ONLY observability around the
seven documented pipeline stages --

    1. Market Observation
    2. Market Intelligence
    3. Strategy Evaluation
    4. Risk Validation
    5. Execution Decision
    6. Order Dispatch
    7. Journal Recording

It contains NO trading logic, NO decision logic, and NO control-flow
change. `stage()` is a context manager that logs start/finish/duration/
outcome/failure_reason around a block of EXISTING code, left in its
original place and order in orchestrator.py -- it never runs code, never
short-circuits a candle, and never suppresses an exception (any raised
exception is logged with outcome="error" and then re-raised unchanged,
so every existing try/except in orchestrator.py sees exactly the same
exception it always did).

Production Engineering Sprint 7 (Stage Outcome Accuracy): `stage()` now
yields a StageHandle so a wrapped block can report a non-exception
("soft") failure via handle.mark_failed(reason) -- e.g. _exit_leg()
returning False rather than raising. This affects ONLY what gets
logged, never what happens: no trading decision, order, risk action, or
journal entry is touched by this change, only the outcome field on the
pipeline_stage_finish log line. Backward-compatible: every pre-existing
`with stage(...):` call site (without `as handle`) is unaffected --
mark_failed() is opt-in per call site.

See docs/PIPELINE_STAGES.md for the full stage-by-stage mapping to the
existing code this wraps, and docs/STAGE_EXTRACTION_CONTRACT.md for the
audit of which stages are pure-extracted vs. wrapped-in-place.
"""
from __future__ import annotations

import logging
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Iterator, Optional

from .logging_setup import log_event


@dataclass
class StageHandle:
    """Yielded by stage() so a wrapped block can report a non-exception
    failure. Calling mark_failed() never raises, never alters control
    flow -- the caller's own existing return/if-logic is what actually
    determines what happens next; this only changes what gets logged."""

    failed: bool = field(default=False, init=False)
    failure_reason: str = field(default="", init=False)

    def mark_failed(self, reason: str) -> None:
        self.failed = True
        self.failure_reason = reason


@contextmanager
def stage(logger: logging.Logger, name: str, **context: Any) -> Iterator[StageHandle]:
    """Logs pipeline_stage_start / pipeline_stage_finish around a block.

    On success with no mark_failed() call: pipeline_stage_finish with
    outcome="ok" and duration_ms.

    On success WITH a mark_failed(reason) call (Sprint 7): outcome="failed",
    failure_reason=reason, duration_ms -- the block's own return value/
    control flow is completely unaffected; this only changes the log line.

    On exception: pipeline_stage_finish with outcome="error",
    failure_reason=str(exc), and duration_ms -- then the exception is
    re-raised unmodified. Never swallows, never alters, never delays
    beyond the block's own natural execution time.
    """
    handle = StageHandle()
    log_event(logger, "pipeline_stage_start", stage=name, **context)
    start = time.perf_counter()
    try:
        yield handle
    except Exception as exc:
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        log_event(logger, "pipeline_stage_finish", stage=name, outcome="error",
                  failure_reason=str(exc), duration_ms=duration_ms, **context)
        raise
    else:
        duration_ms = round((time.perf_counter() - start) * 1000, 3)
        if handle.failed:
            log_event(logger, "pipeline_stage_finish", stage=name, outcome="failed",
                      failure_reason=handle.failure_reason, duration_ms=duration_ms, **context)
        else:
            log_event(logger, "pipeline_stage_finish", stage=name, outcome="ok",
                      duration_ms=duration_ms, **context)
