"""D0 mode integration — BUJJI Options OS v3, Numeric Risk Governor
Gate D0, production_runtime wiring.

The ONE new integration point between a real `CompositionRoot` and the
already-audited `d0_rehearsal_runtime.run_d0_rehearsal`. Deliberately a
SEPARATE file from `runtime.py`: `runtime.py` already legitimately
imports `runtime_execution_engine`/`broker_adapter_engine` for its
existing three modes' real dispatch logic, so a blanket "this module
never imports dispatch-capable code" test would not be meaningful
there. This file has NO such legitimate need -- it only validates the
mode and delegates -- so the same structural, AST-verified guarantee
`d0_rehearsal_runtime.py` itself already proves (zero import of
`bujji.runtime_execution`/`bujji.broker`/`bujji.broker_adapter`/
`bujji.execution`) applies here too, checked independently by this
file's own test.

`runtime.py`, `composition_root.py`, and `startup.py` are all
UNTOUCHED by this change -- confirmed by diff, not just this claim.
`d0_rehearsal_runtime.py` is also untouched; this file only calls its
existing, already-tested public function.
"""
from __future__ import annotations

from datetime import datetime
from typing import Callable

from .composition_root import CompositionRoot
from .config import RUNTIME_MODE_D0_REHEARSAL
from .d0_rehearsal_runtime import D0RehearsalInput, D0RehearsalResult, run_d0_rehearsal

Clock = Callable[[], datetime]


class WrongRuntimeModeError(ValueError):
    """Raised when run_d0_rehearsal_mode() is called against a
    CompositionRoot whose config.mode is not D0_REHEARSAL -- never
    silently proceeds under the wrong mode's assumptions."""


def run_d0_rehearsal_mode(
    root: CompositionRoot,
    rehearsal_input: D0RehearsalInput,
    clock: Clock,
) -> D0RehearsalResult:
    """The sole production_runtime entry point for Gate D0. Validates
    the CompositionRoot was built under RUNTIME_MODE_D0_REHEARSAL, then
    delegates entirely to run_d0_rehearsal() -- this function never
    reads root.broker, root.execution_engine, root.execution_adapter,
    or any other dispatch-capable field; it only checks root.config.mode
    (a plain string) before delegating."""
    if root.config.mode != RUNTIME_MODE_D0_REHEARSAL:
        raise WrongRuntimeModeError(
            f"run_d0_rehearsal_mode() requires a CompositionRoot built under "
            f"mode={RUNTIME_MODE_D0_REHEARSAL!r}, got mode={root.config.mode!r}"
        )
    return run_d0_rehearsal(rehearsal_input, clock=clock)
