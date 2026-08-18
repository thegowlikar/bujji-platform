"""Runtime configuration — BUJJI Options OS v3, Engineering Series 54.

Purely declarative. Loading this configuration never makes a runtime
decision, never performs I/O, and never connects to anything. It is a
plain, frozen description of how the composition root should build the
object graph and which of the three modes the runtime should run in.

`qualification_fingerprint`/`replay_status`/`chain_valid`/`deterministic`
are a deliberate design point, disclosed in
docs/PRODUCTION_COMPOSITION_ROOT.md: `QualificationPolicy` (Series 47)
describes whether *this build* of the pipeline has already been
replay-qualified (Series 46) -- a build-time/release-time fact, not
something recomputed on every run. This config therefore carries the
result of the *last successful* Series 46 replay qualification,
supplied by whatever release process produced this configuration --
never recomputed live inside a runtime process.

A fourth mode, D0_REHEARSAL (Numeric Risk Governor Gate D0), was added
after the original three. It exercises the real Governor assess() via
`bujji.production_runtime.d0_rehearsal_runtime.run_d0_rehearsal_mode`
against a real CompositionRoot, with no dispatch call anywhere in its
own reachable code -- verified by an AST-based test, not just this
docstring. Falls under the SAME `broker_name == "fyers"` guard below as
every mode except PRODUCTION_READY: a live broker is never
constructible in D0_REHEARSAL, exactly as it already isn't in
READ_ONLY or SHADOW.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

RUNTIME_MODE_READ_ONLY = "READ_ONLY"
RUNTIME_MODE_SHADOW = "SHADOW"
RUNTIME_MODE_PRODUCTION_READY = "PRODUCTION_READY"
RUNTIME_MODE_D0_REHEARSAL = "D0_REHEARSAL"

ALL_RUNTIME_MODES = (
    RUNTIME_MODE_READ_ONLY, RUNTIME_MODE_SHADOW, RUNTIME_MODE_PRODUCTION_READY, RUNTIME_MODE_D0_REHEARSAL,
)

ALL_BROKER_NAMES = ("paper", "fyers")
ALL_MARKET_SOURCES = ("LIVE", "REPLAY")


class InvalidRuntimeConfig(ValueError):
    """Raised when a RuntimeConfig's own declared values are not
    recognized -- never silently coerced."""


@dataclass(frozen=True)
class RuntimeConfig:
    mode: str = RUNTIME_MODE_SHADOW
    broker_name: str = "paper"           # "paper" | "fyers" -- which production Broker to construct.
    broker_display_name: str = "FYERS"   # Name passed to the Broker Adapter (Series 40)'s translate().
    # Cross-check ONLY -- the authoritative lot size is read from the FYERS
    # instrument master at composition time (2026-08-18 fix; the 2026-07-19
    # audit found the master said NIFTY=65 while this default said 75, and the
    # default won). None means "no cross-check declared". There is
    # deliberately no way to force a lot size from config: pin a fixture
    # master via `instrument_master_directory` instead.
    lot_size: Optional[int] = None
    instrument_master_directory: str = "data/instrument_master"
    capital_policy_value: str = "SIMULATION"
    qualification_fingerprint: str = "RFP-0000000000000000"
    replay_status: str = "PASSED"
    chain_valid: bool = True
    deterministic: bool = True
    session_ttl_seconds: int = 28800
    allow_authorized_with_warnings: bool = True
    allow_pause_resume: bool = True
    policy_version: str = "1.0.0"
    journal_directory: str = "data/production"
    market_source: str = "REPLAY"        # "LIVE" | "REPLAY" -- descriptive only, never a hidden decision.
    logging_level: str = "INFO"

    def __post_init__(self) -> None:
        if self.mode not in ALL_RUNTIME_MODES:
            raise InvalidRuntimeConfig(f"Unrecognized mode: {self.mode!r}")
        if self.broker_name not in ALL_BROKER_NAMES:
            raise InvalidRuntimeConfig(f"Unrecognized broker_name: {self.broker_name!r}")
        if self.market_source not in ALL_MARKET_SOURCES:
            raise InvalidRuntimeConfig(f"Unrecognized market_source: {self.market_source!r}")
        if self.lot_size is not None and self.lot_size <= 0:
            raise InvalidRuntimeConfig(f"lot_size cross-check must be positive: {self.lot_size!r}")
        # P1-1 fix (TODO.md): a live-capable broker must never be
        # constructible outside Mode 3. Before this check, `RuntimeConfig(
        # mode="SHADOW", broker_name="fyers")` constructed successfully and
        # produced a real, unguarded FyersBroker with a live place_order --
        # confirmed by direct execution, not a hypothetical. This is the
        # primary fix layer (reject at config construction, before the
        # composition root ever runs); see composition_root.py's
        # `_build_broker` for the defense-in-depth second layer.
        if self.broker_name == "fyers" and self.mode != RUNTIME_MODE_PRODUCTION_READY:
            raise InvalidRuntimeConfig(
                f"broker_name='fyers' is only permitted in mode="
                f"{RUNTIME_MODE_PRODUCTION_READY!r} (got mode={self.mode!r}). "
                f"Outside Mode 3, use broker_name='paper' -- a live broker "
                f"must never be constructible in READ_ONLY or SHADOW mode."
            )


def load_config(values: Dict[str, Any]) -> RuntimeConfig:
    """Build a RuntimeConfig from a plain dict -- a pure field mapping,
    never a runtime decision, never I/O. Unrecognized keys raise a
    TypeError from the dataclass constructor itself (never silently
    ignored, which would hide a configuration typo); unrecognized
    values for `mode`/`broker_name`/`market_source` raise
    InvalidRuntimeConfig via `__post_init__` above.
    """
    return RuntimeConfig(**values)
